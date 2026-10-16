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
import json
import os
import sys
import tempfile
import time
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import satelite  # noqa: E402
from satelite import (  # noqa: E402
    Config, Frase, Nucleo, PuenteMQTT, RegistroLatencias, ServidorTecla,
    aceptar_por_antiguedad, reloj_ms, topico_estado_de, MENSAJE_REPETIR,
    MENSAJE_SIN_CONEXION, TRAMA_BYTES,
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

    async def decir(self, texto, **_kw):
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

    async def decir(self, texto, **_kw):
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
            nucleo.al_decir({"texto": "Usted dijo: prende la luz.",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            await self.esperar(lambda: self.rep.dichos)
            self.assertEqual(self.rep.dichos, ["Usted dijo: prende la luz."])
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
            nucleo.al_decir({"texto": "Usted dijo: prende la luz.",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            await self.esperar(lambda: self.rep.dichos)
            self.assertEqual(self.rep.dichos, ["Usted dijo: prende la luz."])
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
            nucleo.al_decir({"texto": "Usted dijo: prende la luz.",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            nucleo.al_decir({"texto": "Usted dijo: prende la luz.",
                             "tipo": "respuesta", "ts": int(reloj_ms() / 1000)})
            await self.esperar(lambda: self.rep.dichos)
            await asyncio.sleep(0.1)
            self.assertEqual(self.rep.dichos, ["Usted dijo: prende la luz."])
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
            nucleo.al_decir({"texto": "Usted dijo: prende la luz de la sala.",
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


class TestOrdenPiper(unittest.TestCase):
    """`Reproductor._orden` pasa a Piper solo los parametros definidos."""

    def _orden(self, **parametros):
        cfg = Config()
        cfg.piper = "/no/existe/piper"
        cfg.voz = "/no/existe/voz.onnx"
        for nombre, valor in parametros.items():
            setattr(cfg, nombre, valor)
        return satelite.Reproductor(cfg, salida_nula=True)._orden()

    def test_sin_parametros_es_la_orden_de_siempre(self):
        self.assertEqual(self._orden(), ["/no/existe/piper", "--model",
                                         "/no/existe/voz.onnx", "-f", "-"])

    def test_solo_los_definidos(self):
        orden = self._orden(sentence_silence=0.3, noise_w=1.0)
        self.assertEqual(orden[5:], ["--sentence_silence", "0.3", "--noise_w", "1.0"])
        self.assertNotIn("--length_scale", orden)
        self.assertNotIn("--noise_scale", orden)

    def test_los_cuatro(self):
        orden = self._orden(length_scale=1.0, sentence_silence=0.3,
                            noise_scale=0.667, noise_w=1.0)
        self.assertEqual(orden[5:], ["--length_scale", "1.0",
                                     "--sentence_silence", "0.3",
                                     "--noise_scale", "0.667", "--noise_w", "1.0"])

    def test_cero_se_pasa_no_se_confunde_con_ausente(self):
        self.assertEqual(self._orden(noise_scale=0.0)[5:], ["--noise_scale", "0.0"])


class TestConfigVoz(unittest.TestCase):
    def _cargar(self, contenido):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False,
                                         encoding="utf-8") as archivo:
            archivo.write(contenido)
        try:
            return satelite.cargar_config(archivo.name)
        finally:
            os.unlink(archivo.name)

    def test_seccion_voz_completa(self):
        cfg = self._cargar('''[voz]
modelo = "/v/es_MX-claude-high.onnx"
length_scale = 1.0
sentence_silence = 0.3
noise_scale = 0.667
noise_w = 1
''')
        self.assertEqual(cfg.voz, "/v/es_MX-claude-high.onnx")
        self.assertEqual((cfg.length_scale, cfg.sentence_silence, cfg.noise_scale,
                          cfg.noise_w), (1.0, 0.3, 0.667, 1.0))

    def test_config_antiguo_sin_voz(self):
        cfg = self._cargar('''[rutas]
voz = "/v/es_MX-ald-medium.onnx"
''')
        self.assertEqual(cfg.voz, "/v/es_MX-ald-medium.onnx")
        self.assertEqual((cfg.length_scale, cfg.sentence_silence, cfg.noise_scale,
                          cfg.noise_w), (None, None, None, None))

    def test_plantilla_del_repo(self):
        ruta = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "config.example.toml")
        # Solo la seccion [voz]: el resto de la plantilla apunta a secretos que
        # aqui no existen.
        import tomllib
        with open(ruta, "rb") as archivo:
            voz = tomllib.load(archivo)["voz"]
        cfg = self._cargar("[voz]\n" + "".join(
            "%s = %r\n" % (clave, valor) for clave, valor in voz.items()
        ).replace("'", '"'))
        self.assertTrue(cfg.voz.endswith("/voces/es_MX-claude-high.onnx"))
        self.assertEqual((cfg.length_scale, cfg.sentence_silence, cfg.noise_scale,
                          cfg.noise_w), (1.0, 0.3, 0.667, 1.0))


class TestTextos(unittest.TestCase):
    def test_frases_habladas(self):
        self.assertEqual(MENSAJE_REPETIR,
                         "Disculpe, no le escuché bien. ¿Me lo puede repetir?")
        self.assertEqual(MENSAJE_SIN_CONEXION,
                         "Disculpe, en este momento no tengo conexión. Por favor, "
                         "inténtelo de nuevo en unos minutos.")


class FakeRepLento:
    """Tarda en «decir»: sirve para que una alerta lo interrumpa."""

    def __init__(self, duracion=1.0):
        self.dichos = []
        self.cortados = []
        self.duracion = duracion

    async def decir(self, texto, **_kw):
        self.dichos.append(texto)
        try:
            await asyncio.sleep(self.duracion)
        except asyncio.CancelledError:
            self.cortados.append(texto)
            raise
        return reloj_ms()


def _ahora_s():
    return int(reloj_ms() / 1000)


class TestDosSatelites(unittest.IsolatedAsyncioTestCase):
    """Fase 2C: el contrato de satelite/decir con dos satelites."""

    def _satelite(self, client_id):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        cfg = Config(altura_max_s=8.0, client_id=client_id)
        rep = FakeRep()
        nucleo = Nucleo(cfg, rep, RegistroLatencias(tmp.name),
                        asyncio.get_running_loop())
        nucleo.ws = FakeWS()
        nucleo.fuente = FuenteQuieta()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        self.addCleanup(tarea.cancel)
        return nucleo, rep

    async def asyncSetUp(self):
        self.sala, self.rep_sala = self._satelite("satelite-sala")
        self.vm, self.rep_vm = self._satelite("satelite-vm")

    def _a_los_dos(self, carga):
        self.sala.al_decir(dict(carga))
        self.vm.al_decir(dict(carga))

    async def _pausa(self):
        await asyncio.sleep(0.1)

    async def test_frase_id_lleva_el_client_id(self):
        frase = self.vm.abrir_frase()
        self.assertRegex(frase.frase_id, r"^satelite-vm-\d+-1$")

    async def test_respuesta_la_dice_solo_el_emisor(self):
        self.vm.abrir_frase()
        self.vm.cerrar_frase()
        fid = self.vm.turno_pendiente.frase_id
        self._a_los_dos({"texto": "Usted dijo: hola.", "tipo": "respuesta",
                         "frase_id": fid, "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_vm.dichos, ["Usted dijo: hola."])
        self.assertEqual(self.rep_sala.dichos, [])

    async def test_respuesta_duplicada_con_frase_id_no_se_repite(self):
        self.vm.abrir_frase()
        self.vm.cerrar_frase()
        fid = self.vm.turno_pendiente.frase_id
        for _ in range(2):
            self.vm.al_decir({"texto": "Usted dijo: hola.", "tipo": "respuesta",
                              "frase_id": fid, "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_vm.dichos, ["Usted dijo: hola."])

    async def test_respuesta_sin_frase_id_la_dicen_los_dos(self):
        with self.assertLogs("satelite", level="WARNING") as aviso:
            self._a_los_dos({"texto": "Usted dijo: hola.", "tipo": "respuesta",
                             "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_sala.dichos, ["Usted dijo: hola."])
        self.assertEqual(self.rep_vm.dichos, ["Usted dijo: hola."])
        self.assertTrue(any("sin frase_id" in linea for linea in aviso.output))

    async def test_alerta_la_dicen_los_dos(self):
        self._a_los_dos({"texto": "Atención: gas.", "tipo": "alerta",
                         "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_sala.dichos, ["Atención: gas."])
        self.assertEqual(self.rep_vm.dichos, ["Atención: gas."])

    async def test_aviso_lo_dicen_los_dos(self):
        self._a_los_dos({"texto": "Su familia ya recibió...", "tipo": "aviso",
                         "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(len(self.rep_sala.dichos), 1)
        self.assertEqual(len(self.rep_vm.dichos), 1)

    async def test_destino_lo_dice_solo_uno(self):
        self._a_los_dos({"texto": "Atención: gas.", "tipo": "alerta",
                         "destino": "satelite-vm", "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_vm.dichos, ["Atención: gas."])
        self.assertEqual(self.rep_sala.dichos, [])

    async def test_destino_filtra_tambien_una_respuesta(self):
        self._a_los_dos({"texto": "hola", "tipo": "respuesta",
                         "destino": "satelite-sala", "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_vm.dichos, [])

    async def test_recordatorio_lo_dicen_los_dos(self):
        self._a_los_dos({"texto": "Le recuerdo: regar. Repito: regar.",
                         "tipo": "recordatorio", "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_sala.dichos,
                         ["Le recuerdo: regar. Repito: regar."])
        self.assertEqual(self.rep_vm.dichos,
                         ["Le recuerdo: regar. Repito: regar."])

    async def test_recordatorio_con_destino_lo_dice_solo_uno(self):
        self._a_los_dos({"texto": "Le recuerdo: regar. Repito: regar.",
                         "tipo": "recordatorio", "destino": "satelite-vm",
                         "ts": _ahora_s()})
        await self._pausa()
        self.assertEqual(self.rep_vm.dichos,
                         ["Le recuerdo: regar. Repito: regar."])
        self.assertEqual(self.rep_sala.dichos, [])


class _ClienteMQTTFalso:
    """Registra el orden de subscribe/publish, sin abrir red."""

    def __init__(self):
        self.orden = []
        self.publicados = []

    def subscribe(self, topico, qos=0):
        self.orden.append(("subscribe", topico))

    def publish(self, topico, carga, qos=0, retain=False):
        self.orden.append(("publish", topico))
        self.publicados.append((topico, carga, qos, retain))


class _LoopFalso:
    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


class _CodigoFalso:
    is_failure = False


class _CfgFalsa:
    client_id = "satelite-vm"
    mqtt_usuario = "satelite"
    topico_decir = "casa/sala/satelite/decir"
    topico_estado = None


class TestOrdenAlConectar(unittest.TestCase):
    """Regresion del fallo del 2026-10-15 (entrega tardia perdida).

    Al (re)conectar, el satelite DEBE suscribirse a satelite/decir ANTES de
    publicar su estado conectado. Si publica el estado primero, el orquestador
    responde al instante con un recordatorio pendiente y, como el satelite usa
    clean_session=True (sin cola de mensajes en el broker), ese primer mensaje
    se pierde. Esta prueba falla si se vuelve a invertir el orden.
    """

    def _puente_y_cliente(self):
        cfg = _CfgFalsa()
        puente = PuenteMQTT.__new__(PuenteMQTT)
        puente.cfg = cfg
        puente.nucleo = None
        puente.topico_estado = topico_estado_de(cfg)
        puente.loop = _LoopFalso()
        puente.listo = asyncio.Event()
        return puente, _ClienteMQTTFalso()

    def test_se_suscribe_a_decir_antes_de_publicar_el_estado(self):
        puente, cliente = self._puente_y_cliente()

        puente._al_conectar(cliente, None, None, _CodigoFalso())

        # La primera llamada es suscribirse a decir; la segunda, publicar estado.
        self.assertEqual(cliente.orden[0],
                         ("subscribe", puente.cfg.topico_decir))
        self.assertEqual(cliente.orden[1],
                         ("publish", puente.topico_estado))
        indice_sub = cliente.orden.index(
            ("subscribe", puente.cfg.topico_decir))
        indice_pub = cliente.orden.index(
            ("publish", puente.topico_estado))
        self.assertLess(indice_sub, indice_pub,
                        "el satelite publico su estado antes de suscribirse a "
                        "satelite/decir: se perdera la entrega tardia")

        # El estado va como {"conectado": true} y retenido.
        topico, carga, _qos, retain = cliente.publicados[0]
        self.assertEqual(topico, puente.topico_estado)
        self.assertEqual(json.loads(carga), {"conectado": True})
        self.assertTrue(retain)

    def test_el_orden_malo_se_detecta(self):
        """Confirma que la comprobacion de orden caza la regresion.

        Con el orden invertido (el fallo de hoy: publicar estado y luego
        suscribirse) la asercion de `test_se_suscribe_a_decir_antes_de_publicar_
        el_estado` no se cumpliria; aqui se reproduce ese orden a mano.
        """
        puente, cliente = self._puente_y_cliente()
        cliente.publish(puente.topico_estado, '{"conectado":true}',
                        qos=0, retain=True)
        cliente.subscribe(puente.cfg.topico_decir, qos=1)
        with self.assertRaises(AssertionError):
            self.assertLess(
                cliente.orden.index(("subscribe", puente.cfg.topico_decir)),
                cliente.orden.index(("publish", puente.topico_estado)))


class TestAlertaPrioridad(Base):
    async def test_alerta_de_hace_30_s_se_dice_y_una_respuesta_no(self):
        nucleo = self.hacer_nucleo()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.al_decir({"texto": "vieja", "tipo": "respuesta",
                             "ts": _ahora_s() - 30})
            nucleo.al_decir({"texto": "Atención: gas.", "tipo": "alerta",
                             "ts": _ahora_s() - 30})
            await asyncio.sleep(0.1)
            self.assertEqual(self.rep.dichos, ["Atención: gas."])
        finally:
            tarea.cancel()

    async def test_alerta_de_hace_una_hora_no_se_dice(self):
        nucleo = self.hacer_nucleo()
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.al_decir({"texto": "Atención: gas.", "tipo": "alerta",
                             "ts": _ahora_s() - 3600})
            await asyncio.sleep(0.1)
            self.assertEqual(self.rep.dichos, [])
        finally:
            tarea.cancel()

    async def test_alerta_corta_una_respuesta_que_suena(self):
        rep = FakeRepLento(duracion=1.0)
        nucleo = self.hacer_nucleo(repes=rep)
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.al_decir({"texto": "respuesta larga", "tipo": "respuesta",
                             "ts": _ahora_s()})
            await self.esperar(lambda: rep.dichos)
            nucleo.al_decir({"texto": "Atención: gas.", "tipo": "alerta",
                             "ts": _ahora_s()})
            await self.esperar(lambda: len(rep.dichos) == 2)
            self.assertEqual(rep.cortados, ["respuesta larga"])
            self.assertEqual(rep.dichos, ["respuesta larga", "Atención: gas."])
        finally:
            tarea.cancel()

    async def test_alerta_pasa_delante_en_la_cola(self):
        rep = FakeRepLento(duracion=0.2)
        nucleo = self.hacer_nucleo(repes=rep)
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.al_decir({"texto": "aviso 1", "tipo": "aviso", "ts": _ahora_s()})
            await self.esperar(lambda: rep.dichos)
            nucleo.al_decir({"texto": "aviso 2", "tipo": "aviso", "ts": _ahora_s()})
            nucleo.al_decir({"texto": "gas", "tipo": "alerta", "ts": _ahora_s()})
            await self.esperar(lambda: len(rep.dichos) == 3, timeout=3)
            # «aviso 1» se corta, la alerta va antes que «aviso 2».
            self.assertEqual(rep.dichos, ["aviso 1", "gas", "aviso 2"])
        finally:
            tarea.cancel()

    async def test_una_alerta_no_corta_otra_alerta(self):
        rep = FakeRepLento(duracion=0.2)
        nucleo = self.hacer_nucleo(repes=rep)
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.al_decir({"texto": "gas 1", "tipo": "alerta", "ts": _ahora_s()})
            await self.esperar(lambda: rep.dichos)
            nucleo.al_decir({"texto": "gas 2", "tipo": "alerta", "ts": _ahora_s()})
            await self.esperar(lambda: len(rep.dichos) == 2, timeout=3)
            self.assertEqual(rep.cortados, [])
        finally:
            tarea.cancel()

    async def test_alerta_interrumpe_un_recordatorio_en_curso(self):
        rep = FakeRepLento(duracion=0.2)
        nucleo = self.hacer_nucleo(repes=rep)
        tarea = asyncio.get_running_loop().create_task(nucleo.bucle_habla())
        try:
            nucleo.al_decir({"texto": "Le recuerdo: agua. Repito: agua.",
                             "tipo": "recordatorio", "ts": _ahora_s()})
            await self.esperar(lambda: rep.dichos)
            nucleo.al_decir({"texto": "gas", "tipo": "alerta", "ts": _ahora_s()})
            await self.esperar(lambda: len(rep.dichos) == 2, timeout=3)
            self.assertEqual(rep.cortados, ["Le recuerdo: agua. Repito: agua."])
            self.assertEqual(rep.dichos,
                             ["Le recuerdo: agua. Repito: agua.", "gas"])
        finally:
            tarea.cancel()


class TestTopicoEstado(unittest.TestCase):
    def test_por_satelite(self):
        self.assertEqual(satelite.topico_estado_de(Config(client_id="satelite-vm")),
                         "casa/sala/satelite/satelite-vm/estado")

    def test_config_viejo_se_corrige(self):
        cfg = Config(client_id="satelite-sala",
                     topico_estado="casa/sala/satelite/estado")
        with self.assertLogs("satelite", level="WARNING"):
            self.assertEqual(satelite.topico_estado_de(cfg),
                             "casa/sala/satelite/satelite-sala/estado")

    def test_client_id_con_comodin_se_rechaza(self):
        with self.assertRaises(ValueError):
            satelite.topico_estado_de(Config(client_id="sat/+"))


def _wav(ruta, frecuencia=16000, canales=1, ancho=2, cuadros=1600):
    with wave.open(ruta, "wb") as w:
        w.setnchannels(canales)
        w.setsampwidth(ancho)
        w.setframerate(frecuencia)
        w.writeframes(b"\x01" * (ancho * canales * cuadros))


class TestEntradaArchivo(Base):
    def test_wav_correcto(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "bien.wav")
            _wav(ruta)
            self.assertEqual(len(satelite.leer_wav_estricto(ruta)), 3200)

    def test_rechazos_claros(self):
        with tempfile.TemporaryDirectory() as tmp:
            casos = (({"frecuencia": 22050}, "22050 Hz en vez de 16000 Hz"),
                     ({"canales": 2}, "2 canales en vez de 1"),
                     ({"ancho": 1}, "8 bits en vez de 16"))
            for parametros, mensaje in casos:
                ruta = os.path.join(tmp, "malo.wav")
                _wav(ruta, **parametros)
                with self.assertRaisesRegex(ValueError, mensaje):
                    satelite.leer_wav_estricto(ruta)
            ruta = os.path.join(tmp, "texto.wav")
            with open(ruta, "w") as archivo:
                archivo.write("no soy un wav")
            with self.assertRaisesRegex(ValueError, "no es un WAV PCM valido"):
                satelite.leer_wav_estricto(ruta)
            with self.assertRaisesRegex(ValueError, "no existe"):
                satelite.leer_wav_estricto(os.path.join(tmp, "no.wav"))
            with self.assertRaisesRegex(ValueError, "absoluta"):
                satelite.leer_wav_estricto("relativa.wav")

    async def test_socket_archivo_manda_la_frase(self):
        nucleo = self.hacer_nucleo()
        nucleo.cfg.fuente = "archivo"
        with tempfile.TemporaryDirectory() as tmp:
            ruta_wav = os.path.join(tmp, "frase.wav")
            _wav(ruta_wav, cuadros=16000)  # 1 s = 10 tramas
            ruta_sock = os.path.join(tmp, "s.sock")
            servidor = await ServidorTecla(ruta_sock, nucleo).servir()
            try:
                reader, writer = await asyncio.open_unix_connection(ruta_sock)
                writer.write(b"A " + ruta_wav.encode() + b"\n")
                await writer.drain()
                writer.write_eof()
                respuesta = (await reader.read()).decode()
                writer.close()
                self.assertRegex(respuesta, r"^ok satelite-sala-\d+-1$")
                await self.esperar(lambda: nucleo.turno_pendiente is not None)
                self.assertEqual(len(nucleo.ws.frames), 10)
                self.assertEqual(nucleo.ws.cuantos("frase"), 1)
                self.assertTrue(any(o.get("fin") for o in nucleo.ws.json))
            finally:
                servidor.close()

    async def test_socket_archivo_rechaza_y_lo_dice(self):
        nucleo = self.hacer_nucleo()
        with tempfile.TemporaryDirectory() as tmp:
            ruta_wav = os.path.join(tmp, "malo.wav")
            _wav(ruta_wav, canales=2)
            ruta_sock = os.path.join(tmp, "s.sock")
            servidor = await ServidorTecla(ruta_sock, nucleo).servir()
            try:
                reader, writer = await asyncio.open_unix_connection(ruta_sock)
                writer.write(b"A " + ruta_wav.encode() + b"\n")
                writer.write_eof()
                respuesta = (await reader.read()).decode()
                writer.close()
            finally:
                servidor.close()
        self.assertIn("error: el WAV no es PCM 16 kHz mono 16 bits", respuesta)
        self.assertEqual(nucleo.ws.cuantos("frase"), 0)

    async def test_tecla_ignorada_con_fuente_archivo(self):
        nucleo = self.hacer_nucleo()
        nucleo.cfg.fuente = "archivo"
        nucleo.presionar()
        self.assertIsNone(nucleo.frase)

    def test_tecla_py_archivo_contra_el_socket(self):
        """El cliente real (tecla.py) con un servidor de socket minimo."""
        import socket
        import subprocess
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            ruta_sock = os.path.join(tmp, "s.sock")
            recibido = []
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(ruta_sock)
            srv.listen(1)

            def atender():
                con, _ = srv.accept()
                datos = b""
                while True:
                    trozo = con.recv(4096)
                    if not trozo:
                        break
                    datos += trozo
                recibido.append(datos)
                con.sendall(b"ok satelite-vm-1-1")
                con.close()

            hilo = threading.Thread(target=atender)
            hilo.start()
            tecla = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "tecla.py")
            salida = subprocess.run(
                [sys.executable, tecla, "--socket", ruta_sock, "archivo", "x.wav"],
                capture_output=True, text=True, cwd=tmp, timeout=10)
            hilo.join(5)
            srv.close()
        self.assertEqual(salida.returncode, 0, salida.stderr)
        self.assertEqual(salida.stdout.strip(), "ok satelite-vm-1-1")
        self.assertEqual(recibido, [b"A " + os.path.join(tmp, "x.wav").encode()
                                    + b"\n"])


class TestSalidaArchivo(unittest.TestCase):
    def test_nombre_y_contenido(self):
        with tempfile.TemporaryDirectory() as tmp:
            ahora = time.mktime((2026, 10, 14, 1, 2, 3, 0, 0, -1))
            ruta = satelite.guardar_salida(tmp, b"RIFFdatos", "respuesta",
                                           "satelite-vm-1791-1", ahora=ahora)
            self.assertEqual(os.path.basename(ruta),
                             "20261014-010203_respuesta_satelite-vm-1791-1.wav")
            with open(ruta, "rb") as archivo:
                self.assertEqual(archivo.read(), b"RIFFdatos")
            ruta2 = satelite.guardar_salida(tmp, b"x", "alerta", None, ahora=ahora)
            self.assertEqual(os.path.basename(ruta2),
                             "20261014-010203_alerta_sistema.wav")
            ruta3 = satelite.guardar_salida(tmp, b"y", "alerta", None, ahora=ahora)
            self.assertEqual(os.path.basename(ruta3),
                             "20261014-010203_alerta_sistema-2.wav")
            self.assertFalse([n for n in os.listdir(tmp) if n.endswith(".parcial")])

    def test_conserva_solo_los_ultimos_50(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = time.mktime((2026, 10, 14, 0, 0, 0, 0, 0, -1))
            for i in range(55):
                ruta = satelite.guardar_salida(tmp, b"x", "aviso", "f-%d" % i,
                                               ahora=base + i)
                os.utime(ruta, ns=(int((base + i) * 1e9),) * 2)
            nombres = sorted(os.listdir(tmp))
            self.assertEqual(len(nombres), 50)
            self.assertNotIn("20261014-000000_aviso_f-0.wav", nombres)
            self.assertIn("20261014-000054_aviso_f-54.wav", nombres)

    def test_frase_id_raro_no_escapa_de_la_carpeta(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = satelite.guardar_salida(tmp, b"x", "respuesta", "../../etc/x")
            self.assertEqual(os.path.dirname(ruta), tmp)

    def test_config_de_la_vm(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False,
                                         encoding="utf-8") as archivo:
            archivo.write('[audio]\nfuente = "archivo"\n'
                          '[salida]\nmodo = "archivo"\ncarpeta = "/tmp/x"\n')
        try:
            cfg = satelite.cargar_config(archivo.name)
        finally:
            os.unlink(archivo.name)
        self.assertEqual((cfg.fuente, cfg.salida_modo, cfg.salida_carpeta,
                          cfg.salida_max), ("archivo", "archivo", "/tmp/x", 50))

    def test_config_sin_carpeta_se_rechaza(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False,
                                         encoding="utf-8") as archivo:
            archivo.write('[salida]\nmodo = "archivo"\n')
        try:
            with self.assertRaisesRegex(ValueError, "carpeta"):
                satelite.cargar_config(archivo.name)
        finally:
            os.unlink(archivo.name)

    def test_laptop_por_defecto_sin_cambios(self):
        cfg = Config()
        self.assertEqual((cfg.fuente, cfg.salida_modo), ("pipewire", "audio"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
