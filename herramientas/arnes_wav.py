#!/usr/bin/env python3
"""Arnes de pruebas de voz de la etapa A (Paso 2).

Simula al satelite: coge frases de texto, las convierte en voz con Piper, y las
manda al servicio de voz por WebSocket exactamente como lo haria el satelite (PCM
16 kHz mono, tramas de 100 ms, a ritmo real). Despues mira el resultado por MQTT y
mide aciertos y latencias.

Por que existe: el reconocimiento tiene un problema que no se ve leyendo codigo.
Una frase de la gramatica puede reconocerse como OTRA frase de la gramatica, o una
frase de fuera puede colarse como un comando. Eso no lo detecta ninguna prueba
unitaria; hay que decir frases de verdad y mirar que sale. Este arnes las dice.

Tres cosas que hace y que una prueba normal no puede:

  1. Aciertos. Recorre la gramatica, sintetiza cada frase y cuenta cuantas se
     reconocen tal cual. Un modelo y una gramatica se validan asi, no a ojo.
  2. Fuera de gramatica. Dice frases que NO son comandos y cuenta cuantas caen en
     [unk] (bien) y cuantas se confunden con un comando (mal). Es la medida del
     falso positivo, que es lo que enciende una luz sin que nadie lo pida.
  3. Errores del protocolo. Token malo, audio antes del inicio, trama de tamano
     invalido, corte a mitad de frase. Se comprueba que el servicio contesta lo
     que dice el contrato y, en el corte, que NO publica media frase.

El audio es sintetico y vive en un directorio temporal: se borra al terminar. No
se guarda nada en el repositorio.

Uso (con el servicio de voz levantado):

    .venv/bin/python herramientas/arnes_wav.py
    .venv/bin/python herramientas/arnes_wav.py --solo aciertos --limite 8
    .venv/bin/python herramientas/arnes_wav.py --sin-mqtt   # sin eco/latencias

Dependencias: `websockets` (cliente) y `numpy`, y `paho-mqtt` para el observador.
Todas estan en el venv `.venv` del repositorio (ver el reporte de la etapa A).
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unicodedata
import wave

import numpy as np
import paho.mqtt.client as mqtt
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICIO_GRABADO = os.path.join(RAIZ, "servidor", "voz", "gramatica.json")
TOKEN_POR_DEFECTO = "/home/ilse/Servidores/asistente-voz-secretos/satelite/token"
PIPER_DIR_POR_DEFECTO = os.path.join(RAIZ, "herramientas", "piper")
VOZ_POR_DEFECTO = os.path.join(RAIZ, "herramientas", "voces", "es_MX-ald-medium.onnx")
ENV_SERVIDOR = os.path.join(RAIZ, "servidor", ".env")

SR = 16000
TRAMA = 3200                 # 100 ms de PCM 16 kHz mono 16 bits.
MAX_BYTES_FRASE = TRAMA * 100

# Frases que NO son comandos. La mitad son parecidas a un comando (para ver si se
# confunden) y la otra mitad no tienen nada que ver (para ver si se cuelan). La
# primera es la interesante: "prende la tele" no debe encender la luz.
FRASES_FUERA = [
    "prende la tele",
    "apaga la estufa",
    "enciende el radio",
    "la luz esta bonita",
    "ayer",
    "a ver",
    "ya",
    "necesito agua",
    "cuanto cuesta",
    "buenas noches",
    "necesito ayuda por favor",
    "ayuda ahora",
    "auxilio ahora",
    "ayudame ya",
    "apaga la tele",
    "prende la estufa",
    "enciende la tele",
    "apaga por favor",
    "prende por favor",
    "la ayuda",
]

_FALLOS = []


# --- Utilidades -------------------------------------------------------------

def normalizar(texto: str) -> str:
    """Minusculas, sin acentos y con espacios simples, para comparar."""
    if not texto:
        return ""
    texto = texto.strip().lower()
    texto = "".join(c for c in unicodedata.normalize("NFD", texto)
                    if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto)


def cargar_gramatica():
    with open(SERVICIO_GRABADO, encoding="utf-8") as archivo:
        configuracion = json.load(archivo)
    frases = []
    for lista in configuracion.get("intenciones", {}).values():
        frases.extend(lista)
    return frases


def cargar_comandos():
    with open(SERVICIO_GRABADO, encoding="utf-8") as archivo:
        configuracion = json.load(archivo)
    comandos = []
    intenciones = configuracion.get("intenciones", {})
    for clave, lista in intenciones.items():
        if clave == "SENUELO":
            continue
        if isinstance(lista, list):
            comandos.extend(lista)
    return comandos


def cargar_senuelos():
    with open(SERVICIO_GRABADO, encoding="utf-8") as archivo:
        configuracion = json.load(archivo)
    intenciones = configuracion.get("intenciones", {})
    return intenciones.get("SENUELO", []) if isinstance(intenciones.get("SENUELO"), list) else []


def leer_token(ruta: str) -> str:
    with open(ruta, encoding="utf-8") as archivo:
        return archivo.read().strip()


def leer_password_env(nombre: str) -> str:
    if not os.path.exists(ENV_SERVIDOR):
        return ""
    with open(ENV_SERVIDOR, encoding="utf-8") as archivo:
        for linea in archivo:
            if linea.startswith(nombre + "="):
                return linea.split("=", 1)[1].strip()
    return ""


def cargar_voz_config(ruta: str) -> dict:
    """Lee la seccion `[voz]` del config del satelite, para reusar su voz.

    Devuelve las claves que falten como `None`: el llamador decide con que
    rellenarlas. Es la forma de que el arnes hable con la MISMA voz y los MISMOS
    parametros que el satelite sin copiarlos a mano y sin que se desincronicen.
    """
    with open(ruta, "rb") as archivo:
        doc = tomllib.load(archivo)
    voz = doc.get("voz", {})
    return {
        "modelo": voz.get("modelo"),
        "length_scale": voz.get("length_scale"),
        "sentence_silence": voz.get("sentence_silence"),
        "noise_scale": voz.get("noise_scale"),
        "noise_w": voz.get("noise_w"),
    }


# --- Sintesis y audio -------------------------------------------------------

def sintetizar(texto: str, piper_dir: str, voz: str, largo: float = 1.0,
               sentence_silence=None, noise_scale=None, noise_w=None):
    """Devuelve PCM 16 kHz mono 16-bit LE de la frase, usando Piper.

    Piper genera a 22050 Hz; Vosk quiere 16 kHz. No hay ffmpeg ni sox en la VM, asi
    que el remuestreo se hace aqui con interpolacion lineal, que para voz de prueba
    y para Vosk es de sobra.

    `largo` es el `--length_scale` de Piper: mas de 1 habla mas despacio. Existe
    porque la velocidad de habla cambia el acierto, y conviene poder medirlo.

    `sentence_silence`, `noise_scale` y `noise_w` se pasan SOLO si tienen valor. Con
    los tres en `None` el comando es exactamente el de siempre y Piper usa sus
    valores por defecto.
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporal:
        ruta = temporal.name
    orden = [os.path.join(piper_dir, "piper"), "--model", voz,
             "--length_scale", str(largo)]
    for nombre, valor in (("--sentence_silence", sentence_silence),
                          ("--noise_scale", noise_scale),
                          ("--noise_w", noise_w)):
        if valor is not None:
            orden += [nombre, str(valor)]
    orden += ["--output_file", ruta]
    try:
        subprocess.run(
            orden,
            input=(texto + "\n").encode("utf-8"),
            cwd=piper_dir,
            env={**os.environ, "LD_LIBRARY_PATH": piper_dir},
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with wave.open(ruta, "rb") as w:
            canales = w.getnchannels()
            ancho = w.getsampwidth()
            frecuencia = w.getframerate()
            datos = w.readframes(w.getnframes())
    finally:
        os.unlink(ruta)

    if ancho != 2:
        raise RuntimeError("Piper no genero PCM de 16 bits (ancho %d)" % ancho)
    muestras = np.frombuffer(datos, dtype="<i2")
    if canales == 2:
        muestras = muestras.reshape(-1, 2).mean(axis=1).astype("<i2")

    if frecuencia != SR:
        n_salida = int(round(len(muestras) * SR / frecuencia))
        indices = np.linspace(0, len(muestras) - 1, n_salida)
        muestras = np.interp(indices, np.arange(len(muestras)),
                             muestras.astype(np.float64)).astype("<i2")

    # Un poco de silencio al final: Vosk reconoce mejor la ultima palabra si no le
    # llega pegada al corte.
    relleno = np.zeros(int(SR * 0.2), dtype="<i2")
    return np.concatenate([muestras, relleno]).tobytes()


def resolver_voz(args):
    """Completa voz y parametros: argumento > `[voz]` de `--config-voz` > defecto.

    Sin `--config-voz` ni argumentos nuevos, el resultado es el de siempre: voz
    ald-medium, `largo` 1.0 y el resto sin pasar a Piper.
    """
    desde_config = cargar_voz_config(args.config_voz) if args.config_voz else {}
    if args.voz is None:
        args.voz = desde_config.get("modelo") or VOZ_POR_DEFECTO
    args.voz = os.path.expanduser(args.voz)
    if args.largo is None:
        valor = desde_config.get("length_scale")
        args.largo = float(valor) if valor is not None else 1.0
    for nombre in ("sentence_silence", "noise_scale", "noise_w"):
        if getattr(args, nombre) is None and desde_config.get(nombre) is not None:
            setattr(args, nombre, float(desde_config[nombre]))
    return args


def sintetizar_frase(args, texto: str):
    """Sintetiza con la voz y los parametros ya resueltos en `args`."""
    return sintetizar(texto, args.piper_dir, args.voz, args.largo,
                      sentence_silence=args.sentence_silence,
                      noise_scale=args.noise_scale, noise_w=args.noise_w)


def trocear(pcm: bytes):
    return [pcm[i:i + TRAMA] for i in range(0, len(pcm), TRAMA)]


# --- El cliente del WebSocket -----------------------------------------------

async def abrir_conexion(host, puerto, token):
    url = "ws://%s:%d" % (host, puerto)
    ws = await connect(url, max_size=1 << 20)
    await ws.send(json.dumps({"tipo": "inicio", "token": token}))
    respuesta = json.loads(await ws.recv())
    return ws, respuesta


async def decir_frase(ws, frase_id, pcm, ritmo=True):
    """Manda una frase completa y devuelve el mensaje `final`."""
    await ws.send(json.dumps({"tipo": "frase", "frase_id": frase_id,
                              "modo": "comandos"}))
    for trama in trocear(pcm):
        await ws.send(trama)
        if ritmo:
            await asyncio.sleep(0.1)
    await ws.send(json.dumps({"fin": True}))
    while True:
        mensaje = json.loads(await ws.recv())
        if mensaje.get("tipo") in ("final", "error"):
            return mensaje


# --- El observador de MQTT --------------------------------------------------

class Observador:
    """Lee lo que el servicio publica y lo que el orquestador contesta en el eco.

    Usa el usuario `puente` (lectura de casa/#) porque es el unico que puede leer a
    la vez `casa/sala/voz/texto` y `casa/sala/satelite/decir`. Lo importante es que
    el client id es PROPIO (y distinto en cada corrida): conectarse con el client id
    del orquestador o del panel expulsaria a esos servicios del broker.
    """

    def __init__(self, host, puerto, usuario, password):
        self.mensajes = []
        self.candado = threading.Lock()
        self.cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="arnes-voz-%d" % os.getpid(),
            clean_session=True,
        )
        self.cliente.username_pw_set(usuario, password)
        self.cliente.on_message = self._al_mensaje
        self.cliente.connect(host, puerto, keepalive=60)
        self.cliente.subscribe([("casa/sala/voz/texto", 1),
                                ("casa/sala/satelite/decir", 1)])
        self.cliente.loop_start()

    def _al_mensaje(self, cliente, usuario, mensaje):
        try:
            carga = json.loads(mensaje.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            carga = None
        with self.candado:
            self.mensajes.append((mensaje.topic, carga, time.time()))

    def limpiar(self):
        with self.candado:
            self.mensajes.clear()

    def esperar(self, topico, predicado, timeout=5.0):
        fin = time.time() + timeout
        while time.time() < fin:
            with self.candado:
                for indice, (tp, carga, ts) in enumerate(self.mensajes):
                    if tp == topico and predicado(carga):
                        return self.mensajes.pop(indice)
            time.sleep(0.02)
        return None

    def cerrar(self):
        self.cliente.disconnect()
        self.cliente.loop_stop()


# --- Secciones --------------------------------------------------------------

async def seccion_aciertos(args, token, observador):
    print("\n1. Aciertos sobre la gramatica")
    print("-------------------------------")
    frases = cargar_comandos()
    if args.limite:
        frases = frases[:args.limite]

    aciertos = 0
    desconocidas = 0
    confundidas = []
    latencias_final = []
    latencias_publicado = []

    for indice, frase in enumerate(frases, 1):
        pcm = sintetizar_frase(args, frase)
        ws, saludo = await abrir_conexion(args.host, args.puerto, token)
        if saludo.get("tipo") != "listo":
            print("  el servicio no respondio 'listo': %s" % saludo)
            _FALLOS.append("saludo")
            await ws.close()
            continue
        frase_id = "arnes-%03d" % indice
        if observador:
            observador.limpiar()
        final = await decir_frase(ws, frase_id, pcm, ritmo=not args.sin_ritmo)
        await ws.close()

        texto = final.get("texto", "") if final.get("tipo") == "final" else ""
        marcas = final.get("marcas", {}) if final.get("tipo") == "final" else {}
        esperado = normalizar(frase)

        if normalizar(texto) == esperado:
            aciertos += 1
            marca = "OK "
        elif texto.strip() in ("", "[unk]"):
            desconocidas += 1
            marca = "UNK"
        else:
            confundidas.append((frase, texto))
            marca = "OTR"

        if marcas.get("fin_ms") and marcas.get("final_ms"):
            latencias_final.append(marcas["final_ms"] - marcas["fin_ms"])

        publicado = None
        if observador:
            publicado = observador.esperar(
                "casa/sala/voz/texto",
                lambda c, fid=frase_id: isinstance(c, dict) and c.get("frase_id") == fid,
                timeout=5.0,
            )
            if publicado and marcas.get("fin_ms"):
                pm = (publicado[1] or {}).get("marcas", {}) or {}
                if pm.get("publicado_ms"):
                    latencias_publicado.append(pm["publicado_ms"] - marcas["fin_ms"])

        print("  [%s] %-32s -> %r" % (marca, frase, texto))

    total = len(frases)
    print("\n  aciertos: %d/%d (%.0f%%)" % (aciertos, total, 100.0 * aciertos / max(total, 1)))
    print("  sin reconocer ([unk]): %d" % desconocidas)
    print("  reconocidas como otra frase: %d %s"
          % (len(confundidas), confundidas if confundidas else ""))
    if latencias_final:
        print("  latencia fin->final: min %d ms, media %.0f ms, max %d ms"
              % (min(latencias_final), sum(latencias_final) / len(latencias_final),
                 max(latencias_final)))
    if latencias_publicado:
        print("  latencia fin->publicado en MQTT: min %d ms, media %.0f ms, max %d ms"
              % (min(latencias_publicado),
                 sum(latencias_publicado) / len(latencias_publicado),
                 max(latencias_publicado)))
    return aciertos, total


async def seccion_fuera(args, token):
    print("\n2. Frases fuera de la gramatica")
    print("--------------------------------")
    comandos = {normalizar(f) for f in cargar_comandos()}
    senuelos = {normalizar(f) for f in cargar_senuelos()}
    falso_positivo = []
    for indice, frase in enumerate(FRASES_FUERA, 1):
        pcm = sintetizar_frase(args, frase)
        ws, _ = await abrir_conexion(args.host, args.puerto, token)
        final = await decir_frase(ws, "fuera-%03d" % indice, pcm,
                                  ritmo=not args.sin_ritmo)
        await ws.close()
        texto = final.get("texto", "") if final.get("tipo") == "final" else ""
        normal = normalizar(texto)
        if normal in comandos:
            falso_positivo.append((frase, texto))
            marca = "COMMAND"
        elif normal in senuelos:
            marca = "señuelo"
        elif normal in ("", "[unk]"):
            marca = "[unk] "
        else:
            marca = "otro  "
        print("  [%s] %-32s -> %r" % (marca, frase, texto))
    print("\n  fuera de gramatica que se colaron como comando: %d/%d"
          % (len(falso_positivo), len(FRASES_FUERA)))
    return falso_positivo


async def seccion_errores(args, token):
    print("\n3. Errores del protocolo")
    print("------------------------")
    url = "ws://%s:%d" % (args.host, args.puerto)

    # Token invalido -> error token_invalido y cierre 4401.
    ws = await connect(url, max_size=1 << 20)
    await ws.send(json.dumps({"tipo": "inicio", "token": "token-que-no-es"}))
    codigo_cierre = None
    error = None
    try:
        error = json.loads(await ws.recv())
    except ConnectionClosed:
        pass
    try:
        await ws.recv()
    except ConnectionClosed as cierre:
        codigo_cierre = cierre.rcvd.code if cierre.rcvd else None
    await ws.close()
    ok = (isinstance(error, dict) and error.get("codigo") == "token_invalido"
          and codigo_cierre == 4401)
    print("  token invalido: %s (codigo %s)" % ("OK" if ok else "MAL", codigo_cierre))
    if not ok:
        _FALLOS.append("token_invalido(%r,%s)" % (error, codigo_cierre))

    # Audio antes del inicio -> error sin_inicio y cierre 4400.
    ws = await connect(url, max_size=1 << 20)
    await ws.send(b"\x00" * TRAMA)
    error = None
    codigo_cierre = None
    try:
        error = json.loads(await ws.recv())
    except ConnectionClosed:
        pass
    try:
        await ws.recv()
    except ConnectionClosed as cierre:
        codigo_cierre = cierre.rcvd.code if cierre.rcvd else None
    await ws.close()
    ok = (isinstance(error, dict) and error.get("codigo") == "sin_inicio"
          and codigo_cierre == 4400)
    print("  audio antes del inicio: %s (codigo %s)" % ("OK" if ok else "MAL", codigo_cierre))
    if not ok:
        _FALLOS.append("sin_inicio(%r,%s)" % (error, codigo_cierre))

    # Trama impar -> formato_audio.
    ws, _ = await abrir_conexion(args.host, args.puerto, token)
    await ws.send(json.dumps({"tipo": "frase", "frase_id": "mala-1",
                              "modo": "comandos"}))
    await ws.send(b"\x00" * (TRAMA + 1))   # impar: 3201
    error = json.loads(await ws.recv())
    ok = isinstance(error, dict) and error.get("codigo") == "formato_audio"
    print("  trama impar: %s (%s)" % ("OK" if ok else "MAL", error.get("codigo")))
    if not ok:
        _FALLOS.append("formato_audio-impar(%r)" % error)

    # Trama de mas de 3200 B -> formato_audio.
    await ws.send(json.dumps({"tipo": "frase", "frase_id": "mala-2",
                              "modo": "comandos"}))
    await ws.send(b"\x00" * (TRAMA + 2))
    error = json.loads(await ws.recv())
    ok = isinstance(error, dict) and error.get("codigo") == "formato_audio"
    print("  trama mayor de 3200 B: %s (%s)"
          % ("OK" if ok else "MAL", error.get("codigo")))
    if not ok:
        _FALLOS.append("formato_audio-grande(%r)" % error)
    await ws.close()

    # Frase de mas de 10 s -> frase_larga. Se manda sin ritmo: 101 tramas.
    ws, _ = await abrir_conexion(args.host, args.puerto, token)
    await ws.send(json.dumps({"tipo": "frase", "frase_id": "larga-1",
                              "modo": "comandos"}))
    for _ in range(101):
        await ws.send(b"\x00" * TRAMA)
    error = json.loads(await ws.recv())
    ok = isinstance(error, dict) and error.get("codigo") == "frase_larga"
    print("  frase de mas de 10 s: %s (%s)" % ("OK" if ok else "MAL", error.get("codigo")))
    if not ok:
        _FALLOS.append("frase_larga(%r)" % error)
    await ws.close()

    # Inactividad: una frase abierta y 3.5 s sin nada.
    ws, _ = await abrir_conexion(args.host, args.puerto, token)
    await ws.send(json.dumps({"tipo": "frase", "frase_id": "quieta-1",
                              "modo": "comandos"}))
    await asyncio.sleep(3.5)
    error = json.loads(await ws.recv())
    ok = isinstance(error, dict) and error.get("codigo") == "inactividad"
    print("  inactividad: %s (%s)" % ("OK" if ok else "MAL", error.get("codigo")))
    if not ok:
        _FALLOS.append("inactividad(%r)" % error)
    await ws.close()

    return not _FALLOS


async def seccion_corte(args, token, observador):
    print("\n4. Corte a mitad de frase")
    print("-------------------------")
    # Se empieza a decir una frase y se corta la conexion sin mandar `fin`. El
    # servicio NO puede publicar nada: media frase publicada es peor que ninguna,
    # porque el orquestador la tomaria por una orden completa.
    pcm = sintetizar_frase(args, "prende la luz de la sala")
    if observador:
        observador.limpiar()
    ws, _ = await abrir_conexion(args.host, args.puerto, token)
    await ws.send(json.dumps({"tipo": "frase", "frase_id": "cortada-1",
                              "modo": "comandos"}))
    for trama in trocear(pcm)[:5]:
        await ws.send(trama)
        await asyncio.sleep(0.1)
    await ws.close()

    publicado = None
    if observador:
        publicado = observador.esperar(
            "casa/sala/voz/texto",
            lambda c: isinstance(c, dict) and c.get("frase_id") == "cortada-1",
            timeout=2.0,
        )
    print("  tras cortar la conexion a mitad: %s"
          % ("MAL, se publico algo" if publicado else "OK, no se publico nada"))
    if publicado:
        _FALLOS.append("corte-publico(%r)" % (publicado,))
    return publicado is None


# --- Programa ---------------------------------------------------------------

async def principal(args):
    token = leer_token(args.token_archivo)
    if not token:
        print("No hay token en %s" % args.token_archivo)
        return 2

    observador = None
    if not args.sin_mqtt:
        password = args.mqtt_password or leer_password_env("MQTT_PUENTE_PASSWORD")
        if not password:
            print("Sin contrasena MQTT (MQTT_PUENTE_PASSWORD); sigo sin observador.")
        else:
            observador = Observador(args.mqtt_host, args.mqtt_puerto,
                                    args.mqtt_usuario, password)

    try:
        if args.solo in ("todo", "aciertos"):
            await seccion_aciertos(args, token, observador)
        if args.solo in ("todo", "fuera"):
            await seccion_fuera(args, token)
        if args.solo in ("todo", "errores"):
            await seccion_errores(args, token)
        if args.solo in ("todo", "corte"):
            await seccion_corte(args, token, observador)
    finally:
        if observador:
            observador.cerrar()

    print("\n" + "=" * 60)
    if _FALLOS:
        print("fallos de protocolo: %d" % len(_FALLOS))
        for fallo in _FALLOS:
            print("  - " + str(fallo))
        return 1
    print("sin fallos de protocolo")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Arnes de pruebas de voz (etapa A).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--puerto", type=int, default=2700)
    parser.add_argument("--token-archivo", default=TOKEN_POR_DEFECTO)
    parser.add_argument("--piper-dir", default=PIPER_DIR_POR_DEFECTO)
    parser.add_argument("--voz", default=None,
                        help="ruta del .onnx (def.: es_MX-ald-medium de herramientas/)")
    parser.add_argument("--solo", choices=["todo", "aciertos", "fuera", "errores", "corte"],
                        default="todo")
    parser.add_argument("--limite", type=int, default=0,
                        help="limita cuantas frases de la gramatica se prueban")
    parser.add_argument("--largo", type=float, default=None,
                        help="length_scale de Piper: >1 habla mas despacio (def. 1.0)")
    parser.add_argument("--sentence-silence", type=float, default=None,
                        help="sentence_silence de Piper (def.: el de Piper)")
    parser.add_argument("--noise-scale", type=float, default=None,
                        help="noise_scale de Piper (def.: el de Piper)")
    parser.add_argument("--noise-w", type=float, default=None,
                        help="noise_w de Piper (def.: el de Piper)")
    parser.add_argument("--config-voz", default=None,
                        help="config.toml del satelite: toma de su [voz] el modelo y "
                             "los parametros que no se den por argumento")
    parser.add_argument("--sin-ritmo", action="store_true",
                        help="manda el audio sin esperar 100 ms entre tramas")
    parser.add_argument("--sin-mqtt", action="store_true",
                        help="no abre el observador de MQTT (sin eco ni latencias)")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-puerto", type=int, default=1883)
    parser.add_argument("--mqtt-usuario", default="puente")
    parser.add_argument("--mqtt-password", default="")
    args = parser.parse_args()
    resolver_voz(args)
    return asyncio.run(principal(args))


if __name__ == "__main__":
    sys.exit(main())
