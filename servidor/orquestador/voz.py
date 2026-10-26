"""Intenciones de voz del orquestador (Paso 3).

Lee el texto reconocido por el servicio de voz, lo clasifica contra la gramatica
congelada (servidor/voz/gramatica.json, fuente unica) y ejecuta la accion:
publica en luz/set o alerta/ayuda, responde por satelite/decir y anota el turno.

Coincidencia exacta tras normalizar (minusculas, sin tildes, espacios simples).
No hay coincidencia aproximada.
"""

import json
import logging
import os
import re
import threading
import time
import unicodedata

import config
import textos

registro = logging.getLogger("voz")

UNK = "[unk]"
CONTADOR_AYUDA = [0]

# Estado en memoria que actualizan los mensajes MQTT (orquestador.enrutar).
_estado_luz = {"valor": None}   # "on" | "off" | None
_nodo_conectado = {"valor": None}  # True | False | None

_frase_a_intencion = {}


def _ruta_gramatica() -> str:
    candidatas = []
    env = os.environ.get("GRAMATICA_JSON")
    if env:
        return os.path.abspath(env)
    candidatas.append("/app/gramatica.json")
    # Desarrollo: el archivo del repo.
    base = os.path.dirname(os.path.abspath(__file__))
    candidatas.append(os.path.join(base, "..", "voz", "gramatica.json"))
    candidatas.append(os.path.join(base, "..", "..", "servidor", "voz", "gramatica.json"))
    for ruta in candidatas:
        if ruta and os.path.isfile(ruta):
            return os.path.abspath(ruta)
    return ""


def _cargar() -> dict:
    ruta = _ruta_gramatica()
    if not ruta:
        registro.warning("voz: no se encontro gramatica.json; sin intenciones")
        return {}
    try:
        with open(ruta, encoding="utf-8") as archivo:
            configuracion = json.load(archivo)
    except (OSError, ValueError) as error:
        registro.warning("voz: no se pudo leer %s: %s", ruta, error)
        return {}
    if not isinstance(configuracion, dict):
        registro.warning("voz: gramatica.json no es un objeto")
        return {}
    intenciones = configuracion.get("intenciones")
    if not isinstance(intenciones, dict) or not intenciones:
        registro.warning("voz: gramatica.json sin intenciones")
        return {}
    mapa = {}
    for intencion, frases in intenciones.items():
        if not isinstance(frases, list):
            continue
        # Las frases SENUELO viven en la gramatica para que Vosk tenga donde
        # mandar el audio parecido, pero no son intenciones: el orquestador las
        # trata como NO_ENTENDI (no se anaden al mapa).
        if intencion == "SENUELO":
            continue
        for frase in frases:
            if isinstance(frase, str) and frase.strip():
                mapa[normalizar(frase)] = intencion
    registro.info("voz: gramatica cargada desde %s (%d frases)", ruta, len(mapa))
    return mapa


def normalizar(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def no_entendido(texto) -> bool:
    if not isinstance(texto, str):
        return True
    if texto.strip() == "":
        return True
    if UNK in texto:
        return True
    return False


def actualizar_estado_luz(cuerpo: str) -> None:
    try:
        carga = json.loads(cuerpo)
    except (TypeError, ValueError):
        return
    if isinstance(carga, dict):
        estado = carga.get("estado")
        if estado in ("on", "off"):
            _estado_luz["valor"] = estado


def actualizar_nodo(cuerpo: str) -> None:
    try:
        carga = json.loads(cuerpo)
    except (TypeError, ValueError):
        return
    if isinstance(carga, dict):
        if carga.get("conectado") is True:
            _nodo_conectado["valor"] = True
        elif carga.get("conectado") is False:
            _nodo_conectado["valor"] = False


def _publicar_respuesta(publicar, texto, frase_id=None):
    salida = {"texto": texto, "tipo": "respuesta", "ts": int(time.time())}
    if isinstance(frase_id, str) and frase_id:
        salida["frase_id"] = frase_id
    publicar(config.TOPICO_SATELITE_DECIR, salida, qos=1)


def _confirmar_luz(estado_pedido, publicar, frase_id):
    limite = time.time() + 3.0
    while time.time() < limite:
        if _estado_luz["valor"] == estado_pedido:
            return
        time.sleep(0.1)
    if _estado_luz["valor"] != estado_pedido:
        texto = (textos.NO_PUDO_ENCENDER_LUZ if estado_pedido == "on"
                 else textos.NO_PUDO_APAGAR_LUZ)
        _publicar_respuesta(publicar, texto, frase_id)
        registro.warning("voz: luz no confirmo %s en 3 s", estado_pedido)


def procesar(cuerpo: str, publicar, base=None) -> bool:
    try:
        carga = json.loads(cuerpo)
    except ValueError as error:
        registro.warning("texto de voz que no es JSON: %s", error)
        return False
    if not isinstance(carga, dict):
        registro.warning("texto de voz que no es un objeto JSON: %s",
                         type(carga).__name__)
        return False

    ts = carga.get("ts")
    if ts is None or not isinstance(ts, (int, float)):
        registro.warning("voz: mensaje sin ts, descartado")
        return False
    now = int(time.time())
    if abs(now - int(ts)) > 10:
        registro.warning("voz: mensaje antiguo (ts=%s, ahora=%s), descartado",
                         ts, now)
        return False

    texto = carga.get("texto", "")
    frase_id = carga.get("frase_id")

    if no_entendido(texto):
        _publicar_respuesta(publicar, textos.NO_ENTENDIDO, frase_id)
        registro.info("voz turno: texto=%r intencion=NO_ENTENDI accion=no_entendido "
                      "latencia_ms=%d", texto, int((time.time() - int(ts)) * 1000))
        return True

    intencion = _frase_a_intencion.get(normalizar(texto))
    if intencion is None:
        _publicar_respuesta(publicar, textos.NO_ENTENDIDO, frase_id)
        registro.info("voz turno: texto=%r intencion=NO_ENTENDI accion=no_entendido "
                      "latencia_ms=%d", texto, int((time.time() - int(ts)) * 1000))
        return True

    latencia = int((time.time() - int(ts)) * 1000)

    if intencion in ("ENCENDER_LUZ", "APAGAR_LUZ"):
        estado_pedido = "on" if intencion == "ENCENDER_LUZ" else "off"
        if _nodo_conectado["valor"] is False:
            texto_resp = (textos.NO_PUDO_ENCENDER_LUZ if estado_pedido == "on"
                          else textos.NO_PUDO_APAGAR_LUZ)
            _publicar_respuesta(publicar, texto_resp, frase_id)
            registro.info("voz turno: texto=%r intencion=%s accion=nodo_desconectado "
                          "latencia_ms=%d", texto, intencion, latencia)
            return True
        if _estado_luz["valor"] == estado_pedido:
            texto_resp = (textos.LUZ_YA_ENCENDIDA if estado_pedido == "on"
                          else textos.LUZ_YA_APAGADA)
            _publicar_respuesta(publicar, texto_resp, frase_id)
            registro.info("voz turno: texto=%r intencion=%s accion=ya_estaba "
                          "latencia_ms=%d", texto, intencion, latencia)
            return True
        publicar(config.TOPICO_LUZ_SET,
                 {"estado": estado_pedido, "origen": "voz",
                  "ts": int(time.time())},
                 qos=1)
        texto_resp = (textos.RESPUESTA_ENCENDER_LUZ if estado_pedido == "on"
                      else textos.RESPUESTA_APAGAR_LUZ)
        _publicar_respuesta(publicar, texto_resp, frase_id)
        threading.Thread(target=_confirmar_luz,
                         args=(estado_pedido, publicar, frase_id),
                         daemon=True).start()
        registro.info("voz turno: texto=%r intencion=%s accion=luz_set_%s "
                      "latencia_ms=%d", texto, intencion, estado_pedido, latencia)
        return True

    if intencion == "AYUDA":
        if base is not None and hasattr(base, "hay_ayuda_reciente") \
                and base.hay_ayuda_reciente():
            _publicar_respuesta(publicar, textos.RESPUESTA_AYUDA_RECIENTE, frase_id)
            registro.info("voz turno: texto=%r intencion=AYUDA accion=ayuda_antirrebote "
                          "latencia_ms=%d", texto, latencia)
            return True
        CONTADOR_AYUDA[0] += 1
        id_alerta = "srv-%08x-%04x" % (int(time.time()) % 0xFFFFFFFF,
                                       CONTADOR_AYUDA[0])
        publicar(config.TOPICO_ALERTA_AYUDA,
                 {"id": id_alerta, "origen": "voz"}, qos=1)
        _publicar_respuesta(publicar, textos.RESPUESTA_AYUDA, frase_id)
        registro.info("voz turno: texto=%r intencion=AYUDA accion=ayuda_publicada "
                      "latencia_ms=%d", texto, latencia)
        return True

    if intencion == "CANCELAR":
        _publicar_respuesta(publicar, textos.RESPUESTA_CANCELAR, frase_id)
        registro.info("voz turno: texto=%r intencion=CANCELAR accion=cancelar "
                      "latencia_ms=%d", texto, latencia)
        return True

    # Intencion desconocida (no deberia ocurrir).
    _publicar_respuesta(publicar, textos.NO_ENTENDIDO, frase_id)
    registro.info("voz turno: texto=%r intencion=%s accion=no_entendido "
                  "latencia_ms=%d", texto, intencion, latencia)
    return True


_frase_a_intencion = _cargar()
