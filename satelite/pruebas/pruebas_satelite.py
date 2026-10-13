#!/usr/bin/env python3
"""Pruebas automaticas del satelite (etapa B del Paso 2), sin red real.

Se ejecutan con el Python del entorno de uv:

    uv run python pruebas/pruebas_satelite.py

Cubren lo que el prompt exige comprobar sin depender de la VM ni de PipeWire:
descarte por antiguedad de satelite/decir (con la excepcion de recordatorio y
chat), tope de 8 s, tecla ignorada mientras el asistente habla, y los errores del
WebSocket (mas la falta de conexion).
"""

import asyncio
import os
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import satelite  # noqa: E402
from satelite import (  # noqa: E402
    Config, Frase, Nucleo, RegistroLatencias, ServidorTecla, aceptar_por_antiguedad,
    reloj_ms, MENSAJE_REPETIR, MENSAJE_SIN_CONEXION, TRAMA_BYTES,
)

# Todos los codigos de error reales que puede mandar servidor_voz.py, mas el
# generico "interno" que el prompt pide tratar igual.
CODIGOS_ERROR = [
    "token_invalido", "sin_inicio", "formato_audio", "frase_larga",
    "inactividad", "modo_chat_no_disponible", "modo_desconocido",
    "mensaje_invalido", "frase_en_curso", "interno",
]


class FakeWS:
    def __init__(self, listo=True):
        self.listo = listo
        self.json = []
        self.frames = []
        self._aviso_conexion_dado = False

    def enviar_json(self, objeto):
        if not self.listo:
            raise ConnectionError("no listo")
        self.json.append(objeto)

    def enviar_bytes(self, datos):
        if not self.listo:
            raise ConnectionError("no listo")
        self.frames.append(datos)

    def cuantos(self, tipo):
        return sum(1 for o in self.json if o.get("tipo") == tipo)


class FakeRep:
    def __init__(self):
        self.dichos = []

    async def decir(self, texto):
        self.dichos.append(texto)
        return reloj_ms()


class FakeRepSintetizaYReproduce:
    """Replica la salida real: el hook de sintesis se dispara antes de reproducir.

    En salida real Piper termina de sintetizar (marca t_sonido emitida via
    al_sintesis) y DESPUES pw-play reproduce el audio; decir solo vuelve al
    terminar la reproduccion. Esta falsa reproduce ese orden.
    """

    def __init__(self, duracion=0.2):
        self.dichos = []
        self.duracion = duracion
        self.al_sintesis = None

    async def decir(self, texto):
        self.dichos.append(texto)
        t_sonido = reloj_ms()
        if self.al_sintesis is not None:
            self.al_sintesis(t_sonido)
        await asyncio.sleep(self.duracion)
        return t_sonido


class FuenteQuieta:
    """No entrega nada: sirve para que la captura se quede esperando y se cancele."""

    async def tramas(self):
        await asyncio.sleep(3600)
        yield b""  # inalcanzable


class FuenteInfinita:
    async def tramas(self):
        while True:
            yield b"\x00" * TRAMA_BYTES


class Base(unittest.IsolatedAsyncioTestCase):
    def hacer_nucleo(self, ws=None, fuente=None, repes=None):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        cfg = Config(altura_max_s=8.0)
        self.rep = repes if repes is not None else FakeRep()
        self.reg = RegistroLatencias(self.tmp.name)
        loop = asyncio.get_running_loop()
        nucleo = Nucleo(cfg, self.rep, self.reg, loop)
        nucleo.ws = ws if ws is not None else FakeWS()
        nucleo.fuente = fuente if fuente is not None else FuenteQuieta()
        self.nucleo = nucleo
        self.addCleanup(lambda: os.path.exists(self.tmp.name)
                        and os.unlink(self.tmp.name))
        return nucleo

    async def esperar(self, condicion, timeout=3.0):
        fin = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < fin:
            if condicion():
                return True
            await asyncio.sleep(0.01)
        return False


class TestAntiguedad(unittest.TestCase):
    def test_respuesta_fresca(self):
        ahora = 1_700_000_000
        self.assertTrue(aceptar_por_antiguedad(
            {"tipo": "respuesta", "ts": ahora - 2}, ahora))

    def test_respuesta_vieja_se_descarta(self):
        ahora = 1_700_000_000
        self.assertFalse(aceptar_por_antiguedad(
            {"tipo": "respuesta", "ts": ahora - 60}, ahora))

    def test_respuesta_sin_ts_se_descarta(self):
        self.assertFalse(aceptar_por_antiguedad({"tipo": "respuesta"}, 1_700_000_000))

    def test_recordatorio_viejo_se_dice(self):
        ahora = 1_700_000_000
        self.assertTrue(aceptar_por_antiguedad(
            {"tipo": "recordatorio", "ts": ahora - 60}, ahora))

    def test_chat_viejo_se_dice(self):
        ahora = 1_700_000_000
        self.assertTrue(aceptar_por_antiguedad(
            {"tipo": "chat", "ts": ahora - 60}, ahora))

    def test_limite_exacto(self):
        ahora = 1_700_000_000
        self.assertTrue(aceptar_por_antiguedad(
            {"tipo": "respuesta", "ts": ahora - 10}, ahora))
        self.assertFalse(aceptar_por_antiguedad(
            {"tipo": "respuesta", "ts": ahora - 11}, ahora))


class TestIntervalos(unittest.TestCase):
    def test_calculo(self):
        f = Frase(frase_id="x", t_fin_voz=1000, t_final=1014, t_decir=1050,
                  t_sonido=1200)
        d = f.intervalos()
        self.assertEqual(d["fin_final_ms"], 14)
        self.assertEqual(d["fin_decir_ms"], 50)
        self.assertEqual(d["fin_sonido_ms"], 200)

    def test_sin_datos(self):
        f = Frase(frase_id="x", t_fin_voz=1000)
        d = f.intervalos()
        self.assertIsNone(d["fin_final_ms"])
        self.assertIsNone(d["fin_decir_ms"])
        self.assertIsNone(d["fin_sonido_ms"])


class TestNucleo(Base):
    async def test_tope_de_8s(self):
        nucleo = self.hacer_nucleo(fuente=FuenteInfinita())
        nucleo.abrir_frase()
        await self.esperar(lambda: nucleo.frase is None
                           and (nucleo.captura is None or nucleo.captura.done()))
        # 8 s / 0.1 s por trama = 80 tramas, y un solo fin.
        self.assertEqual(len(nucleo.ws.frames), 80)
        self.assertEqual(sum(1 for o in nucleo.ws.json if o.get("fin") is True), 1)
        self.assertEqual(nucleo.ws.cuantos("frase"), 1)

    async def test_tecla_ignorada_mientras_habla(self):
        nucleo = self.hacer_nucleo()
        nucleo.hablando = True
        nucleo.presionar()
        self.assertIsNone(nucleo.frase)
        self.assertEqual(nucleo.ws.cuantos("frase"), 0)
        self.assertEqual(len(nucleo.ws.frames), 0)

    async def test_sin_conexion_al_presionar(self):
        nucleo = self.hacer_nucleo(ws=FakeWS(listo=False))
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.presionar()
            await self.esperar(lambda: self.rep.dichos)
            self.assertEqual(self.rep.dichos, [MENSAJE_SIN_CONEXION])
            self.assertIsNone(nucleo.frase)
        finally:
            tarea.cancel()

    async def test_errores_del_websocket(self):
        for codigo in CODIGOS_ERROR:
            with self.subTest(codigo=codigo):
                await self._probar_codigo(codigo)

    async def _probar_codigo(self, codigo):
        self.rep = FakeRep()
        nucleo = self.hacer_nucleo()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.abrir_frase()
            nucleo.cerrar_frase()
            self.assertIsNotNone(nucleo.turno_pendiente)
            fid = nucleo.turno_pendiente.frase_id
            nucleo.al_recibir_error({"tipo": "error", "codigo": codigo,
                                     "frase_id": fid})
            await self.esperar(lambda: self.rep.dichos)
            self.assertEqual(self.rep.dichos, [MENSAJE_REPETIR])
        finally:
            tarea.cancel()

    async def test_respuesta_fresca_se_dice_y_se_mide(self):
        nucleo = self.hacer_nucleo()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.abrir_frase()
            nucleo.cerrar_frase()
            fid = nucleo.turno_pendiente.frase_id
            nucleo.al_recibir_final({"tipo": "final", "frase_id": fid,
                                     "texto": "prende la luz"})
            nucleo.al_decir({"texto": "Escuché: prende la luz",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            await self.esperar(lambda: self.rep.dichos)
            self.assertEqual(self.rep.dichos, ["Escuché: prende la luz"])
            # el registro tiene la frase con sus tres intervalos
            await self.esperar(lambda: os.path.getsize(self.tmp.name) > 0)
            with open(self.tmp.name, encoding="utf-8") as archivo:
                import json
                entrada = json.loads(archivo.readline())
            self.assertEqual(entrada["texto"], "prende la luz")
            self.assertIsNotNone(entrada["fin_final_ms"])
            self.assertIsNotNone(entrada["fin_decir_ms"])
        finally:
            tarea.cancel()

    async def test_final_tardio_tras_el_eco_no_se_pierde(self):
        """Regresion: el final del WebSocket puede llegar DESPUES del eco.

        El eco (satelite/decir) y el `final` viajan por caminos distintos y la red
        puede reordenarlos. Antes, al llegar el eco se liberaba el turno pendiente
        y el final llegado despues caia en <final para una frase desconocida>: la
        frase se escribia sin fin->final (se reporto en la laptop, 3 de 10 frases).
        """
        nucleo = self.hacer_nucleo()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.abrir_frase()
            nucleo.cerrar_frase()
            fid = nucleo.turno_pendiente.frase_id
            # El eco llega ANTES que el final: la condicion de carrera reportada.
            nucleo.al_decir({"texto": "Escuché: prende la luz",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            await self.esperar(lambda: self.rep.dichos)
            self.assertEqual(self.rep.dichos, ["Escuché: prende la luz"])
            nucleo.al_recibir_final({"tipo": "final", "frase_id": fid,
                                     "texto": "prende la luz"})
            await self.esperar(lambda: os.path.getsize(self.tmp.name) > 0)
            with open(self.tmp.name, encoding="utf-8") as archivo:
                import json
                entrada = json.loads(archivo.readline())
            self.assertEqual(entrada["texto"], "prende la luz")
            self.assertIsNotNone(entrada["fin_final_ms"])
            self.assertIsNotNone(entrada["fin_decir_ms"])
            self.assertIsNone(nucleo.turno_pendiente)
        finally:
            tarea.cancel()

    async def test_eco_duplicado_no_se_dice_dos_veces(self):
        """El eco entregado dos veces por el broker no debe hablar dos veces."""
        nucleo = self.hacer_nucleo()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.abrir_frase()
            nucleo.cerrar_frase()
            nucleo.al_decir({"texto": "Escuché: prende la luz",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            nucleo.al_decir({"texto": "Escuché: prende la luz",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            await self.esperar(lambda: self.rep.dichos)
            await asyncio.sleep(0.1)
            self.assertEqual(self.rep.dichos, ["Escuché: prende la luz"])
        finally:
            tarea.cancel()

    async def test_registro_se_cierra_al_sintetizar_no_al_reproducir(self):
        """El registro se cierra con fin->final Y fin->sonido en salida real.

        La marca t_sonido se conoce al TERMINAR DE SINTETIZAR, no al terminar de
        reproducir. Antes el guardado esperaba a que pw-play terminara (unos 2 s):
        si la cadena pasaba de la vigia de 3 s, esta escribia la frase a medias y
        se perdia fin->sonido (se reporto en la laptop, 3 de 10 frases).
        """
        nucleo = self.hacer_nucleo(repes=FakeRepSintetizaYReproduce())
        nucleo.segundos_sin_respuesta = 0.05
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.abrir_frase()
            nucleo.cerrar_frase()
            fid = nucleo.turno_pendiente.frase_id
            nucleo.al_decir({"texto": "Escuché: prende la luz de la sala",
                             "tipo": "respuesta",
                             "ts": int(reloj_ms() / 1000)})
            await self.esperar(lambda: self.rep.dichos)
            nucleo.al_recibir_final({"tipo": "final", "frase_id": fid,
                                     "texto": "prende la luz de la sala"})
            await self.esperar(lambda: os.path.getsize(self.tmp.name) > 0)
            with open(self.tmp.name, encoding="utf-8") as archivo:
                import json
                entrada = json.loads(archivo.readline())
            self.assertEqual(entrada["texto"], "prende la luz de la sala")
            self.assertIsNotNone(entrada["fin_final_ms"])
            self.assertIsNotNone(entrada["fin_sonido_ms"])
            self.assertIsNone(nucleo.turno_pendiente)
        finally:
            tarea.cancel()

    async def test_respuesta_vieja_se_descarta(self):
        nucleo = self.hacer_nucleo()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.al_decir({"texto": "vieja", "tipo": "respuesta",
                             "ts": int(reloj_ms() / 1000) - 60})
            await asyncio.sleep(0.1)
            self.assertEqual(self.rep.dichos, [])
        finally:
            tarea.cancel()


class TestTecla(unittest.IsolatedAsyncioTestCase):
    """El socket de la tecla traduce P/R (contrato de tecla.py) a presionar/soltar."""

    class Nodo:
        def __init__(self):
            self.veces = []

        def presionar(self):
            self.veces.append("presionar")

        def soltar(self):
            self.veces.append("soltar")

    async def _enviar(self, ruta, letra):
        reader, writer = await asyncio.open_unix_connection(ruta)
        writer.write(letra)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def test_p_r_y_p_s_abren_y_cierran(self):
        nodo = self.Nodo()
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "tecla.sock")
            servidor = await ServidorTecla(ruta, nodo).servir()
            try:
                for letra in (b"P", b"R", b"p", b"s"):
                    await self._enviar(ruta, letra)
                    await asyncio.sleep(0.02)
            finally:
                servidor.close()
        self.assertEqual(nodo.veces,
                         ["presionar", "soltar", "presionar", "soltar"])


class TestWav(unittest.TestCase):
    def test_mono_16k_pasa_tal_cual(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            ruta = tmp.name
        try:
            datos = b"\x01\x00" * 16000
            with wave.open(ruta, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(datos)
            salida = satelite._leer_wav_16k(ruta)
            self.assertEqual(salida, datos)
        finally:
            os.unlink(ruta)

    def test_estereo_22050_a_mono_16k(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            ruta = tmp.name
        try:
            n = 22050
            with wave.open(ruta, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b"\x10\x00\x10\x00" * n)
            salida = satelite._leer_wav_16k(ruta)
            # ~1 s a 16 kHz mono, 2 bytes por muestra
            self.assertAlmostEqual(len(salida), 32000, delta=8)
        finally:
            os.unlink(ruta)


class TestPiperPersistente(unittest.IsolatedAsyncioTestCase):
    """El Piper persistente devuelve un WAV por frase por stdout.

    Si las fronteras entre frases se leen mal, el audio de una se mezcla con el de
    la siguiente; por eso se fija que `_leer_wav` consuma exactamente un WAV.
    """

    @staticmethod
    def _wav_de(pcm):
        import io
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(pcm)
        return buffer.getvalue()

    def test_el_pcm_se_extrae_del_chunk_data(self):
        pcm = b"\x11\x00" * 32
        self.assertEqual(satelite._pcm_de_wav(self._wav_de(pcm)), pcm)

    async def test_lee_un_wav_y_deja_intacto_el_siguiente(self):
        primero = self._wav_de(b"\x01\x00" * 8)
        segundo = self._wav_de(b"\x02\x00" * 16)
        flujo = asyncio.StreamReader()
        flujo.feed_data(primero + segundo)
        flujo.feed_eof()
        self.assertEqual(await satelite._leer_wav(flujo), primero)
        self.assertEqual(await satelite._leer_wav(flujo), segundo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
