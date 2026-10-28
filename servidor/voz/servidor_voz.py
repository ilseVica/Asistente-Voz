#!/usr/bin/env python3
"""Servicio de reconocimiento de voz (Vosk) por WebSocket.

Etapa A del Paso 2 del proyecto Asistente de Voz. Este servicio hace UNA sola
cosa: recibe audio por WebSocket, lo pasa por Vosk y deja el texto reconocido en
MQTT. NO resuelve intenciones; eso es del orquestador (Paso 3).

Decisiones que se ven en este archivo y no en otro sitio:

- El modelo se carga UNA vez al arrancar, en `main()`, y se comparte entre todas
  las conexiones. Cargarlo por conexion son cientos de MB y varios segundos por
  cada frase, que es justo lo que no se puede permitir.

- El reconocedor (`KaldiRecognizer`) SI se crea por frase. Es barato comparado con
  el modelo, y es lo que permite que cada frase empiece en limpio, sin arrastrar
  el estado de la anterior.

- Vosk es CPU y bloquea: cada `AcceptWaveform` se ejecuta en un hilo aparte con
  `asyncio.to_thread`, para no congelar el bucle de eventos mientras dura. Con
  tramas de 100 ms y el modelo small, cada llamada dura una fraccion de eso, pero
  el bucle tiene que seguir atendiendo el resto de conexiones y el temporizador de
  inactividad.

- No hay resultados parciales: no se llama a `Result()` para mandar nada al
  cliente. Vosk puede cerrar una frase por su cuenta (endpointing) y entonces el
  texto de ese tramo se acumula para no perderlo, pero al satelite solo se le manda
  el mensaje `final`, una vez.

- El audio no se escribe en disco en ningun momento: las tramas van del WebSocket
  al reconocedor y se descartan.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import signal
import sys
import time
from contextlib import contextmanager

import paho.mqtt.client as mqtt
import vosk
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

# --- Configuracion por entorno ---------------------------------------------

PUERTO = int(os.environ.get("VOZ_PUERTO", "2700"))

# Direcciones donde escuchar. Por defecto las tres del sistema, igual que el
# broker: loopback, la red de la casa y Tailscale. Nunca 0.0.0.0: si se agrega
# otra interfaz a la VM, no queda expuesta por accidente. Se abre un `serve()`
# por direccion porque un servidor de websockets escucha en una sola.
DIRECCIONES = (
    os.environ.get("VOZ_DIRECCIONES", "").replace(",", " ").split()
    # Si la variable no viene o viene vacia, las tres del sistema. Una cadena
    # vacia no puede dejar el servicio sin escuchar en ningun sitio: eso seria un
    # arranque que parece correcto y no acepta una sola conexion.
    or ["127.0.0.1", "192.168.1.169", "100.116.194.116"]
)

MODELO = os.environ.get("VOZ_MODELO", "/modelo/vosk-model-small-es-0.42")
GRAMATICA = os.environ.get("VOZ_GRAMATICA", "/app/gramatica.json")

# El hash SHA-256 (hex, minusculas) del token del satelite. En el .env va SOLO el
# hash; el token en claro lo guarda quien configura el satelite. La comparacion
# usa `hmac.compare_digest` para no filtrar por tiempo de respuesta si el token
# se parece o no.
TOKEN_SHA256 = os.environ.get("VOZ_TOKEN_SHA256", "").strip().lower()

MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PUERTO = int(os.environ.get("MQTT_PUERTO", "1883"))
MQTT_USUARIO = os.environ.get("MQTT_USUARIO", "voz")
MQTT_PASSWORD = os.environ.get("MQTT_VOZ_PASSWORD", "")
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "voz")

TOPICO_TEXTO = "casa/sala/voz/texto"
QOS = 1

# --- Limites del protocolo --------------------------------------------------

LARGO_TRAMA = 3200              # 100 ms de PCM 16 kHz mono 16 bits.
MAX_BYTES_FRASE = LARGO_TRAMA * 100  # 10 s de audio.
SEGUNDOS_INACTIVIDAD = 3        # Sin tramas dentro de una frase.
CIERRE_TOKEN_INVALIDO = 4401
CIERRE_SIN_INICIO = 4400

registro = logging.getLogger("voz")


def ahora_ms() -> int:
    """Epoch en milisegundos con el reloj del servidor."""
    return int(time.time() * 1000)


# --- Captura de avisos de vocabulario --------------------------------------

@contextmanager
def _stderr_capturado():
    """Redirige el stderr de bajo nivel (fd 2) a un pipe y lo devuelve.

    Vosk escribe sus avisos desde C directamente al descriptor 2, asi que
    `contextlib.redirect_stderr` no los ve. Hay que actuar sobre el fd. Se usa
    solo un instante, al cargar la gramatica, para recoger los avisos de
    «Ignoring word missing in vocabulary».
    """
    lectura, escritura = os.pipe()
    original = os.dup(2)
    try:
        os.dup2(escritura, 2)
        os.close(escritura)
        yield lectura
    finally:
        os.dup2(original, 2)
        os.close(original)


def _leer_pipe(fd: int) -> str:
    trozos = []
    try:
        while True:
            datos = os.read(fd, 4096)
            if not datos:
                break
            trozos.append(datos)
    except OSError:
        pass
    return b"".join(trozos).decode("utf-8", "replace")


def cargar_gramatica(modelo: vosk.Model) -> tuple:
    """Lee el JSON de la gramatica, crea un reconocedor de prueba y recoge avisos.

    Devuelve `(frases, advertencias)`. Las advertencias son las palabras que el
    modelo no conoce; NO se cambian, se reportan. Es lo que pide el contrato:
    Vosk ignora esas palabras en silencio y el resultado seria una frase distinta
    de la esperada sin que nadie lo note.
    """
    with open(GRAMATICA, encoding="utf-8") as archivo:
        configuracion = json.load(archivo)

    frases = []
    for lista in configuracion.get("intenciones", {}).values():
        frases.extend(lista)
    unk = configuracion.get("unk", "[unk]")
    if unk and unk not in frases:
        frases.append(unk)

    if not frases:
        raise SystemExit("la gramatica esta vacia: revisa %s" % GRAMATICA)

    # Se crea un reconocedor para que Vosk consuma la gramatica y suelte los
    # avisos; ese mismo JSON es el que usaran los reconocedores de cada frase.
    gramatica_json = json.dumps(frases, ensure_ascii=False)
    with _stderr_capturado() as lectura:
        vosk.KaldiRecognizer(modelo, 16000, gramatica_json)
    salida = _leer_pipe(lectura)

    advertencias = []
    for linea in salida.splitlines():
        if "missing in vocabulary" in linea:
            # La forma es:
            #   WARNING (VoskAPI:UpdateGrammarFst():recognizer.cc:308) Ignoring
            #   word missing in vocabulary: 'palabra'
            # Vosk ignora la palabra en SILENCIO en el reconocimiento: la unica
            # senal es esta linea, y solo si el modelo no la conoce.
            palabra = linea.split("missing in vocabulary", 1)[1]
            advertencias.append(palabra.strip().lstrip(":").strip().strip("'\""))

    return frases, advertencias


def _json_gramatica(frases) -> str:
    return json.dumps(frases, ensure_ascii=False)

def crear_reconocedores(modelo: vosk.Model, gramatica_json: str):
    """Crea los dos reconocedores (gramatica y libre) sobre el mismo modelo."""
    return (vosk.KaldiRecognizer(modelo, 16000, gramatica_json),
            vosk.KaldiRecognizer(modelo, 16000))


def final_libre(reconocedor) -> str:
    """Texto final del reconocedor libre, usando la funcion real `_final`."""
    return _final(reconocedor)


# --- MQTT -------------------------------------------------------------------

class Publicador:
    """El cliente MQTT del servicio: solo escribe en casa/sala/voz/texto."""

    def __init__(self):
        if not MQTT_PASSWORD:
            raise RuntimeError(
                "FALTA MQTT_VOZ_PASSWORD en el entorno. Sin ella el servicio no "
                "puede publicar la frase reconocida."
            )
        self.cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=MQTT_CLIENT_ID,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self.cliente.username_pw_set(MQTT_USUARIO, MQTT_PASSWORD)
        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        self.conectado = False
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_disconnect = self._al_desconectar

    def _al_conectar(self, cliente, usuario, banderas, codigo, propiedades=None):
        if codigo.is_failure:
            registro.error("conexion MQTT rechazada (rc=%s)", codigo)
            self.conectado = False
            return
        self.conectado = True
        registro.info("MQTT conectado como %s en %s:%d", MQTT_USUARIO,
                      MQTT_HOST, MQTT_PUERTO)

    def _al_desconectar(self, cliente, usuario, banderas, codigo, propiedades=None):
        self.conectado = False
        if codigo.is_failure:
            registro.warning("MQTT desconectado (rc=%s); reintentando", codigo)

    def conectar(self):
        self.cliente.connect(MQTT_HOST, MQTT_PUERTO, keepalive=60)
        self.cliente.loop_start()

    def cerrar(self):
        try:
            self.cliente.disconnect()
            self.cliente.loop_stop()
        except Exception:  # noqa: BLE001
            pass

    def publicar(self, carga: dict) -> None:
        cuerpo = json.dumps(carga, separators=(",", ":"), ensure_ascii=False)
        resultado = self.cliente.publish(TOPICO_TEXTO, cuerpo, qos=QOS,
                                         retain=False)
        resultado.wait_for_publish(timeout=5)


# --- El WebSocket ------------------------------------------------------------

class Frase:
    """El estado de una frase en curso."""

    __slots__ = ("id", "modo", "reconocedor", "reconocedor_libre", "textos",
                 "textos_libres", "bytes", "primera_ms", "ultima_ms")

    def __init__(self, frase_id, modo, reconocedor, reconocedor_libre):
        self.id = frase_id
        self.modo = modo
        self.reconocedor = reconocedor
        self.reconocedor_libre = reconocedor_libre
        self.textos = []
        self.textos_libres = []
        self.bytes = 0
        self.primera_ms = None
        self.ultima_ms = None


def token_valido(token: str) -> bool:
    if not TOKEN_SHA256:
        return False
    calculado = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    return hmac.compare_digest(calculado, TOKEN_SHA256)


async def enviar_error(websocket, codigo, frase_id=None, detalle=""):
    mensaje = {
        "tipo": "error",
        "codigo": codigo,
        "frase_id": frase_id,
        "detalle": detalle,
    }
    try:
        await websocket.send(json.dumps(mensaje, ensure_ascii=False))
    except ConnectionClosed:
        pass


def _aceptar(reconocedor, datos: bytes) -> str:
    """Alimenta una trama y devuelve el texto de un cierre por endpointing.

    Vosk puede cerrar una frase por su cuenta si detecta una pausa; en ese caso
    `AcceptWaveform` devuelve True y hay que recoger `Result()` para no perder lo
    dicho. En modo comandos esto casi no pasa, pero perder la primera palabra de
    una frase seria un fallo silencioso, que es el peor de todos.
    """
    if reconocedor.AcceptWaveform(datos):
        return json.loads(reconocedor.Result()).get("text", "")
    return ""


def _final(reconocedor) -> str:
    return json.loads(reconocedor.FinalResult()).get("text", "")


async def _resolver_frase(websocket, frase: Frase, motivo_error: str,
                          fin_ms: int, publicador: Publicador):
    """Cierra una frase: reconoce, manda `final` y publica en MQTT."""
    if frase.id is None:
        return
    # Si la frase se descarta (error o corte), no se reconoce ni se publica nada.
    if motivo_error:
        return

    texto_seg = await asyncio.to_thread(_final, frase.reconocedor)
    if texto_seg:
        frase.textos.append(texto_seg)
    texto_libre_seg = await asyncio.to_thread(_final, frase.reconocedor_libre)
    if texto_libre_seg:
        frase.textos_libres.append(texto_libre_seg)
    final_ms = ahora_ms()
    texto = " ".join(t for t in frase.textos if t).strip()
    texto_libre = " ".join(t for t in frase.textos_libres if t).strip()

    marcas = {
        "primera_trama_ms": frase.primera_ms,
        "ultima_trama_ms": frase.ultima_ms,
        "fin_ms": fin_ms,
        "final_ms": final_ms,
        "publicado_ms": None,
    }

    try:
        await websocket.send(json.dumps({
            "tipo": "final",
            "frase_id": frase.id,
            "texto": texto,
            "texto_libre": texto_libre,
            "modo": frase.modo,
            "marcas": marcas,
        }, ensure_ascii=False))
    except ConnectionClosed:
        # Si el satelite se cayo justo al terminar, la frase no se publica: se
        # registra y se descarta, que es lo mismo que hace un corte a mitad.
        registro.warning("frase %s: el satelite se desconecto antes del final; "
                         "no se publica", frase.id)
        return

    # La marca de publicacion va ANTES de llamar a paho: el cuerpo se serializa
    # dentro de `publicar` y, aun con QoS 1, el mensaje ya no se puede reescribir
    # despues de mandarlo. El momento exacto del PUBACK seria la medicion mas
    # fiel, pero aqui no hay forma de meterlo en un cuerpo ya serializado; la
    # diferencia es de milisegundos y todas las marcas son del mismo reloj.
    marcas["publicado_ms"] = ahora_ms()
    carga = {
        "frase_id": frase.id,
        "texto": texto,
        "texto_libre": texto_libre,
        "modo": frase.modo,
        "ts": int(time.time()),
        "marcas": marcas,
    }
    try:
        await asyncio.to_thread(publicador.publicar, carga)
        registro.info("frase %s [%s] -> %r (fin->final %d ms, fin->publicado %d ms)",
                      frase.id, frase.modo, texto,
                      final_ms - fin_ms, marcas["publicado_ms"] - fin_ms)
    except Exception as error:  # noqa: BLE001
        registro.error("frase %s: no se pudo publicar: %s", frase.id, error)


async def atender(websocket, modelo: vosk.Model, fases, publicador: Publicador):
    """Atiende una conexion persistente: un inicio y, despues, muchas frases."""
    registro.info("conexion de %s", websocket.remote_address)
    autenticado = False
    frase = None
    gramatica_json = _json_gramatica(fases)

    try:
        while True:
            esperando = SEGUNDOS_INACTIVIDAD if frase is not None else None
            try:
                if esperando is None:
                    mensaje = await websocket.recv()
                else:
                    mensaje = await asyncio.wait_for(websocket.recv(),
                                                     timeout=esperando)
            except asyncio.TimeoutError:
                await enviar_error(websocket, "inactividad", frase.id,
                                   "mas de %d s sin tramas" % SEGUNDOS_INACTIVIDAD)
                registro.warning("frase %s descartada por inactividad", frase.id)
                frase = None
                continue
            except ConnectionClosed:
                if frase is not None:
                    registro.warning("frase %s descartada: conexion caida a mitad",
                                     frase.id)
                break

            # --- Tramas binarias de audio ---
            if isinstance(mensaje, (bytes, bytearray)):
                if not autenticado:
                    await enviar_error(websocket, "sin_inicio", None,
                                       "audio antes del inicio")
                    await websocket.close(code=CIERRE_SIN_INICIO)
                    return
                if frase is None:
                    registro.warning("audio sin frase en curso; se ignora")
                    continue
                if len(mensaje) % 2 != 0 or len(mensaje) > LARGO_TRAMA:
                    await enviar_error(websocket, "formato_audio", frase.id,
                                       "trama de %d bytes" % len(mensaje))
                    registro.warning("frase %s descartada por formato_audio "
                                     "(%d bytes)", frase.id, len(mensaje))
                    frase = None
                    continue
                if frase.primera_ms is None:
                    frase.primera_ms = ahora_ms()
                frase.ultima_ms = ahora_ms()
                frase.bytes += len(mensaje)
                if frase.bytes > MAX_BYTES_FRASE:
                    await enviar_error(websocket, "frase_larga", frase.id,
                                       "mas de 10 s de audio")
                    registro.warning("frase %s descartada por frase_larga", frase.id)
                    frase = None
                    continue
                texto_seg = await asyncio.to_thread(_aceptar, frase.reconocedor,
                                                    bytes(mensaje))
                if texto_seg:
                    frase.textos.append(texto_seg)
                texto_libre_seg = await asyncio.to_thread(
                    _aceptar, frase.reconocedor_libre, bytes(mensaje))
                if texto_libre_seg:
                    frase.textos_libres.append(texto_libre_seg)
                continue

            # --- Mensajes de texto (JSON) ---
            try:
                datos = json.loads(mensaje)
            except (ValueError, TypeError):
                await enviar_error(websocket, "mensaje_invalido", None,
                                   "no es JSON")
                continue
            if not isinstance(datos, dict):
                await enviar_error(websocket, "mensaje_invalido", None,
                                   "no es un objeto")
                continue

            tipo = datos.get("tipo")

            if not autenticado:
                if tipo != "inicio":
                    await enviar_error(websocket, "sin_inicio", None,
                                       "el primer mensaje no es inicio")
                    await websocket.close(code=CIERRE_SIN_INICIO)
                    return
                if not token_valido(datos.get("token", "")):
                    await enviar_error(websocket, "token_invalido", None,
                                       "token no valido")
                    await websocket.close(code=CIERRE_TOKEN_INVALIDO)
                    return
                autenticado = True
                await websocket.send(json.dumps({"tipo": "listo"}))
                registro.info("cliente autenticado y listo")
                continue

            if tipo == "frase":
                if frase is not None:
                    await enviar_error(websocket, "frase_en_curso", frase.id,
                                       "ya hay una frase abierta")
                    continue
                modo = datos.get("modo", "comandos")
                frase_id = datos.get("frase_id")
                if not frase_id:
                    await enviar_error(websocket, "mensaje_invalido", None,
                                       "frase sin frase_id")
                    continue
                if modo == "chat":
                    await enviar_error(websocket, "modo_chat_no_disponible",
                                       frase_id,
                                       "el modo chat aun no esta implementado")
                    continue
                if modo != "comandos":
                    await enviar_error(websocket, "modo_desconocido", frase_id,
                                       "modo %r no soportado" % modo)
                    continue
                reconocedor = vosk.KaldiRecognizer(modelo, 16000, gramatica_json)
                reconocedor_libre = vosk.KaldiRecognizer(modelo, 16000)
                frase = Frase(frase_id, modo, reconocedor, reconocedor_libre)
                continue

            if tipo == "fin" or datos.get("fin") is True:
                if frase is None:
                    registro.warning("fin sin frase en curso; se ignora")
                    continue
                fin_ms = ahora_ms()
                en_curso = frase
                frase = None
                await _resolver_frase(websocket, en_curso, None, fin_ms,
                                      publicador)
                continue

            await enviar_error(websocket, "mensaje_invalido", None,
                               "tipo %r desconocido" % tipo)

    except ConnectionClosed:
        pass
    finally:
        if frase is not None:
            registro.warning("frase %s descartada al cerrar la conexion", frase.id)
        registro.info("conexion de %s cerrada", websocket.remote_address)


async def servir(modelo: vosk.Model, fases, publicador: Publicador) -> list:
    servidores = []
    for direccion in DIRECCIONES:
        try:
            servidor = await serve(
                lambda ws: atender(ws, modelo, fases, publicador),
                direccion,
                PUERTO,
                # Una trama de mas de esto la corta la libreria antes de que la
                # veamos. Con 1 MiB, una trama de mas de 3200 B llega igual a
                # `atender` y sale como `formato_audio`, que es lo que dice el
                # contrato; solo se descarta por la via dura lo absurdo.
                max_size=1 << 20,
            )
            servidores.append(servidor)
            registro.info("escuchando en %s:%d", direccion, PUERTO)
        except OSError as error:
            registro.error("no se pudo escuchar en %s:%d: %s",
                           direccion, PUERTO, error)
    if not servidores:
        raise SystemExit("no se pudo abrir ningun socket de %d" % PUERTO)
    return servidores


def configurar_log() -> None:
    niveles = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "AVISO": logging.WARNING,
               "WARNING": logging.WARNING, "ERROR": logging.ERROR}
    nivel = niveles.get(os.environ.get("NIVEL_LOG", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=nivel,
                        format="%(asctime)s %(levelname)-7s %(name)-6s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("paho").setLevel(max(nivel, logging.WARNING))


async def amain() -> int:
    configurar_log()
    # Vosk escribe sus progresos de carga en stderr desde C. Se silencian, y NO se
    # pierde el aviso de vocabulario: los WARNING de Vosk se emiten igual con el
    # nivel a -1 (verificado), asi que la gramatica se sigue vigilando.
    vosk.SetLogLevel(-1)
    registro.info("cargando modelo %s", MODELO)
    t0 = time.time()
    modelo = vosk.Model(MODELO)
    registro.info("modelo cargado en %.1f s", time.time() - t0)

    fases, advertencias = cargar_gramatica(modelo)
    registro.info("gramatica cargada: %d frases", len(fases))
    for palabra in advertencias:
        registro.warning("vocabulario: el modelo no conoce %r; propuesta: "
                         "sustituirla por una palabra conocida en gramatica.json "
                         "(no se cambia sola)", palabra)
    if advertencias:
        registro.warning("hay %d palabra(s) de la gramatica que el modelo ignora: "
                         "esas frases NO se reconoceran como se espera",
                         len(advertencias))

    if not TOKEN_SHA256:
        raise SystemExit("FALTA VOZ_TOKEN_SHA256 en el entorno")

    publicador = Publicador()
    publicador.conectar()

    servidores = await servir(modelo, fases, publicador)

    parando = asyncio.Event()
    bucle = asyncio.get_running_loop()
    for senal in (signal.SIGTERM, signal.SIGINT):
        try:
            bucle.add_signal_handler(senal, parando.set)
        except NotImplementedError:
            pass
    registro.info("servicio de voz listo")
    await parando.wait()

    registro.info("parando...")
    for servidor in servidores:
        servidor.close()
    for servidor in servidores:
        await servidor.wait_closed()
    publicador.cerrar()
    registro.info("servicio de voz detenido")
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
