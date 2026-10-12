#!/usr/bin/env python3
"""Satelite de voz (etapa B del Paso 2) para la laptop.

Hace cuatro cosas y solo cuatro: escuchar por el microfono mientras se mantiene
una tecla, mandar el audio al servicio de Vosk por WebSocket, recibir la respuesta
por `casa/sala/satelite/decir` y decirla en voz alta con Piper. Fuera de esta
etapa (a proposito): Silero VAD, cancelacion de eco, palabra de activacion, puente
de Mosquitto y chat.

Decisiones que se ven en este archivo:

- **La tecla marca el fin de la frase.** No hay VAD todavia: al soltar la tecla se
  manda `{"fin":true}`. El tope de 8 s cierra la frase sola si el usuario sigue
  apretando. Mientras Piper habla, la tecla se ignora (P14).

- **El audio no toca el disco.** Se captura con `pw-record` eligiendo el nodo por
  NOMBRE (`--target`), sale PCM crudo s16le 16 kHz mono por su salida estandar y
  se reenvia en tramas de 3200 B. Piper corre PERSISTENTE (el modelo se carga una
  sola vez), recibe los textos por `stdin` —una linea por frase— y devuelve un WAV
  por frase por `stdout`; el PCM de ese WAV va directo a `pw-play` por una tuberia
  (o se descarta con `--salida-nula`).

- **Solo se dice lo que llega por MQTT.** El texto `final` del WebSocket NO se
  pronuncia nunca: sirve para cerrar la frase y medir. Lo que se oye es
  `casa/sala/satelite/decir` (el eco del orquestador, y mas adelante los avisos).

- **Las marcas son de UN solo reloj.** `tiempo.time()*1000` en este proceso. Nunca
  se restan marcas de relojes distintos: fin de voz, `fin` enviado, `final`
  recibido, `decir` recibido y arranque del sonido. Con ellas se calcula
  fin->final, fin->decir y fin->sonido, que es lo que pide el presupuesto.

- **La configuracion vive fuera del repositorio**, en
  `~/.config/asistente-satelite/config.toml` (permisos 600). El token y la
  contrasena se pueden poner ahi o, mejor, apuntar a un archivo con
  `token_archivo` / `password_archivo`.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import logging
import os
import signal
import struct
import sys
import time
import wave
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

# --- Constantes del contrato ------------------------------------------------

TASA = 16000                 # Hz de captura y del contrato.
TRAMA_BYTES = 3200           # 100 ms de PCM s16le mono 16 kHz.
TRAMAS_POR_SEGUNDO = 10
SEGUNDOS_SIN_RESPUESTA = 3   # Tras `fin`, si no llega decir, se registra.
ANTIGUEDAD_S = 10            # Regla de antiguedad del contrato.
# Tipos de satelite/decir que se dicen aunque lleguen tarde (P43).
TIPOS_TARDIOS = frozenset({"recordatorio", "chat"})

TOPICO_ESTADO_DEF = "casa/sala/satelite/estado"
TOPICO_DECIR_DEF = "casa/sala/satelite/decir"

MENSAJE_REPETIR = "No te escuché bien, ¿puedes repetir?"
MENSAJE_SIN_CONEXION = "No tengo conexión con el servidor"

registro = logging.getLogger("satelite")


def reloj_ms() -> int:
    """Epoch en milisegundos con el reloj del satelite."""
    return int(time.time() * 1000)


# --- Reglas puras (faciles de probar) ---------------------------------------

def aceptar_por_antiguedad(carga: dict, ahora_s: int) -> bool:
    """¿El mensaje de satelite/decir se puede decir?

    Descarta lo que no trae `ts` o lo que trae un `ts` de mas de 10 s, salvo los
    tipos `recordatorio` y `chat`, que se dicen aunque lleguen tarde (P43).
    """
    if not isinstance(carga, dict):
        return False
    if carga.get("tipo") in TIPOS_TARDIOS:
        return True
    ts = carga.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return False
    return abs(ahora_s - int(ts)) <= ANTIGUEDAD_S


def _leer_wav_16k(ruta: str) -> bytes:
    """Devuelve PCM s16le mono 16 kHz del WAV, remuestreando si hace falta.

    Solo se usa en el modo de prueba `--fuente-archivo`; la captura real ya sale a
    16 kHz de `pw-record`. El remuestreo es lineal y en Python puro: es audio de
    prueba corto y no vale la pena una dependencia (numpy) solo para esto.
    """
    with wave.open(ruta, "rb") as w:
        canales = w.getnchannels()
        ancho = w.getsampwidth()
        frecuencia = w.getframerate()
        datos = w.readframes(w.getnframes())
    if ancho != 2:
        raise ValueError("se esperaba PCM de 16 bits (ancho %d)" % ancho)

    muestras = array.array("h")
    muestras.frombytes(datos)
    if sys.byteorder == "big":
        muestras.byteswap()

    if canales == 2:
        monofonicas = array.array("h", bytes(2 * (len(muestras) // 2)))
        for i in range(len(monofonicas)):
            monofonicas[i] = (muestras[2 * i] + muestras[2 * i + 1]) // 2
        muestras = monofonicas
    elif canales != 1:
        raise ValueError("se esperaba 1 o 2 canales (hay %d)" % canales)

    if frecuencia != TASA:
        n = int(round(len(muestras) * TASA / frecuencia))
        salida = array.array("h", bytes(2 * n))
        for i in range(n):
            pos = i * frecuencia / TASA
            i0 = int(pos)
            frac = pos - i0
            if i0 + 1 < len(muestras):
                salida[i] = int(muestras[i0] * (1 - frac) + muestras[i0 + 1] * frac)
            else:
                salida[i] = muestras[i0]
        muestras = salida

    if sys.byteorder == "big":
        muestras.byteswap()
    return muestras.tobytes()


# --- Configuracion -----------------------------------------------------------

@dataclass
class Config:
    ws_url: str = "ws://127.0.0.1:2700"
    token: str = ""
    mqtt_host: str = "127.0.0.1"
    mqtt_puerto: int = 1883
    mqtt_usuario: str = "satelite"
    mqtt_password: str = ""
    client_id: str = "satelite-sala"
    topico_estado: str = TOPICO_ESTADO_DEF
    topico_decir: str = TOPICO_DECIR_DEF
    entrada_nodo: str = ""
    salida_nodo: str = ""
    latencia_captura: str = "20ms"
    altura_max_s: float = 8.0
    piper: str = ""
    voz: str = ""
    registro_ruta: str = ""
    socket_ruta: str = ""
    precargar_piper: bool = False


def _expandir(ruta: str) -> str:
    return os.path.expanduser(os.path.expandvars(ruta))


def _leer_secreto(ruta: str) -> str:
    if not ruta:
        return ""
    with open(_expandir(ruta), encoding="utf-8") as archivo:
        return archivo.read().strip()


def _ruta_estado_defecto() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "asistente-satelite", "latencias.jsonl")


def _ruta_socket_defecto() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, "asistente-satelite.sock")


def cargar_config(ruta: str) -> Config:
    with open(_expandir(ruta), "rb") as archivo:
        doc = __import__("tomllib").load(archivo)

    servidor = doc.get("servidor", {})
    mq = doc.get("mqtt", {})
    audio = doc.get("audio", {})
    rutas = doc.get("rutas", {})
    tecla = doc.get("tecla", {})

    cfg = Config()
    cfg.ws_url = servidor.get("ws_url", cfg.ws_url)
    cfg.token = servidor.get("token") or _leer_secreto(servidor.get("token_archivo", ""))
    cfg.mqtt_host = mq.get("host", cfg.mqtt_host)
    cfg.mqtt_puerto = int(mq.get("puerto", cfg.mqtt_puerto))
    cfg.mqtt_usuario = mq.get("usuario", cfg.mqtt_usuario)
    cfg.mqtt_password = mq.get("password") or _leer_secreto(mq.get("password_archivo", ""))
    cfg.client_id = mq.get("client_id", cfg.client_id)
    cfg.topico_estado = mq.get("topico_estado", cfg.topico_estado)
    cfg.topico_decir = mq.get("topico_decir", cfg.topico_decir)
    cfg.entrada_nodo = audio.get("entrada_nodo", cfg.entrada_nodo)
    cfg.salida_nodo = audio.get("salida_nodo", cfg.salida_nodo)
    cfg.latencia_captura = audio.get("latencia_captura", cfg.latencia_captura)
    cfg.altura_max_s = float(audio.get("altura_max_s", cfg.altura_max_s))
    cfg.piper = rutas.get("piper", cfg.piper)
    cfg.voz = rutas.get("voz", cfg.voz)
    cfg.registro_ruta = rutas.get("registro", _ruta_estado_defecto())
    cfg.socket_ruta = tecla.get("socket", _ruta_socket_defecto())
    cfg.precargar_piper = bool(rutas.get("precargar_piper", cfg.precargar_piper))
    return cfg


# --- Registro de latencias ---------------------------------------------------

class RegistroLatencias:
    """Escribe una linea JSON por frase, sin audio, en un archivo de estado."""

    def __init__(self, ruta: str):
        self.ruta = _expandir(ruta)
        carpeta = os.path.dirname(self.ruta)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)

    def escribir(self, entrada: dict) -> None:
        with open(self.ruta, "a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(entrada, ensure_ascii=False) + "\n")


@dataclass
class Frase:
    frase_id: str
    origen: str = "tecla"
    t_fin_voz: int | None = None
    t_fin_enviado: int | None = None
    t_final: int | None = None
    t_decir: int | None = None
    t_sonido: int | None = None
    t_final_enviado: int | None = None
    texto: str = ""
    modo: str = "comandos"
    error: str | None = None
    sin_respuesta: bool = False
    escrito: bool = False
    vigia: asyncio.Task | None = None

    def intervalos(self) -> dict:
        def resta(a, b):
            if a is None or b is None:
                return None
            return a - b
        return {
            "fin_final_ms": resta(self.t_final, self.t_fin_voz),
            "fin_decir_ms": resta(self.t_decir, self.t_fin_voz),
            "fin_sonido_ms": resta(self.t_sonido, self.t_fin_voz),
        }


# --- Fuentes de audio --------------------------------------------------------

class FuentePipeWire:
    """Captura con `pw-record` eligiendo el nodo por nombre, PCM crudo a stdout."""

    def __init__(self, nodo: str, latencia: str):
        self.nodo = nodo
        self.latencia = latencia

    async def tramas(self):
        orden = ["pw-record", "--rate", str(TASA), "--channels", "1",
                 "--format", "s16", "--raw", "--latency", self.latencia]
        if self.nodo:
            orden += ["--target", self.nodo]
        orden.append("-")
        proceso = await asyncio.create_subprocess_exec(
            *orden, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            while True:
                trozo = await proceso.stdout.read(TRAMA_BYTES)
                if not trozo:
                    break
                if len(trozo) % 2:
                    trozo = trozo[:-1]
                if trozo:
                    yield trozo
        finally:
            if proceso.returncode is None:
                proceso.terminate()
                try:
                    await asyncio.wait_for(proceso.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proceso.kill()
                    await proceso.wait()


class FuenteArchivo:
    """Lee un WAV y lo entrega en tramas, a ritmo real (modo de prueba)."""

    def __init__(self, ruta: str, ritmo: float = 0.1):
        self.ruta = ruta
        self.ritmo = ritmo

    async def tramas(self):
        pcm = _leer_wav_16k(self.ruta)
        for i in range(0, len(pcm), TRAMA_BYTES):
            yield pcm[i:i + TRAMA_BYTES]
            if self.ritmo:
                await asyncio.sleep(self.ritmo)


# --- Reproductor (Piper persistente -> pw-play) -----------------------------

def _pcm_de_wav(wav: bytes) -> bytes:
    """Devuelve el PCM del chunk `data` de un WAV, ignorando el resto de chunks.

    Un WAV de Piper es RIFF/WAVE + `fmt ` + `data`, y el `data` empieza a 44
    bytes. Se recorre por chunks en lugar de saltar a 44 a ciegas: asi, el dia que
    Piper meta un chunk extra (un `LIST`, por ejemplo), su cabecera no se cuela en
    el audio.
    """
    i = 12
    while i + 8 <= len(wav):
        identificador = wav[i:i + 4]
        tamano = struct.unpack("<I", wav[i + 4:i + 8])[0]
        if identificador == b"data":
            return wav[i + 8:i + 8 + tamano]
        i += 8 + tamano + (tamano & 1)
    raise ValueError("el WAV de Piper no tiene chunk data")


async def _leer_wav(flujo) -> bytes:
    """Lee de `flujo` (el `stdout` de Piper) el WAV completo de una frase.

    Piper en modo `-f -` escribe un WAV por linea leida, uno detras de otro, sin
    separador propio. El campo de tamano del RIFF dice cuanto ocupa el WAV entero,
    asi que se lee exactamente eso y la frontera entre frases queda exacta: sin
    esto, la frase siguiente empezaria a leer bytes de la anterior.
    """
    cabecera = await flujo.readexactly(12)
    if cabecera[:4] != b"RIFF" or cabecera[8:12] != b"WAVE":
        raise ValueError("Piper no devolvio un WAV (%r)" % cabecera[:12])
    total = struct.unpack("<I", cabecera[4:8])[0] + 8
    datos = bytearray(cabecera)
    while len(datos) < total:
        trozo = await flujo.read(min(65536, total - len(datos)))
        if not trozo:
            raise EOFError("Piper cerro la salida a mitad del WAV")
        datos.extend(trozo)
    return bytes(datos)


class Reproductor:
    """Piper persistente: el modelo se carga UNA vez y atiende frase a frase.

    Antes se lanzaba un proceso `piper` por frase, que pagaba la carga del modelo
    (medio segundo) en cada una. Ahora el proceso queda vivo: recibe los textos
    por `stdin`, una linea por frase, y escribe un WAV por frase por `stdout`. De
    ese WAV sale el PCM crudo que va a `pw-play` (o que se descarta, con
    `salida_nula`). El modelo solo se carga al arrancar el proceso.
    """

    def __init__(self, cfg: Config, salida_nula: bool = False):
        self.cfg = cfg
        self.salida_nula = salida_nula
        self.piper = _expandir(cfg.piper)
        self.voz = _expandir(cfg.voz)
        self.rate = self._rate_de_voz()
        self.entorno = dict(os.environ)
        carpeta = os.path.dirname(self.piper)
        self.entorno["LD_LIBRARY_PATH"] = carpeta + os.pathsep + \
            self.entorno.get("LD_LIBRARY_PATH", "")
        self.espeak = os.path.join(carpeta, "espeak-ng-data")
        self._proceso = None

    def _rate_de_voz(self) -> int:
        if self.voz and os.path.exists(self.voz + ".json"):
            try:
                with open(self.voz + ".json", encoding="utf-8") as archivo:
                    return int(json.load(archivo)["audio"]["sample_rate"])
            except (OSError, ValueError, KeyError):
                pass
        return 22050

    def _orden(self) -> list:
        # `-f -` = WAV a stdout. Sin `--output_raw` a proposito: el WAV trae su
        # propio tamano y eso da una frontera limpia entre frases.
        orden = [self.piper, "--model", self.voz, "-f", "-"]
        if os.path.isdir(self.espeak):
            orden += ["--espeak_data", self.espeak]
        return orden

    async def iniciar(self) -> None:
        """Arranca Piper (con la carga del modelo) si no esta ya vivo."""
        if self._proceso is not None and self._proceso.returncode is None:
            return
        self._proceso = await asyncio.create_subprocess_exec(
            *self._orden(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=self.entorno)

    async def cerrar(self) -> None:
        """Cierra el proceso persistente. Sin esto quedaria huerfano al salir."""
        if self._proceso is None:
            return
        if self._proceso.stdin is not None:
            self._proceso.stdin.close()
        try:
            await asyncio.wait_for(self._proceso.wait(), timeout=5)
        except asyncio.TimeoutError:
            self._proceso.kill()
            await self._proceso.wait()
        self._proceso = None

    async def decir(self, texto: str) -> int:
        """Sintetiza y reproduce. Devuelve la marca de inicio del sonido (ms)."""
        await self.iniciar()
        proceso = self._proceso
        assert proceso is not None and proceso.stdin is not None
        proceso.stdin.write((texto + "\n").encode("utf-8"))
        await proceso.stdin.drain()
        wav = await _leer_wav(proceso.stdout)
        t_sonido = reloj_ms()
        if self.salida_nula:
            return t_sonido

        pcm = _pcm_de_wav(wav)
        reproductor = await asyncio.create_subprocess_exec(
            "pw-play", "--rate", str(self.rate), "--channels", "1",
            "--format", "s16", "--raw",
            *(["--target", self.cfg.salida_nodo] if self.cfg.salida_nodo else []),
            "-", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        assert reproductor.stdin is not None
        try:
            reproductor.stdin.write(pcm)
            await reproductor.stdin.drain()
        finally:
            reproductor.stdin.close()
        await reproductor.wait()
        return t_sonido


# --- WebSocket ---------------------------------------------------------------

class GestorWS:
    """Conexion persistente con reconexion de espera creciente.

    Un unico escritor consume una cola, para que `frase`, sus tramas y `fin` salgan
    SIEMPRE en orden. Si la conexion se cae a mitad de frase, la frase se descarta.
    """

    def __init__(self, cfg: Config, nucleo: "Nucleo", loop):
        self.cfg = cfg
        self.nucleo = nucleo
        self.loop = loop
        self.ws = None
        self.listo = False
        self._salida: asyncio.Queue = asyncio.Queue()
        self._tarea = None
        self._aviso_conexion_dado = False

    def iniciar(self) -> None:
        self._tarea = self.loop.create_task(self._bucle())

    async def _escritor(self) -> None:
        while True:
            carga = await self._salida.get()
            ws = self.ws
            if ws is None:
                continue
            try:
                await ws.send(carga)
            except (ConnectionClosed, OSError) as error:
                registro.warning("envio WS fallo: %s", error)

    def enviar_json(self, objeto: dict) -> None:
        if not self.listo:
            raise ConnectionError("WebSocket no listo")
        self._salida.put_nowait(json.dumps(objeto, ensure_ascii=False))

    def enviar_bytes(self, datos: bytes) -> None:
        if not self.listo:
            raise ConnectionError("WebSocket no listo")
        self._salida.put_nowait(bytes(datos))

    async def _bucle(self) -> None:
        espera = 1
        escritor = self.loop.create_task(self._escritor())
        try:
            while True:
                try:
                    self.ws = await connect(self.cfg.ws_url, max_size=1 << 20,
                                            open_timeout=10)
                    await self.ws.send(json.dumps({"tipo": "inicio",
                                                   "token": self.cfg.token}))
                    saludo = json.loads(await self.ws.recv())
                    if saludo.get("tipo") != "listo":
                        raise RuntimeError("saludo inesperado: %r" % saludo)
                    self.listo = True
                    self._aviso_conexion_dado = False
                    espera = 1
                    registro.info("WebSocket conectado y listo")
                    async for bruto in self.ws:
                        self._despachar(bruto)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001
                    registro.warning("WebSocket caido (%s); reintento en %ss",
                                     error, espera)
                finally:
                    self.listo = False
                    self.ws = None
                    self._vaciar_cola()
                    self.nucleo.descartar_frase_actual()
                await asyncio.sleep(espera)
                espera = min(espera * 2, 30)
        finally:
            escritor.cancel()

    def _vaciar_cola(self) -> None:
        while not self._salida.empty():
            try:
                self._salida.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _despachar(self, bruto) -> None:
        if isinstance(bruto, (bytes, bytearray)):
            return
        try:
            mensaje = json.loads(bruto)
        except (ValueError, TypeError):
            return
        tipo = mensaje.get("tipo")
        if tipo == "final":
            self.nucleo.al_recibir_final(mensaje)
        elif tipo == "error":
            self.nucleo.al_recibir_error(mensaje)


# --- MQTT --------------------------------------------------------------------

class PuenteMQTT:
    def __init__(self, cfg: Config, nucleo: "Nucleo", loop):
        self.cfg = cfg
        self.nucleo = nucleo
        self.loop = loop
        self.listo = asyncio.Event()
        self.cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self.cliente.username_pw_set(cfg.mqtt_usuario, cfg.mqtt_password)
        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        # Last Will: si el satelite se cae, el broker publica {"conectado":false}.
        self.cliente.will_set(
            cfg.topico_estado,
            json.dumps({"conectado": False}, separators=(",", ":")),
            qos=0, retain=True)
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_message = self._al_mensaje

    def iniciar(self) -> None:
        self.cliente.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_puerto,
                                   keepalive=60)
        self.cliente.loop_start()

    def _al_conectar(self, cliente, userdata, banderas, codigo, propiedades=None):
        if codigo.is_failure:
            registro.error("MQTT rechazado (rc=%s)", codigo)
            return
        registro.info("MQTT conectado como %s (client_id=%s)", self.cfg.mqtt_usuario,
                      self.cfg.client_id)
        cliente.publish(self.cfg.topico_estado,
                        json.dumps({"conectado": True}, separators=(",", ":")),
                        qos=0, retain=True)
        cliente.subscribe(self.cfg.topico_decir, qos=1)
        self.loop.call_soon_threadsafe(self.listo.set)

    def _al_mensaje(self, cliente, userdata, mensaje):
        try:
            carga = json.loads(mensaje.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            registro.warning("satelite/decir no es JSON; se ignora")
            return
        self.loop.call_soon_threadsafe(self.nucleo.al_decir, carga)

    def cerrar(self) -> None:
        try:
            self.cliente.publish(self.cfg.topico_estado,
                                 json.dumps({"conectado": False},
                                            separators=(",", ":")),
                                 qos=0, retain=True)
            self.cliente.disconnect()
            self.cliente.loop_stop()
        except Exception:  # noqa: BLE001
            pass


# --- El nucleo ---------------------------------------------------------------

@dataclass
class ItemHabla:
    texto: str
    tipo: str
    turno: Frase | None = None
    recibido_ms: int = 0


class Nucleo:
    def __init__(self, cfg: Config, reproductor: Reproductor,
                 registro_lat: RegistroLatencias, loop):
        self.cfg = cfg
        self.rep = reproductor
        self.reg = registro_lat
        self.loop = loop
        self.ws: GestorWS | None = None
        self.fuente = FuentePipeWire(cfg.entrada_nodo, cfg.latencia_captura)
        self.frase: Frase | None = None
        self.captura: asyncio.Task | None = None
        self.hablando = False
        self.turno_pendiente: Frase | None = None
        self.cola: asyncio.Queue = asyncio.Queue()
        self._contador = 0
        self.primer_turno_ms: int | None = None
        self._primer_turno_hecho = False

    # --- tecla ---
    def presionar(self) -> None:
        if self.hablando:
            registro.info("tecla ignorada: el asistente esta hablando (P14)")
            return
        if self.frase is not None:
            registro.info("tecla ignorada: ya hay una frase abierta")
            return
        if self.ws is None or not self.ws.listo:
            registro.warning("tecla con el WebSocket no listo")
            self._encolar_habla(MENSAJE_SIN_CONEXION, "local")
            return
        self.abrir_frase(origen="tecla")

    def soltar(self) -> None:
        if self.frase is not None:
            self.cerrar_frase(motivo="soltar")

    # --- ciclo de una frase ---
    def abrir_frase(self, origen: str = "tecla") -> Frase:
        self._contador += 1
        frase_id = "sat-%d-%03d" % (int(time.time()), self._contador)
        frase = Frase(frase_id=frase_id, origen=origen)
        self.frase = frase
        registro.info("frase %s abierta (%s)", frase_id, origen)
        try:
            self.ws.enviar_json({"tipo": "frase", "frase_id": frase_id,
                                 "modo": "comandos"})
        except ConnectionError as error:
            registro.error("no se pudo abrir la frase: %s", error)
            self.frase = None
            self._encolar_habla(MENSAJE_SIN_CONEXION, "local")
            return frase
        self.captura = self.loop.create_task(self._capturar(frase))
        return frase

    async def _capturar(self, frase: Frase) -> None:
        max_tramas = max(1, int(self.cfg.altura_max_s * TRAMAS_POR_SEGUNDO))
        enviadas = 0
        try:
            async for trama in self.fuente.tramas():
                if self.frase is not frase:
                    return
                if self.ws is None or not self.ws.listo:
                    return
                self.ws.enviar_bytes(trama)
                enviadas += 1
                if enviadas >= max_tramas:
                    registro.warning("frase %s: tope de %.1f s; se cierra sola",
                                     frase.frase_id, self.cfg.altura_max_s)
                    self.cerrar_frase(motivo="tope")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            registro.error("captura interrumpida: %s", error)
            if self.frase is frase:
                self.cerrar_frase(motivo="error_captura")
            return
        if self.frase is frase:
            self.cerrar_frase(motivo="fin_fuente")

    def cerrar_frase(self, motivo: str = "soltar") -> None:
        frase = self.frase
        if frase is None:
            return
        self.frase = None
        if self.captura is not None and not self.captura.done():
            self.captura.cancel()
        self.captura = None
        frase.t_fin_voz = reloj_ms()
        try:
            self.ws.enviar_json({"fin": True})
            frase.t_fin_enviado = reloj_ms()
        except ConnectionError as error:
            registro.error("no se pudo enviar fin: %s", error)
            frase.error = "sin_conexion"
            self._escribir_frase(frase)
            self._encolar_habla(MENSAJE_SIN_CONEXION, "local")
            return
        self.turno_pendiente = frase
        frase.vigia = self.loop.create_task(self._vigia_respuesta(frase))
        registro.info("frase %s: fin enviado (motivo=%s)", frase.frase_id, motivo)

    async def _vigia_respuesta(self, frase: Frase) -> None:
        await asyncio.sleep(SEGUNDOS_SIN_RESPUESTA)
        if frase.t_decir is None:
            frase.sin_respuesta = True
            registro.warning("frase %s: nada en satelite/decir en %d s",
                             frase.frase_id, SEGUNDOS_SIN_RESPUESTA)
            self._escribir_frase(frase)

    def descartar_frase_actual(self) -> None:
        frase = self.frase
        if frase is None:
            return
        self.frase = None
        if self.captura is not None and not self.captura.done():
            self.captura.cancel()
        self.captura = None
        frase.error = "conexion_caida"
        self._escribir_frase(frase)
        registro.warning("frase %s descartada por caida de conexion",
                         frase.frase_id)
        if self.ws is not None and not self.ws._aviso_conexion_dado:
            self.ws._aviso_conexion_dado = True
            self._encolar_habla(MENSAJE_SIN_CONEXION, "local")

    # --- respuesta del WebSocket ---
    def al_recibir_final(self, mensaje: dict) -> None:
        frase = self._frase_del_mensaje(mensaje)
        if frase is None:
            registro.warning("final para una frase desconocida: %s", mensaje)
            return
        frase.t_final = reloj_ms()
        frase.texto = mensaje.get("texto", "")
        final_ms = frase.t_final
        fin_voz = frase.t_fin_voz if frase.t_fin_voz is not None else final_ms
        registro.info("frase %s: final en %d ms -> %r", frase.frase_id,
                      final_ms - fin_voz, frase.texto)

    def al_recibir_error(self, mensaje: dict) -> None:
        codigo = mensaje.get("codigo", "?")
        frase = self._frase_del_mensaje(mensaje)
        registro.warning("error del WebSocket: %s (frase %s)", codigo,
                         frase.frase_id if frase else mensaje.get("frase_id"))
        if frase is not None:
            frase.error = codigo
            if self.turno_pendiente is frase:
                self.turno_pendiente = None
            self._escribir_frase(frase)
        self._encolar_habla(MENSAJE_REPETIR, "error")

    def _frase_del_mensaje(self, mensaje: dict) -> Frase | None:
        frase_id = mensaje.get("frase_id")
        if self.turno_pendiente is not None:
            if frase_id is None or self.turno_pendiente.frase_id == frase_id:
                return self.turno_pendiente
        return None

    # --- respuesta por MQTT ---
    def al_decir(self, carga: dict) -> None:
        ahora = int(time.time())
        if not aceptar_por_antiguedad(carga, ahora):
            registro.warning("satelite/decir descartado por antiguedad o sin ts: %s",
                             carga)
            return
        texto = carga.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            return
        tipo = carga.get("tipo", "")
        frase = None
        if tipo == "respuesta" and self.turno_pendiente is not None:
            frase = self.turno_pendiente
            frase.t_decir = reloj_ms()
            self.turno_pendiente = None
        self._encolar_habla(texto, tipo, turno=frase)

    # --- cola de voz ---
    def _encolar_habla(self, texto: str, tipo: str,
                       turno: Frase | None = None) -> None:
        self.cola.put_nowait(ItemHabla(texto=texto, tipo=tipo, turno=turno,
                                       recibido_ms=reloj_ms()))

    async def bucle_habla(self) -> None:
        while True:
            item = await self.cola.get()
            self.hablando = True
            t_sonido = None
            try:
                t_sonido = await self.rep.decir(item.texto)
            except Exception as error:  # noqa: BLE001
                registro.error("no se pudo hablar: %s", error)
            finally:
                self.hablando = False
            if item.turno is not None and t_sonido is not None:
                item.turno.t_sonido = t_sonido
                self._medir_primer_turno(item.turno)
                self._escribir_frase(item.turno)

    def _medir_primer_turno(self, frase: Frase) -> None:
        if self._primer_turno_hecho or frase.t_fin_voz is None:
            return
        self._primer_turno_hecho = True
        self.primer_turno_ms = frase.t_sonido - frase.t_fin_voz
        registro.info("P35 primer turno fin->sonido: %d ms", self.primer_turno_ms)
        if self.primer_turno_ms > 2500:
            registro.warning("P35: el primer turno paso de 2.5 s; conviene "
                             "precargar Piper (rutas.precargar_piper = true)")

    def _escribir_frase(self, frase: Frase) -> None:
        if frase.escrito or frase.t_fin_voz is None:
            return
        frase.escrito = True
        intervalos = frase.intervalos()
        entrada = {
            "frase_id": frase.frase_id,
            "origen": frase.origen,
            "texto": frase.texto,
            "error": frase.error,
            "sin_respuesta": frase.sin_respuesta,
            "t_fin_voz_ms": frase.t_fin_voz,
            "t_fin_enviado_ms": frase.t_fin_enviado,
            "t_final_ms": frase.t_final,
            "t_decir_ms": frase.t_decir,
            "t_sonido_ms": frase.t_sonido,
            **intervalos,
        }
        self.reg.escribir(entrada)


# --- Socket de la tecla ------------------------------------------------------

class ServidorTecla:
    """Escucha en un socket Unix local los avisos de la tecla de Hyprland."""

    def __init__(self, ruta: str, nucleo: Nucleo):
        self.ruta = _expandir(ruta)
        self.nucleo = nucleo

    async def servir(self):
        if os.path.exists(self.ruta):
            os.unlink(self.ruta)
        return await asyncio.start_unix_server(self._atender, path=self.ruta)

    async def _atender(self, reader, writer):
        try:
            datos = await reader.read(64)
            accion = datos.strip().lower()
            if accion.startswith(b"p"):
                self.nucleo.presionar()
            elif accion.startswith(b"s"):
                self.nucleo.soltar()
            writer.write(b"ok")
            await writer.drain()
        except Exception as error:  # noqa: BLE001
            registro.warning("socket de tecla: %s", error)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


# --- Programa ----------------------------------------------------------------

def configurar_log(nivel: str) -> None:
    niveles = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
               "WARNING": logging.WARNING, "AVISO": logging.WARNING,
               "ERROR": logging.ERROR}
    logging.basicConfig(
        level=niveles.get(nivel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-8s %(message)s",
        datefmt="%H:%M:%S")
    logging.getLogger("paho").setLevel(logging.WARNING)


async def ejecutar(args) -> int:
    cfg = cargar_config(args.config)
    if args.ws_url:
        cfg.ws_url = args.ws_url
    if args.client_id:
        cfg.client_id = args.client_id
    if args.registro:
        cfg.registro_ruta = args.registro
    configurar_log(args.log)

    if not cfg.token:
        registro.error("falta el token del satelite (servidor.token o token_archivo)")
        return 2

    loop = asyncio.get_running_loop()
    reglat = RegistroLatencias(cfg.registro_ruta)
    reproductor = Reproductor(cfg, salida_nula=args.salida_nula)
    if cfg.precargar_piper:
        registro.info("precargando Piper...")
        try:
            await reproductor.decir("hola")
        except Exception as error:  # noqa: BLE001
            registro.warning("no se pudo precargar Piper: %s", error)

    nucleo = Nucleo(cfg, reproductor, reglat, loop)
    ws = GestorWS(cfg, nucleo, loop)
    nucleo.ws = ws
    mqttc = PuenteMQTT(cfg, nucleo, loop)

    ws.iniciar()
    mqttc.iniciar()
    tarea_habla = loop.create_task(nucleo.bucle_habla())

    servidor_tecla = None
    if not args.fuente_archivo:
        servidor_tecla = await ServidorTecla(cfg.socket_ruta, nucleo).servir()
        registro.info("socket de la tecla en %s", cfg.socket_ruta)

    parando = asyncio.Event()
    for senal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(senal, parando.set)
        except NotImplementedError:
            pass

    if args.fuente_archivo:
        # Esperar a que el WebSocket autentique y el MQTT se suscriba: si no, el
        # eco de la primera frase podria publicarse antes de estar escuchando.
        fin_espera = loop.time() + 15
        while not ws.listo and loop.time() < fin_espera:
            await asyncio.sleep(0.05)
        if not ws.listo:
            registro.error("el WebSocket no quedo listo; se aborta el modo archivo")
            return 3
        await asyncio.wait_for(mqttc.listo.wait(), timeout=15)
        registro.info("modo archivo: %d WAV", len(args.fuente_archivo))
        for ruta in args.fuente_archivo:
            nucleo.fuente = FuenteArchivo(_expandir(ruta), ritmo=args.ritmo)
            frase = nucleo.abrir_frase(origen="archivo")
            while nucleo.frase is not None or \
                    (nucleo.captura is not None and not nucleo.captura.done()):
                await asyncio.sleep(0.02)
            # esperar a que la respuesta se diga (o el vigia cierre la frase)
            while not frase.escrito:
                await asyncio.sleep(0.02)
            while nucleo.hablando or not nucleo.cola.empty():
                await asyncio.sleep(0.02)
            registro.info("archivo %s -> frase %s", ruta, frase.frase_id)
        registro.info("fin del modo archivo")
        await asyncio.sleep(1.0)
        parando.set()

    try:
        await parando.wait()
    finally:
        registro.info("parando...")
        tarea_habla.cancel()
        if servidor_tecla is not None:
            servidor_tecla.close()
            await servidor_tecla.wait_closed()
            if os.path.exists(cfg.socket_ruta):
                os.unlink(cfg.socket_ruta)
        if ws._tarea is not None:
            ws._tarea.cancel()
        mqttc.cerrar()
        try:
            await reproductor.cerrar()
        except Exception:  # noqa: BLE001
            registro.warning("no se pudo cerrar Piper limpiamente", exc_info=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Satelite de voz (etapa B).")
    parser.add_argument("--config", required=True,
                        help="ruta del config.toml (fuera del repositorio)")
    parser.add_argument("--fuente-archivo", action="append", default=[],
                        metavar="WAV",
                        help="modo de prueba: usa este WAV como una frase; repetible")
    parser.add_argument("--ritmo", type=float, default=0.1,
                        help="segundos entre tramas en modo archivo (por defecto 0.1)")
    parser.add_argument("--salida-nula", action="store_true",
                        help="sintetiza con Piper pero no reproduce")
    parser.add_argument("--ws-url", default="", help="sobreescribe servidor.ws_url")
    parser.add_argument("--client-id", default="",
                        help="sobreescribe mqtt.client_id")
    parser.add_argument("--registro", default="",
                        help="sobreescribe la ruta del registro de latencias")
    parser.add_argument("--log", default="INFO")
    args = parser.parse_args()
    try:
        return asyncio.run(ejecutar(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
