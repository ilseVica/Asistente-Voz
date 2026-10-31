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

- **Puede haber varios satelites** (Fase 2C). Cada uno tiene su `client_id`,
  publica su estado en `casa/sala/satelite/<client_id>/estado` y numera sus
  frases como `<client_id>-<epoch>-<n>`. En `satelite/decir`, una `respuesta` la
  dice solo el satelite que hizo la pregunta (por su `frase_id`); los avisos y las
  alertas los dicen todos, salvo que traigan `destino` (ver `Nucleo.al_decir`).

- **Sin microfono ni bocinas** (la instancia de la VM): con `[audio] fuente =
  "archivo"` las frases llegan como WAV por el socket (`asistente-tecla archivo
  <ruta.wav>`), y con `[salida] modo = "archivo"` lo que se dice se guarda como WAV
  en una carpeta en vez de sonar.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import collections
import json
import logging
import os
import re
import signal
import shutil
import struct
import sys
import time
import wave
import threading
import math
import tempfile
import subprocess
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

import textos

# --- Constantes del contrato ------------------------------------------------

TASA = 16000                 # Hz de captura y del contrato.
TRAMA_BYTES = 3200           # 100 ms de PCM s16le mono 16 kHz.
TRAMAS_POR_SEGUNDO = 10
SEGUNDOS_SIN_RESPUESTA = 3   # Tras `fin`, si no llega decir, se registra.
ANTIGUEDAD_S = 10            # Regla de antiguedad del contrato.
# Tipos de satelite/decir que se dicen aunque lleguen tarde (P43).
TIPOS_TARDIOS = frozenset({"recordatorio", "chat"})
# Las alertas habladas (gas, ayuda). NO son tardias: una alerta de hace una hora no
# se dice sola (para eso esta la repeticion al conectarse, que llega con `ts`
# nuevo). Pero su ventana es mas ancha que la de una respuesta, para que un
# desfase de reloj o un broker lento no se traguen un aviso de gas.
TIPO_ALERTA = "alerta"
ANTIGUEDAD_ALERTA_S = 60

# El estado es POR satelite. `{client_id}` se sustituye al conectar.
TOPICO_ESTADO_DEF = "casa/sala/satelite/{client_id}/estado"
# El topico de antes de la Fase 2C. Un config viejo que lo traiga escrito se
# corrige solo (la ACL ya no deja escribirlo).
TOPICO_ESTADO_VIEJO = "casa/sala/satelite/estado"
TOPICO_DECIR_DEF = "casa/sala/satelite/decir"
TOPICO_VIDEO_DEF = "casa/sala/satelite/{client_id}/video"

FUENTES = ("pipewire", "archivo")
MODOS_SALIDA = ("audio", "archivo")
MAX_SALIDAS = 50              # WAV que se conservan en la carpeta de salida.
FRASES_RECORDADAS = 100       # frase_id propios que se recuerdan (dedup/propiedad).

# Los textos hablados viven en `textos.py`. Se reexportan aqui con los mismos
# nombres de siempre para no romper a quien los importa de `satelite`.
MENSAJE_REPETIR = textos.MENSAJE_REPETIR
MENSAJE_SIN_CONEXION = textos.MENSAJE_SIN_CONEXION

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
    limite = ANTIGUEDAD_ALERTA_S if carga.get("tipo") == TIPO_ALERTA else ANTIGUEDAD_S
    return abs(ahora_s - int(ts)) <= limite


def topico_estado_de(cfg) -> str:
    """El topico de estado de ESTE satelite: `casa/sala/satelite/<client_id>/estado`.

    Acepta `{client_id}` en el valor del config. Si el config trae escrito el
    topico unico de antes (`casa/sala/satelite/estado`), se usa el nuevo y se
    avisa: con la ACL de la Fase 2C ese topico ya no se puede escribir, y el
    satelite saldria «desconectado» en el panel sin ningun error a la vista.
    """
    if not cfg.client_id or any(c in cfg.client_id for c in "/+#"):
        raise ValueError("client_id %r no sirve para un topico MQTT" % cfg.client_id)
    valor = cfg.topico_estado or TOPICO_ESTADO_DEF
    if valor == TOPICO_ESTADO_VIEJO:
        registro.warning("mqtt.topico_estado = %r es el de antes de la Fase 2C; se "
                         "usa %r. Quitalo del config.", valor, TOPICO_ESTADO_DEF)
        valor = TOPICO_ESTADO_DEF
    return valor.replace("{client_id}", cfg.client_id)


def topico_video_de(cfg) -> str:
    if not cfg.client_id or any(c in cfg.client_id for c in "/+#"):
        raise ValueError("client_id %r no sirve para un topico MQTT" % cfg.client_id)
    valor = cfg.topico_video if getattr(cfg, "topico_video", None) else TOPICO_VIDEO_DEF
    return valor.replace("{client_id}", cfg.client_id)


def limpiar_consulta(texto) -> str:
    """Limpia la consulta de un video: solo letras, numeros, espacios y acentos.

    Maximo 100 caracteres. No usa shell ni compone comandos: el satelite arma la
    URL localmente a partir de este texto ya limpio.
    """
    if not isinstance(texto, str):
        return ""
    limpio = "".join(c for c in texto if c.isalnum() or c.isspace())
    return " ".join(limpio.split())[:100]


limpiar_consulta_video = limpiar_consulta


def leer_wav_estricto(ruta: str) -> bytes:
    """PCM de un WAV que YA es 16 kHz, mono, 16 bits. Si no, `ValueError` claro.

    Es la entrada por archivo del satelite de la VM. A diferencia de
    `_leer_wav_16k` (modo de prueba), aqui no se convierte nada: un WAV con otro
    formato se rechaza diciendo que tiene y que hace falta, para que quien lo
    mando sepa como arreglarlo.
    """
    if not os.path.isabs(ruta):
        raise ValueError("la ruta tiene que ser absoluta: %s" % ruta)
    if not os.path.isfile(ruta):
        raise ValueError("no existe el archivo %s" % ruta)
    try:
        with wave.open(ruta, "rb") as w:
            canales = w.getnchannels()
            ancho = w.getsampwidth()
            frecuencia = w.getframerate()
            datos = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as error:
        raise ValueError("no es un WAV PCM valido (%s); se necesita PCM 16 kHz "
                         "mono 16 bits" % error) from None
    problemas = []
    if frecuencia != TASA:
        problemas.append("%d Hz en vez de %d Hz" % (frecuencia, TASA))
    if canales != 1:
        problemas.append("%d canales en vez de 1 (mono)" % canales)
    if ancho != 2:
        problemas.append("%d bits en vez de 16" % (8 * ancho))
    if problemas:
        raise ValueError("el WAV no es PCM 16 kHz mono 16 bits: tiene %s"
                         % ", ".join(problemas))
    if not datos:
        raise ValueError("el WAV no tiene audio")
    return datos


_NO_SEGURO = re.compile(r"[^A-Za-z0-9_-]+")


def _parte_de_nombre(texto: str) -> str:
    return _NO_SEGURO.sub("-", texto).strip("-") or "x"


def guardar_salida(carpeta: str, wav: bytes, tipo: str, frase_id,
                   maximo: int = MAX_SALIDAS, ahora: float | None = None) -> str:
    """Guarda lo dicho como `<AAAAMMDD-HHMMSS>_<tipo>_<frase_id|sistema>.wav`.

    Se escribe primero a un temporal y se renombra, para que quien escuche la
    carpeta no vea nunca un WAV a medias. Despues se conservan solo los `maximo`
    mas recientes. Devuelve la ruta escrita.
    """
    os.makedirs(carpeta, exist_ok=True)
    marca = time.strftime("%Y%m%d-%H%M%S", time.localtime(ahora))
    base = "%s_%s_%s" % (marca, _parte_de_nombre(tipo or "sistema"),
                         _parte_de_nombre(frase_id) if frase_id else "sistema")
    ruta = os.path.join(carpeta, base + ".wav")
    n = 2
    while os.path.exists(ruta):
        ruta = os.path.join(carpeta, "%s-%d.wav" % (base, n))
        n += 1
    temporal = ruta + ".parcial"
    with open(temporal, "wb") as archivo:
        archivo.write(wav)
    os.replace(temporal, ruta)
    podar_salida(carpeta, maximo)
    return ruta


def podar_salida(carpeta: str, maximo: int = MAX_SALIDAS) -> int:
    """Borra los WAV mas viejos de la carpeta y deja `maximo`. Devuelve cuantos."""
    wavs = []
    for nombre in os.listdir(carpeta):
        if nombre.endswith(".wav"):
            ruta = os.path.join(carpeta, nombre)
            wavs.append((os.stat(ruta).st_mtime_ns, nombre, ruta))
    wavs.sort()
    sobran = wavs[:-maximo] if maximo > 0 else wavs
    for _, _, ruta in sobran:
        os.unlink(ruta)
    return len(sobran)


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
    topico_video: str = TOPICO_VIDEO_DEF
    entrada_nodo: str = ""
    salida_nodo: str = ""
    latencia_captura: str = "20ms"
    altura_max_s: float = 8.0
    piper: str = ""
    voz: str = ""
    # Parametros de Piper. `None` significa "no lo pases": sin valor en el config,
    # Piper usa su valor por defecto y el comportamiento es el de siempre.
    length_scale: float | None = None
    sentence_silence: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    registro_ruta: str = ""
    socket_ruta: str = ""
    precargar_piper: bool = False
    # Fase 2C: entrada y salida sin dispositivo de audio (satelite de la VM).
    fuente: str = "pipewire"
    salida_modo: str = "audio"
    salida_carpeta: str = ""
    salida_max: int = MAX_SALIDAS


def _expandir(ruta: str) -> str:
    return os.path.expanduser(os.path.expandvars(ruta))


def _opcional_float(valor):
    """Convierte a `float`, o devuelve `None` si no hay valor.

    `None` no es "cero": es "no lo pases", y es lo que distingue un config sin el
    parametro (Piper pone su valor por defecto) de uno que lo pide a 0.0.
    """
    if valor is None:
        return None
    return float(valor)


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
    voz = doc.get("voz", {})
    tecla = doc.get("tecla", {})
    salida = doc.get("salida", {})

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
    cfg.topico_video = mq.get("topico_video", cfg.topico_video)
    cfg.entrada_nodo = audio.get("entrada_nodo", cfg.entrada_nodo)
    cfg.salida_nodo = audio.get("salida_nodo", cfg.salida_nodo)
    cfg.latencia_captura = audio.get("latencia_captura", cfg.latencia_captura)
    cfg.altura_max_s = float(audio.get("altura_max_s", cfg.altura_max_s))
    cfg.piper = rutas.get("piper", cfg.piper)
    # La voz sale de `[voz].modelo` y, si no esta, de `[rutas].voz` (configs
    # antiguos). Los parametros son opcionales: `None` = valor por defecto de Piper.
    cfg.voz = voz.get("modelo", rutas.get("voz", cfg.voz))
    cfg.length_scale = _opcional_float(voz.get("length_scale"))
    cfg.sentence_silence = _opcional_float(voz.get("sentence_silence"))
    cfg.noise_scale = _opcional_float(voz.get("noise_scale"))
    cfg.noise_w = _opcional_float(voz.get("noise_w"))
    cfg.registro_ruta = rutas.get("registro", _ruta_estado_defecto())
    cfg.socket_ruta = tecla.get("socket", _ruta_socket_defecto())
    cfg.precargar_piper = bool(rutas.get("precargar_piper", cfg.precargar_piper))
    cfg.fuente = audio.get("fuente", cfg.fuente)
    if cfg.fuente not in FUENTES:
        raise ValueError("audio.fuente = %r; se admite %s" % (cfg.fuente, FUENTES))
    cfg.salida_modo = salida.get("modo", cfg.salida_modo)
    if cfg.salida_modo not in MODOS_SALIDA:
        raise ValueError("salida.modo = %r; se admite %s"
                         % (cfg.salida_modo, MODOS_SALIDA))
    cfg.salida_carpeta = salida.get("carpeta", cfg.salida_carpeta)
    cfg.salida_max = int(salida.get("maximo", cfg.salida_max))
    if cfg.salida_modo == "archivo" and not cfg.salida_carpeta:
        raise ValueError("salida.modo = \"archivo\" necesita salida.carpeta")
    return cfg


def cargar_config_publica(ruta: str) -> Config:
    with open(_expandir(ruta), "rb") as archivo:
        doc = __import__("tomllib").load(archivo)

    audio = doc.get("audio", {})
    voz = doc.get("voz", {})
    rutas = doc.get("rutas", {})
    tecla = doc.get("tecla", {})

    cfg = Config()
    cfg.entrada_nodo = audio.get("entrada_nodo", cfg.entrada_nodo)
    cfg.salida_nodo = audio.get("salida_nodo", cfg.salida_nodo)
    cfg.latencia_captura = audio.get("latencia_captura", cfg.latencia_captura)
    cfg.altura_max_s = float(audio.get("altura_max_s", cfg.altura_max_s))
    cfg.piper = rutas.get("piper", cfg.piper)
    cfg.voz = voz.get("modelo", rutas.get("voz", cfg.voz))
    cfg.length_scale = _opcional_float(voz.get("length_scale"))
    cfg.sentence_silence = _opcional_float(voz.get("sentence_silence"))
    cfg.noise_scale = _opcional_float(voz.get("noise_scale"))
    cfg.noise_w = _opcional_float(voz.get("noise_w"))
    cfg.precargar_piper = bool(rutas.get("precargar_piper", cfg.precargar_piper))
    cfg.socket_ruta = tecla.get("socket", _ruta_socket_defecto())
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


class FuentePCM:
    """Entrega un PCM ya validado (16 kHz mono 16 bits) en tramas, a ritmo real.

    Es la fuente de `asistente-tecla archivo`: el WAV se valida al recibir la orden
    (`leer_wav_estricto`), y aqui solo se trocea. El ritmo de 100 ms por trama es
    el de la captura real, para que el servicio de voz vea lo mismo que con
    microfono.
    """

    def __init__(self, pcm: bytes, ritmo: float = 0.1):
        self.pcm = pcm
        self.ritmo = ritmo

    async def tramas(self):
        for i in range(0, len(self.pcm), TRAMA_BYTES):
            yield self.pcm[i:i + TRAMA_BYTES]
            if self.ritmo:
                await asyncio.sleep(self.ritmo)


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
        # Hook opcional: se llama con la marca del arranque del sonido (fin de la
        # sintesis), ANTES de la reproduccion. El nucleo lo usa para escribir el
        # registro en ese momento: la metrica fin->sonido es de la sintesis, y no
        # hay que esperar a que termine pw-play (que en salida real puede tardar
        # mas que la vigia de 3 s y dejaba la frase registrada a medias).
        self.al_sintesis = None

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
        # Cada parametro se pasa SOLO si esta definido. Sin valor en el config,
        # Piper usa su propio valor por defecto, que es lo que hacia antes de que
        # estos parametros existieran.
        opciones = (
            ("--length_scale", self.cfg.length_scale),
            ("--sentence_silence", self.cfg.sentence_silence),
            ("--noise_scale", self.cfg.noise_scale),
            ("--noise_w", self.cfg.noise_w),
        )
        for nombre, valor in opciones:
            if valor is not None:
                orden += [nombre, str(valor)]
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

    def _matar_piper(self) -> None:
        """Mata Piper si una alerta corto la sintesis a mitad de un WAV.

        Su `stdout` se quedaria con el resto de ese WAV, y la frase siguiente
        leeria bytes de la anterior. Matarlo cuesta recargar el modelo una vez, que
        es mejor que una frase corrupta.
        """
        if self._proceso is not None and self._proceso.returncode is None:
            try:
                self._proceso.kill()
            except ProcessLookupError:
                pass
        self._proceso = None

    async def decir(self, texto: str, tipo: str = "", frase_id=None) -> int:
        """Sintetiza y reproduce (o guarda). Devuelve la marca del sonido (ms).

        Se puede cancelar (una alerta interrumpe una respuesta): si se cancela
        durante la sintesis se mata Piper, y si es durante la reproduccion se mata
        `pw-play`. `tipo` y `frase_id` solo se usan para nombrar el WAV en la
        salida a carpeta.
        """
        await self.iniciar()
        proceso = self._proceso
        assert proceso is not None and proceso.stdin is not None
        try:
            proceso.stdin.write((texto + "\n").encode("utf-8"))
            await proceso.stdin.drain()
            wav = await _leer_wav(proceso.stdout)
        except asyncio.CancelledError:
            self._matar_piper()
            raise
        t_sonido = reloj_ms()
        if self.al_sintesis is not None:
            self.al_sintesis(t_sonido)
        if self.salida_nula:
            return t_sonido
        if self.cfg.salida_modo == "archivo":
            ruta = guardar_salida(_expandir(self.cfg.salida_carpeta), wav, tipo,
                                  frase_id, self.cfg.salida_max)
            registro.info("dicho guardado en %s", ruta)
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
            try:
                reproductor.stdin.write(pcm)
                await reproductor.stdin.drain()
            finally:
                reproductor.stdin.close()
            await reproductor.wait()
        except asyncio.CancelledError:
            if reproductor.returncode is None:
                reproductor.kill()
            raise
        return t_sonido

    async def sintetizar(self, texto: str) -> int:
        """Sintetiza y descarta el audio, sin reproducirlo.

        Arranca Piper y lee un WAV completo para que el modelo quede cargado en el
        proceso persistente, pero el audio NO sale por las bocinas. Sirve para la
        precarga silenciosa: el primer turno real no paga la carga del modelo y el
        usuario no oye la frase de precarga.
        """
        await self.iniciar()
        proceso = self._proceso
        assert proceso is not None and proceso.stdin is not None
        proceso.stdin.write((texto + "\n").encode("utf-8"))
        await proceso.stdin.drain()
        await _leer_wav(proceso.stdout)
        return reloj_ms()


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
        self.topico_estado = topico_estado_de(cfg)
        self.topico_hablar = "casa/sala/satelite/%s/hablar" % cfg.client_id
        self.topico_video = topico_video_de(cfg)
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
            self.topico_estado,
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
        registro.info("MQTT conectado como %s (client_id=%s, estado en %s)",
                      self.cfg.mqtt_usuario, self.cfg.client_id, self.topico_estado)
        topico_video = getattr(self, "topico_video", None) or topico_video_de(self.cfg)
        self.topico_video = topico_video
        cliente.subscribe(self.cfg.topico_decir, qos=1)
        cliente.subscribe(self.topico_hablar, qos=1)
        cliente.subscribe(topico_video, qos=1)
        video = False
        if getattr(self, "nucleo", None) is not None:
            video = getattr(self.nucleo, "video_disponible", False)
        cliente.publish(self.topico_estado,
                        json.dumps({"conectado": True, "hablar": True,
                                    "video": video},
                                   separators=(",", ":")),
                        qos=0, retain=True)
        self.loop.call_soon_threadsafe(self.listo.set)

    def _al_mensaje(self, cliente, userdata, mensaje):
        try:
            carga = json.loads(mensaje.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            registro.warning("satelite/%s no es JSON; se ignora", mensaje.topic)
            return
        if mensaje.topic == self.topico_hablar:
            self.loop.call_soon_threadsafe(self.nucleo.al_hablar, carga)
        elif mensaje.topic == self.cfg.topico_decir:
            self.loop.call_soon_threadsafe(self.nucleo.al_decir, carga)
        elif mensaje.topic == (getattr(self, "topico_video", None) or topico_video_de(self.cfg)):
            self.loop.call_soon_threadsafe(self.nucleo.al_video, carga)
        else:
            registro.debug("satelite: topico ignorado %s", mensaje.topic)

    def cerrar(self) -> None:
        try:
            self.cliente.publish(self.topico_estado,
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
    frase_id: str | None = None


# Prioridad en la cola de voz: menor = antes. Una alerta pasa delante de todo.
PRIORIDAD_ALERTA = 0
PRIORIDAD_NORMAL = 1


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
        # Cola con prioridad: (prioridad, orden de llegada, item). Las alertas van
        # delante; entre iguales, en orden de llegada.
        self.cola: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._orden_cola = 0
        self._item_actual: ItemHabla | None = None
        self._tarea_decir: asyncio.Task | None = None
        # Los frase_id que genero ESTE satelite (los ultimos FRASES_RECORDADAS), y
        # si su respuesta ya se dijo. Es lo que decide si una `respuesta` es suya:
        # comparar prefijos fallaria con client ids como `sala` y `sala-2`.
        self._mis_frases: collections.OrderedDict = collections.OrderedDict()
        self._contador = 0
        self.primer_turno_ms: int | None = None
        self._primer_turno_hecho = False
        self.segundos_sin_respuesta = SEGUNDOS_SIN_RESPUESTA
        self._timer_hablar = None
        self.video_disponible = bool(shutil.which("mpv") and shutil.which("yt-dlp")
                                    and os.environ.get("WAYLAND_DISPLAY"))
        self._proceso_video = None
        self.idioma_video_pendiente = "es"
        self.idioma_video_hasta = 0.0
        if not self.video_disponible:
            registro.warning("video: desactivado (falta mpv, yt-dlp o WAYLAND_DISPLAY)")

    # --- tecla ---
    def presionar(self) -> None:
        if self.cfg.fuente == "archivo":
            registro.info("tecla ignorada: la fuente es archivo (use "
                          "`asistente-tecla archivo <ruta.wav>`)")
            return
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

    def al_hablar(self, carga: dict) -> None:
        """Inicio/fin del microfono desde el panel (topico .../hablar)."""
        try:
            ts = carga.get("ts")
        except AttributeError:
            ts = None
        if not isinstance(ts, (int, float)):
            registro.warning("hablar: mensaje sin ts, descartado")
            return
        if abs(time.time() - float(ts)) > 10:
            registro.warning("hablar: mensaje antiguo (ts=%s), descartado", ts)
            return
        accion = carga.get("accion") if isinstance(carga, dict) else None
        if accion == "inicio":
            if self.frase is not None or self.hablando:
                registro.info("hablar ignorado: ya hay una frase o se esta hablando")
                return
            if self._timer_hablar is not None:
                try:
                    self._timer_hablar.cancel()
                except Exception:  # noqa: BLE001
                    pass
            def _corte():
                try:
                    if not self.loop.is_closed():
                        self.loop.call_soon_threadsafe(self.soltar)
                except RuntimeError:
                    pass
            self._timer_hablar = threading.Timer(10.0, _corte)
            self._timer_hablar.start()

            def _run() -> None:
                self._tono_corto()
                self.loop.call_soon_threadsafe(self.presionar)

            threading.Thread(target=_run, daemon=True).start()
        elif accion == "fin":
            if self._timer_hablar is not None:
                try:
                    self._timer_hablar.cancel()
                except Exception:  # noqa: BLE001
                    pass
                self._timer_hablar = None
            self.soltar()
        else:
            registro.warning("hablar: accion desconocida %r", accion)

    def _tono_corto(self) -> None:
        """Tono breve por la bocina antes de abrir la frase del panel."""
        try:
            fd, ruta = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            frecuencia = 44100
            duracion = 0.15
            n = int(frecuencia * duracion)
            with wave.open(ruta, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(frecuencia)
                frames = bytearray()
                for i in range(n):
                    valor = int(32767 * 0.3 *
                                math.sin(2 * math.pi * 880 * i / frecuencia))
                    frames += struct.pack("<h", valor)
                w.writeframes(frames)
            for cmd in (["pw-play", ruta], ["aplay", "-q", ruta]):
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, check=False)
                    break
                except FileNotFoundError:
                    continue
        except Exception as error:  # noqa: BLE001
            registro.warning("tono corto: %s", error)
        finally:
            try:
                os.unlink(ruta)
            except OSError:
                pass

    def al_video(self, carga: dict) -> None:
        """Procesa una orden de video (buscar/cerrar) del orquestador."""
        if not isinstance(carga, dict):
            return
        ts = carga.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            registro.warning("video: mensaje sin ts, descartado")
            return
        if abs(time.time() - float(ts)) > 30:
            registro.warning("video: mensaje antiguo (ts=%s), descartado", ts)
            return
        accion = carga.get("accion")
        if accion == "cerrar":
            self._cerrar_video()
            return
        if accion != "buscar":
            registro.debug("video: accion desconocida %r", accion)
            return
        if not self.video_disponible:
            registro.warning("video: no disponible (falta mpv, yt-dlp o WAYLAND_DISPLAY)")
            self._encolar_habla("No puedo poner videos en este momento.", "aviso")
            return
        consulta = limpiar_consulta(carga.get("consulta", ""))
        if len(consulta) < 2:
            return
        # Cierra el video anterior antes de abrir uno nuevo.
        self._cerrar_video()
        args = ["mpv", "--fs", "--no-terminal",
                "ytdl://ytsearch1:%s" % consulta]
        try:
            self._proceso_video = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            registro.info("video: mpv lanzado (consulta_len=%d)", len(consulta))
        except Exception as error:  # noqa: BLE001
            registro.warning("video: no se pudo lanzar mpv: %s", error)
            self._proceso_video = None

    def _cerrar_video(self) -> None:
        proc = self._proceso_video
        self._proceso_video = None
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as error:  # noqa: BLE001
            registro.warning("video: no se pudo cerrar mpv: %s", error)

    def cerrar_video(self) -> None:
        self._cerrar_video()

    def enviar_archivo(self, pcm: bytes) -> tuple:
        """Manda un PCM ya validado como una frase completa. `(frase_id, error)`.

        Las mismas condiciones que la tecla: no mientras el asistente habla (P14),
        no con otra frase abierta y no sin WebSocket. Pero aqui se contesta el
        motivo, porque quien manda el archivo esta esperando la respuesta en la
        terminal.
        """
        if self.hablando:
            return None, "el asistente esta hablando; intente en unos segundos"
        if self.frase is not None:
            return None, "ya hay una frase abierta"
        if self.ws is None or not self.ws.listo:
            return None, "el WebSocket no esta listo (sin conexion con el servidor)"
        self.fuente = FuentePCM(pcm)
        frase = self.abrir_frase(origen="archivo", idioma=None)
        if self.frase is not frase:
            return None, "no se pudo abrir la frase (sin conexion)"
        return frase.frase_id, None

    # --- ciclo de una frase ---
    def _idioma_video_pendiente_actual(self) -> str:
        if time.time() < self.idioma_video_hasta and self.idioma_video_pendiente in ("es", "en"):
            return self.idioma_video_pendiente
        self.idioma_video_pendiente = "es"
        self.idioma_video_hasta = 0.0
        return "es"

    def _guardar_idioma_video_pendiente(self, idioma, ventana_s=None) -> None:
        if idioma not in ("es", "en"):
            idioma = "es"
        self.idioma_video_pendiente = idioma
        try:
            segundos = float(ventana_s) if ventana_s is not None else 15.0
        except (TypeError, ValueError):
            segundos = 15.0
        if segundos <= 0:
            segundos = 15.0
        self.idioma_video_hasta = time.time() + segundos

    def _borrar_idioma_video_pendiente(self) -> None:
        self.idioma_video_pendiente = "es"
        self.idioma_video_hasta = 0.0

    def abrir_frase(self, origen: str = "tecla", idioma=None) -> Frase:
        if idioma is None:
            idioma = self._idioma_video_pendiente_actual()
        if idioma not in ("es", "en"):
            idioma = "es"
        self._contador += 1
        # `<client_id>-<epoch>-<n>`: con dos satelites, el eco lleva el frase_id
        # y cada uno sabe si la respuesta es para el.
        frase_id = "%s-%d-%d" % (self.cfg.client_id, int(time.time()),
                                 self._contador)
        self._mis_frases[frase_id] = False
        while len(self._mis_frases) > FRASES_RECORDADAS:
            self._mis_frases.popitem(last=False)
        frase = Frase(frase_id=frase_id, origen=origen)
        self.frase = frase
        registro.info("frase %s abierta (%s, idioma=%s)", frase_id, origen, idioma)
        try:
            self.ws.enviar_json({"tipo": "frase", "frase_id": frase_id,
                                 "modo": "comandos", "idioma": idioma})
        except ConnectionError as error:
            registro.error("no se pudo abrir la frase: %s", error)
            self.frase = None
            self._encolar_habla(MENSAJE_SIN_CONEXION, "local")
            return frase
        self._borrar_idioma_video_pendiente()
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
        await asyncio.sleep(self.segundos_sin_respuesta)
        if frase.t_decir is None:
            frase.sin_respuesta = True
            registro.warning("frase %s: nada en satelite/decir en %d s",
                             frase.frase_id, self.segundos_sin_respuesta)
        elif not frase.escrito:
            # El eco ya se hablo pero el `final` del WebSocket no llego a tiempo:
            # se registra como esta (sin fin->final) para no colgarla de la vigia.
            registro.warning("frase %s: sin final del WebSocket en %d s",
                             frase.frase_id, self.segundos_sin_respuesta)
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
        if frase.t_sonido is not None or frase.sin_respuesta:
            self._rematar_turno(frase)
        if frase.t_decir is not None and self.turno_pendiente is frase:
            self.turno_pendiente = None

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
        """Decide si ESTE satelite dice un mensaje de `satelite/decir`.

        Con dos satelites, el contrato es:

        - `destino` (un client id), si viene, filtra antes que nada: solo lo dice
          ese satelite, sea del tipo que sea.
        - `respuesta` con `frase_id` de una frase de este satelite: la dice (una
          vez; una entrega duplicada no se repite).
        - `respuesta` con `frase_id` ajeno: es de otro satelite; se ignora.
        - `respuesta` SIN `frase_id`: contrato viejo; se dice y se avisa.
        - Cualquier otro tipo (`aviso`, `alerta`, `recordatorio`...): lo dicen
          todos.
        - Una `alerta` pasa delante de la cola y corta una respuesta que este
          sonando (`_encolar_habla`).
        """
        if not isinstance(carga, dict):
            return
        es_pregunta_video = (carga.get("tipo") == "respuesta"
                             and carga.get("ventana") == "video")
        if not es_pregunta_video:
            self._borrar_idioma_video_pendiente()
        destino = carga.get("destino")
        if destino is not None and destino != self.cfg.client_id:
            registro.debug("satelite/decir para %r, no para %s; se ignora",
                           destino, self.cfg.client_id)
            return
        ahora = int(time.time())
        if not aceptar_por_antiguedad(carga, ahora):
            registro.warning("satelite/decir descartado por antiguedad o sin ts: %s",
                             carga)
            return
        texto = carga.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            return
        tipo = carga.get("tipo", "")
        frase_id = carga.get("frase_id")
        if not isinstance(frase_id, str) or not frase_id:
            frase_id = None
        if tipo == "respuesta" and es_pregunta_video:
            self._guardar_idioma_video_pendiente(
                carga.get("idioma", "es"), carga.get("ventana_s"))
        if tipo == "respuesta":
            self._al_decir_respuesta(texto, frase_id)
            return
        self._encolar_habla(texto, tipo or "aviso", frase_id=frase_id)

    def _al_decir_respuesta(self, texto: str, frase_id) -> None:
        if frase_id is None:
            registro.warning("respuesta sin frase_id en satelite/decir: se dice, pero "
                             "con dos satelites la diran todos")
            frase = None
            if self.turno_pendiente is not None:
                # Si el eco de esta frase ya se hablo (entrega duplicada del
                # broker), no se habla dos veces.
                if self.turno_pendiente.t_decir is not None:
                    return
                frase = self._marcar_decir(self.turno_pendiente)
            self._encolar_habla(texto, "respuesta", turno=frase)
            return
        if frase_id not in self._mis_frases:
            registro.debug("respuesta para %s, que no es de este satelite; se ignora",
                           frase_id)
            return
        if self._mis_frases[frase_id]:
            return  # entrega duplicada del broker: ya se dijo
        self._mis_frases[frase_id] = True
        frase = None
        if self.turno_pendiente is not None and \
                self.turno_pendiente.frase_id == frase_id:
            frase = self._marcar_decir(self.turno_pendiente)
        self._encolar_habla(texto, "respuesta", turno=frase, frase_id=frase_id)

    def _marcar_decir(self, frase: Frase) -> Frase:
        frase.t_decir = reloj_ms()
        # El final del WebSocket y el eco llegan por caminos distintos y en
        # cualquier orden (la red puede reordenarlos): el turno pendiente NO se
        # libera con el eco, sino cuando ya se tienen las dos marcas; si no, un
        # final llegado despues del eco caeria en "frase desconocida" y se
        # perderia fin->final.
        if frase.t_final is not None:
            self.turno_pendiente = None
        return frase

    # --- cola de voz ---
    def _encolar_habla(self, texto: str, tipo: str,
                       turno: Frase | None = None, frase_id=None) -> None:
        item = ItemHabla(texto=texto, tipo=tipo, turno=turno,
                         recibido_ms=reloj_ms(), frase_id=frase_id)
        prioridad = PRIORIDAD_ALERTA if tipo == TIPO_ALERTA else PRIORIDAD_NORMAL
        self._orden_cola += 1
        self.cola.put_nowait((prioridad, self._orden_cola, item))
        # Una alerta corta lo que este sonando, salvo que sea otra alerta: el gas no
        # espera a que termine «Usted dijo...». Lo cortado no se repite.
        actual = self._item_actual
        if tipo == TIPO_ALERTA and actual is not None and actual.tipo != TIPO_ALERTA \
                and self._tarea_decir is not None and not self._tarea_decir.done():
            registro.warning("alerta: se interrumpe lo que se estaba diciendo (%s)",
                             actual.tipo)
            self._tarea_decir.cancel()

    async def bucle_habla(self) -> None:
        while True:
            _, _, item = await self.cola.get()
            self._item_actual = item
            self.hablando = True
            turno = item.turno
            if turno is not None:
                # El registro se cierra al SINETIZAR (cuando se conoce la marca del
                # sonido), no al terminar de reproducir: en salida real pw-play tarda
                # unos segundos y podia dejar que la vigia de 3 s escribiera la frase
                # sin t_sonido (perdiendo fin->sonido).
                def al_sintesis(t_sonido: int) -> None:
                    turno.t_sonido = t_sonido
                    self._medir_primer_turno(turno)
                    self._rematar_turno(turno)
                self.rep.al_sintesis = al_sintesis
            t_sonido = None
            self._tarea_decir = asyncio.get_running_loop().create_task(
                self.rep.decir(item.texto, tipo=item.tipo, frase_id=item.frase_id))
            try:
                t_sonido = await self._tarea_decir
            except asyncio.CancelledError:
                # Si el que se cancela es este bucle (el satelite se apaga), se
                # sigue cancelando. Si solo se cancelo la frase, fue una alerta.
                if asyncio.current_task().cancelling():
                    raise
                registro.info("%s interrumpida por una alerta", item.tipo)
            except Exception as error:  # noqa: BLE001
                registro.error("no se pudo hablar: %s", error)
            finally:
                self._tarea_decir = None
                self._item_actual = None
                self.hablando = False
                if turno is not None:
                    self.rep.al_sintesis = None
                    if turno.t_sonido is None and t_sonido is not None:
                        turno.t_sonido = t_sonido
                        self._medir_primer_turno(turno)
                    if turno.t_sonido is not None:
                        self._rematar_turno(turno)

    def _rematar_turno(self, frase: Frase) -> None:
        """Escribe la frase cuando ya esta completa, sea cual sea el orden.

        El `final` del WebSocket y el eco por MQTT viajan por caminos distintos y
        la red puede reordenarlos: el registro se cierra cuando el ULTIMO de los
        dos llega, no al hablar. Antes se escribia al terminar de hablar, de modo
        que un `final` tardio (llegado despues del eco) se descartaba por
        desconocido y la frase salia sin fin->final.
        """
        if frase.t_final is None:
            return
        self._escribir_frase(frase)

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
            datos = await reader.read(4096)
            # `A <ruta>\n` (archivo) puede llegar en mas de un trozo.
            while datos[:1] in (b"a", b"A") and not datos.endswith(b"\n") \
                    and len(datos) < 8192:
                mas = await asyncio.wait_for(reader.read(4096), timeout=2)
                if not mas:
                    break
                datos += mas
            accion = datos.strip().lower()
            respuesta = b"ok"
            # tecla.py envia P al presionar y R al soltar (P/R); tambien se
            # aceptan p/s. Sin esto, la R del cierre caia en desconocida y el
            # soltar se perdia: la frase quedaba abierta hasta el tope de altura.
            if accion.startswith(b"a"):
                respuesta = self._archivo(datos.strip()[1:].strip())
            elif accion.startswith(b"p"):
                self.nucleo.presionar()
            elif accion.startswith(b"s") or accion.startswith(b"r"):
                self.nucleo.soltar()
            writer.write(respuesta)
            await writer.drain()
        except Exception as error:  # noqa: BLE001
            registro.warning("socket de tecla: %s", error)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    def _archivo(self, ruta_bytes: bytes) -> bytes:
        """`asistente-tecla archivo <ruta>`: valida el WAV y lo manda como frase."""
        try:
            ruta = ruta_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return b"error: la ruta no es UTF-8"
        try:
            pcm = leer_wav_estricto(ruta)
        except ValueError as error:
            registro.warning("archivo rechazado: %s", error)
            return ("error: %s" % error).encode("utf-8")
        frase_id, error = self.nucleo.enviar_archivo(pcm)
        if error is not None:
            registro.warning("archivo %s no enviado: %s", ruta, error)
            return ("error: %s" % error).encode("utf-8")
        registro.info("archivo %s -> frase %s", ruta, frase_id)
        return ("ok %s" % frase_id).encode("utf-8")


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
            await reproductor.sintetizar("hola")
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
        nucleo.cerrar_video()
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
