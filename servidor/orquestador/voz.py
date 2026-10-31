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
from datetime import datetime
from zoneinfo import ZoneInfo

import alertas
import config
import textos

registro = logging.getLogger("voz")

UNK = "[unk]"
CONTADOR_AYUDA = [0]

# Estado en memoria que actualizan los mensajes MQTT (orquestador.enrutar).
_estado_luz = {"valor": None}   # "on" | "off" | None
_ultimos_sensores = {"temperatura": None, "humedad": None, "ts": None}
_nodo_conectado = {"valor": None}  # True | False | None
_ventana_hasta = {"valor": 0.0}
_ventana_reintentos = {"valor": 0}
_ventana_modo = {"valor": None}  # "chat" | "video" | None
_ventana_idioma = {"valor": "es"}  # "es" | "en"
_catalogo_ingles = {}  # titulo normalizado -> artista

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


def _ruta_catalogo() -> str:
    candidatas = []
    env = os.environ.get("CATALOGO_INGLES_JSON")
    if env:
        return os.path.abspath(env)
    candidatas.append("/app/canciones_ingles.json")
    base = os.path.dirname(os.path.abspath(__file__))
    candidatas.append(os.path.join(base, "..", "voz", "canciones_ingles.json"))
    candidatas.append(os.path.join(base, "..", "..", "servidor", "voz", "canciones_ingles.json"))
    for ruta in candidatas:
        if ruta and os.path.isfile(ruta):
            return os.path.abspath(ruta)
    return ""


def _cargar_catalogo() -> dict:
    global _catalogo_ingles
    ruta = _ruta_catalogo()
    if not ruta:
        registro.warning("voz: no se encontro canciones_ingles.json; sin catalogo")
        return {}
    try:
        with open(ruta, encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, ValueError) as error:
        registro.warning("voz: no se pudo leer %s: %s", ruta, error)
        return {}
    if not isinstance(datos, list):
        registro.warning("voz: canciones_ingles.json no es una lista")
        return {}
    mapa = {}
    for item in datos:
        if not isinstance(item, dict):
            continue
        titulo = normalizar(item.get("titulo", ""))
        artista = str(item.get("artista", "")).strip()
        if titulo and artista:
            mapa[titulo] = artista
    _catalogo_ingles = mapa
    registro.info("voz: catalogo ingles cargado desde %s (%d titulos)", ruta, len(mapa))
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

def actualizar_sensores(cuerpo: str) -> None:
    try:
        carga = json.loads(cuerpo)
    except (TypeError, ValueError):
        return
    if isinstance(carga, dict):
        if "temperatura_c" in carga:
            _ultimos_sensores["temperatura"] = carga.get("temperatura_c")
        if "humedad_pct" in carga:
            _ultimos_sensores["humedad"] = carga.get("humedad_pct")
        _ultimos_sensores["ts"] = int(time.time())


def _publicar_respuesta(publicar, texto, frase_id=None, extra=None):
    salida = {"texto": texto, "tipo": "respuesta", "ts": int(time.time())}
    if isinstance(frase_id, str) and frase_id:
        salida["frase_id"] = frase_id
    if isinstance(extra, dict):
        salida.update(extra)
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




def abrir_ventana_respuesta() -> None:
    _ventana_hasta["valor"] = time.time() + config.VENTANA_RESPUESTA_CHAT_S
    _ventana_reintentos["valor"] = 1
    _ventana_modo["valor"] = "chat"


def cerrar_ventana_respuesta() -> None:
    _ventana_hasta["valor"] = 0.0
    _ventana_reintentos["valor"] = 0
    _ventana_modo["valor"] = None


def ventana_respuesta_abierta() -> bool:
    return time.time() < _ventana_hasta["valor"] and _ventana_modo["valor"] == "chat"


def abrir_ventana_video(ingles: bool = False) -> None:
    _ventana_hasta["valor"] = time.time() + config.VENTANA_VIDEO_S
    _ventana_reintentos["valor"] = 0
    _ventana_modo["valor"] = "video"
    _ventana_idioma["valor"] = "en" if ingles else "es"


def cerrar_ventana_video() -> None:
    _ventana_hasta["valor"] = 0.0
    _ventana_reintentos["valor"] = 0
    _ventana_modo["valor"] = None
    _ventana_idioma["valor"] = "es"


def ventana_video_abierta() -> bool:
    return time.time() < _ventana_hasta["valor"] and _ventana_modo["valor"] == "video"

def ventana_video_idioma() -> str:
    return _ventana_idioma["valor"] if _ventana_idioma["valor"] in ("es", "en") else "es"


def ventana_abierta() -> bool:
    return time.time() < _ventana_hasta["valor"]


def cerrar_ventana() -> None:
    cerrar_ventana_respuesta()



def _numero_a_palabras(n: int) -> str:
    unidades = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis",
                "siete", "ocho", "nueve", "diez", "once", "doce", "trece",
                "catorce", "quince", "dieciseis", "diecisiete", "dieciocho",
                "diecinueve", "veinte"]
    if n <= 20:
        return unidades[n]
    if n < 30:
        return "veinti" + unidades[n - 20]
    decenas = ["", "", "veinte", "treinta", "cuarenta", "cincuenta"]
    d = n // 10
    u = n % 10
    if u == 0:
        return decenas[d]
    return decenas[d] + " y " + unidades[u]


def _hora_en_palabras(ahora) -> str:
    hora = ahora.hour
    minuto = ahora.minute
    if minuto == 0:
        if hora == 0:
            return "Es medianoche."
        if hora == 12:
            return "Son las doce del día."
        if hora < 12:
            if hora == 1:
                return "Es la una de la mañana."
            return "Son las %s de la mañana." % _numero_a_palabras(hora)
        h = hora - 12
        if h == 1:
            return "Es la una de la tarde." if hora < 18 else "Es la una de la noche."
        if hora < 18:
            return "Son las %s de la tarde." % _numero_a_palabras(h)
        return "Son las %s de la noche." % _numero_a_palabras(h)
    if minuto == 30:
        minuto_txt = "media"
    else:
        minuto_txt = _numero_a_palabras(minuto)
    if hora == 0:
        if minuto == 30:
            return "Es la una y media de la madrugada."
        return "Es la una y %s de la madrugada." % minuto_txt
    if hora < 12:
        if hora == 1:
            if minuto == 30:
                return "Es la una y media."
            return "Es la una y %s." % minuto_txt
        if minuto == 30:
            return "Son las %s y media." % _numero_a_palabras(hora)
        return "Son las %s y %s." % (_numero_a_palabras(hora), minuto_txt)
    if hora == 12:
        if minuto == 30:
            return "Son las doce y media del día."
        return "Son las doce y %s del día." % minuto_txt
    h = hora - 12
    if h == 1:
        if minuto == 30:
            return "Es la una y media de la tarde." if hora < 18 else "Es la una y media de la noche."
        if hora < 18:
            return "Es la una y %s de la tarde." % minuto_txt
        return "Es la una y %s de la noche." % minuto_txt
    if hora < 18:
        if minuto == 30:
            return "Son las %s y media de la tarde." % _numero_a_palabras(h)
        return "Son las %s y %s de la tarde." % (_numero_a_palabras(h), minuto_txt)
    if minuto == 30:
        return "Son las %s y media de la noche." % _numero_a_palabras(h)
    return "Son las %s y %s de la noche." % (_numero_a_palabras(h), minuto_txt)


def _limpiar_consulta_video(texto) -> str:
    if not isinstance(texto, str):
        return ""
    limpio = "".join(c for c in texto if c.isalnum() or c.isspace())
    return " ".join(limpio.split())[:100]


def _satelite_video_activo(satelites) -> bool:
    if satelites is None:
        return False
    cid = config.SATELITE_VIDEO_CLIENT_ID
    for nombre in ("tiene_video", "hay_video", "satelite_con_video",
                   "video_activo", "hay_satelite_con_video"):
        metodo = getattr(satelites, nombre, None)
        if callable(metodo):
            try:
                return bool(metodo(cid))
            except TypeError:
                try:
                    return bool(metodo())
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
    for atributo in ("_video", "video", "_videos"):
        valor = getattr(satelites, atributo, None)
        if isinstance(valor, dict):
            if cid in valor:
                v = valor[cid]
                if isinstance(v, dict):
                    return bool(v.get("video"))
                return bool(v)
            if any((x.get("video") if isinstance(x, dict) else bool(x))
                   for x in valor.values()):
                return True
    return False


def publicar_cerrar_video_por_alerta(publicar) -> None:
    cid = config.SATELITE_VIDEO_CLIENT_ID
    try:
        publicar("casa/sala/satelite/%s/video" % cid,
                 {"accion": "cerrar", "ts": int(time.time())}, qos=1)
    except Exception:  # noqa: BLE001
        pass


def procesar(cuerpo: str, publicar, base=None, satelites=None) -> bool:
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
    texto_libre = carga.get("texto_libre", "")
    if not isinstance(texto_libre, str):
        texto_libre = ""

    # Ventana (chat o video): tiene prioridad sobre todo lo demas.
    if ventana_abierta():
        if ventana_video_abierta():
            es_ayuda = (_frase_a_intencion.get(normalizar(texto)) == "AYUDA"
                        or _frase_a_intencion.get(normalizar(texto_libre)) == "AYUDA")
            if es_ayuda:
                cerrar_ventana_video()
                publicar_cerrar_video_por_alerta(publicar)
                if base is not None and hasattr(base, "hay_ayuda_reciente") \
                        and base.hay_ayuda_reciente():
                    _publicar_respuesta(publicar, textos.RESPUESTA_AYUDA_RECIENTE, frase_id)
                    return True
                CONTADOR_AYUDA[0] += 1
                id_alerta = "srv-%08x-%04x" % (int(time.time()) % 0xFFFFFFFF,
                                               CONTADOR_AYUDA[0])
                publicar(config.TOPICO_ALERTA_AYUDA,
                         {"id": id_alerta, "origen": "voz"}, qos=1)
                _publicar_respuesta(publicar, textos.RESPUESTA_AYUDA, frase_id)
                return True

            if (normalizar(texto) == "cancela"
                    or normalizar(texto_libre) == "cancela"
                    or _frase_a_intencion.get(normalizar(texto)) == "CANCELAR"
                    or _frase_a_intencion.get(normalizar(texto_libre)) == "CANCELAR"):
                cerrar_ventana_video()
                _publicar_respuesta(publicar, textos.RESPUESTA_CANCELAR, frase_id)
                return True

            idioma_ventana = ventana_video_idioma()
            if idioma_ventana == "en":
                if no_entendido(texto_libre):
                    _publicar_respuesta(publicar, textos.RESPUESTA_NO_ENTENDI_VIDEO, frase_id)
                    _ventana_hasta["valor"] = time.time() + config.VENTANA_VIDEO_S
                    return True
                titulo = normalizar(texto_libre)
                if titulo not in _catalogo_ingles:
                    _publicar_respuesta(publicar, textos.RESPUESTA_NO_ENTENDI_VIDEO, frase_id)
                    _ventana_hasta["valor"] = time.time() + config.VENTANA_VIDEO_S
                    return True
                artista = _catalogo_ingles[titulo]
                consulta = "%s %s" % (titulo, artista)
                respuesta_buscar = textos.RESPUESTA_BUSCANDO_VIDEO.format(
                    consulta="%s de %s" % (titulo, artista))
                # reusar respuesta con "Busco: <titulo> de <artista>"
                respuesta_buscar = "Busco: %s de %s" % (titulo, artista)
            else:
                if no_entendido(texto) and no_entendido(texto_libre):
                    _publicar_respuesta(publicar, textos.RESPUESTA_NO_ENTENDI_VIDEO, frase_id)
                    _ventana_hasta["valor"] = time.time() + config.VENTANA_VIDEO_S
                    return True
                consulta = texto_libre if not no_entendido(texto_libre) else texto
            if idioma_ventana != "en":
                consulta = _limpiar_consulta_video(consulta)
                if len(consulta) < 2:
                    _publicar_respuesta(publicar, textos.RESPUESTA_NO_ENTENDI_VIDEO, frase_id)
                    _ventana_hasta["valor"] = time.time() + config.VENTANA_VIDEO_S
                    return True
            else:
                consulta = _limpiar_consulta_video(consulta)
                if len(consulta) < 2:
                    _publicar_respuesta(publicar, textos.RESPUESTA_NO_ENTENDI_VIDEO, frase_id)
                    _ventana_hasta["valor"] = time.time() + config.VENTANA_VIDEO_S
                    return True

            cerrar_ventana_video()
            disponible = carga.get("ingles_disponible", True)
            if idioma_ventana == "en":
                if disponible is False:
                    respuesta = ("%s. %s"
                                 % (textos.RESPUESTA_VIDEO_INGLES_NO_DISPONIBLE,
                                    respuesta_buscar))
                else:
                    respuesta = respuesta_buscar
            else:
                if False:
                    respuesta = ""
                else:
                    respuesta = textos.RESPUESTA_BUSCANDO_VIDEO.format(consulta=consulta)
            _publicar_respuesta(publicar, respuesta, frase_id)
            cid = config.SATELITE_VIDEO_CLIENT_ID
            try:
                publicar("casa/sala/satelite/%s/video" % cid,
                         {"accion": "buscar", "consulta": consulta,
                          "ts": int(time.time())}, qos=1)
            except Exception as error:  # noqa: BLE001
                registro.warning("voz: no se pudo publicar video/buscar: %s", error)
            registro.info("voz turno: intencion=BUSCAR_VIDEO accion=video_buscar "
                          "idioma=%s consulta_len=%d", idioma_ventana, len(consulta))
            return True

        es_ayuda = (_frase_a_intencion.get(normalizar(texto)) == "AYUDA"
                    or _frase_a_intencion.get(normalizar(texto_libre)) == "AYUDA")
        if es_ayuda:
            cerrar_ventana_respuesta()
            publicar_cerrar_video_por_alerta(publicar)
            if base is not None and hasattr(base, "hay_ayuda_reciente") \
                    and base.hay_ayuda_reciente():
                _publicar_respuesta(publicar, textos.RESPUESTA_AYUDA_RECIENTE, frase_id)
                return True
            CONTADOR_AYUDA[0] += 1
            id_alerta = "srv-%08x-%04x" % (int(time.time()) % 0xFFFFFFFF,
                                           CONTADOR_AYUDA[0])
            publicar(config.TOPICO_ALERTA_AYUDA,
                     {"id": id_alerta, "origen": "voz"}, qos=1)
            _publicar_respuesta(publicar, textos.RESPUESTA_AYUDA, frase_id)
            return True

        if no_entendido(texto) and no_entendido(texto_libre):
            _publicar_respuesta(publicar, textos.PERDON_NO_ESCUCHO, frase_id)
            if _ventana_reintentos["valor"] > 0:
                _ventana_reintentos["valor"] -= 1
                _ventana_hasta["valor"] = time.time() + config.VENTANA_RESPUESTA_CHAT_S
            else:
                cerrar_ventana_respuesta()
            return True

        respuesta = texto_libre if not no_entendido(texto_libre) else texto
        if base is not None and hasattr(base, "crear_respuesta_conversacion"):
            base.crear_respuesta_conversacion(respuesta)
        _publicar_respuesta(publicar, textos.RESPUESTA_CHAT_ENVIADA, frase_id)
        cerrar_ventana_respuesta()
        return True

    if no_entendido(texto):
        _publicar_respuesta(publicar, textos.NO_ENTENDIDO, frase_id)
        registro.info("voz turno: intencion=NO_ENTENDI accion=no_entendido "
                      "texto_len=%d latencia_ms=%d", len(texto),
                      int((time.time() - int(ts)) * 1000))
        return True

    intencion = _frase_a_intencion.get(normalizar(texto))
    if intencion is None:
        _publicar_respuesta(publicar, textos.NO_ENTENDIDO, frase_id)
        registro.info("voz turno: intencion=NO_ENTENDI accion=no_entendido "
                      "texto_len=%d latencia_ms=%d", len(texto),
                      int((time.time() - int(ts)) * 1000))
        return True

    latencia = int((time.time() - int(ts)) * 1000)

    _FRASES_VIDEO_INGLES = {
        normalizar("pon un video en ingles"),
        normalizar("quiero ver un video en ingles"),
    }
    if intencion == "BUSCAR_VIDEO":
        if not config.VIDEO_HABILITADO or not _satelite_video_activo(satelites):
            _publicar_respuesta(publicar, textos.RESPUESTA_VIDEO_NO_DISPONIBLE, frase_id)
            registro.info("voz turno: intencion=BUSCAR_VIDEO accion=no_disponible "
                          "texto_len=%d", len(texto))
            return True
        ingles = normalizar(texto) in _FRASES_VIDEO_INGLES
        abrir_ventana_video(ingles=ingles)
        _publicar_respuesta(publicar, textos.PREGUNTA_VIDEO, frase_id,
                            extra={"idioma": "en" if ingles else "es",
                                   "ventana": "video",
                                   "ventana_s": config.VENTANA_VIDEO_S})
        registro.info("voz turno: intencion=BUSCAR_VIDEO accion=abrir_ventana "
                      "ingles=%s texto_len=%d", ingles, len(texto))
        return True

    if intencion == "CERRAR_VIDEO":
        publicar_cerrar_video_por_alerta(publicar)
        registro.info("voz turno: intencion=CERRAR_VIDEO accion=cerrar "
                      "texto_len=%d", len(texto))
        return True

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
        publicar_cerrar_video_por_alerta(publicar)
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
        ahora = int(time.time())
        recientes = []
        if base is not None and hasattr(base, "listar_recientes"):
            for fila in base.listar_recientes(limite=100, tipo="ayuda"):
                carga = {}
                try:
                    carga = json.loads(fila["carga_json"])
                except (TypeError, ValueError):
                    carga = {}
                if not (isinstance(carga, dict) and carga.get("origen") == "voz"):
                    continue
                recientes.append(fila)
        activas = [f for f in recientes
                   if f["resuelto_en"] is None
                   and ahora - int(f["creado_en"]) <= 120]
        if activas:
            elegida = max(activas, key=lambda f: int(f["creado_en"]))
            alertas.resolver(base, publicar, elegida["id"], ahora,
                             quien="La sala", origen="voz",
                             nombre_quien="sala:falsa_alarma")
            _publicar_respuesta(publicar, textos.RESPUESTA_CANCELAR_FALSA_ALARMA,
                               frase_id)
            registro.info("voz turno: texto=%r intencion=CANCELAR accion=cancelar_ayuda "
                          "id=%s latencia_ms=%d", texto, elegida["id"], latencia)
            return True
        if recientes:
            _publicar_respuesta(publicar, textos.RESPUESTA_CANCELAR_TARDE, frase_id)
        else:
            _publicar_respuesta(publicar, textos.RESPUESTA_CANCELAR_NADA, frase_id)
        registro.info("voz turno: texto=%r intencion=CANCELAR accion=sin_cancelar "
                      "latencia_ms=%d", texto, latencia)
        return True

    if intencion == "HORA":
        _publicar_respuesta(publicar, _hora_en_palabras(datetime.now(ZoneInfo("America/Mazatlan"))), frase_id)
        return True

    if intencion == "FECHA":
        ahora = datetime.now(ZoneInfo("America/Mazatlan"))
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        texto_fecha = "Hoy es %s %d de %s." % (
            dias[ahora.weekday()], ahora.day, meses[ahora.month - 1])
        _publicar_respuesta(publicar, texto_fecha, frase_id)
        return True

    if intencion == "TEMPERATURA":
        ts = _ultimos_sensores.get("ts")
        if ts is None or (int(time.time()) - int(ts)) > 300:
            _publicar_respuesta(publicar, textos.RESPUESTA_SIN_TEMPERATURA, frase_id)
            return True
        temp = _ultimos_sensores.get("temperatura")
        hum = _ultimos_sensores.get("humedad")
        try:
            temp_txt = str(int(round(float(temp)))) if temp is not None else None
        except (TypeError, ValueError):
            temp_txt = None
        try:
            hum_txt = str(int(round(float(hum)))) if hum is not None else None
        except (TypeError, ValueError):
            hum_txt = None
        if temp_txt is None:
            _publicar_respuesta(publicar, textos.RESPUESTA_SIN_TEMPERATURA, frase_id)
            return True
        if hum_txt is not None:
            respuesta = "En la sala hay %s grados y %s por ciento de humedad." % (temp_txt, hum_txt)
        else:
            respuesta = "En la sala hay %s grados." % temp_txt
        _publicar_respuesta(publicar, respuesta, frase_id)
        return True

    if intencion == "RECORDATORIOS":
        if base is None or not hasattr(base, "listar_recordatorios"):
            _publicar_respuesta(publicar, textos.RESPUESTA_SIN_RECORDATORIOS, frase_id)
            return True
        hoy = datetime.now(ZoneInfo("America/Mazatlan")).date()
        pendientes = []
        for fila in base.listar_recordatorios():
            if not fila["activo"]:
                continue
            prox = datetime.fromtimestamp(fila["proximo_en"], ZoneInfo("America/Mazatlan")).date()
            if prox == hoy:
                hora = datetime.fromtimestamp(fila["proximo_en"], ZoneInfo("America/Mazatlan")).strftime("%H:%M")
                pendientes.append((hora, fila["texto"]))
        if not pendientes:
            _publicar_respuesta(publicar, textos.RESPUESTA_SIN_RECORDATORIOS, frase_id)
            return True
        pendientes.sort()
        partes = ["a las %s, %s" % (h, t) for h, t in pendientes]
        _publicar_respuesta(publicar, "Hoy tiene: " + "; ".join(partes) + ".", frase_id)
        return True

    if intencion == "ESTOY_BIEN":
        if base is not None and hasattr(base, "crear_aviso_conversacion"):
            base.crear_aviso_conversacion("La sala", "0", "panel",
                                          "La sala avisa que está bien")
        _publicar_respuesta(publicar, textos.RESPUESTA_ESTOY_BIEN, frase_id)
        return True

    # Intencion desconocida (no deberia ocurrir).
    _publicar_respuesta(publicar, textos.NO_ENTENDIDO, frase_id)
    registro.info("voz turno: intencion=%s accion=no_entendido "
                  "texto_len=%d latencia_ms=%d", intencion, len(texto), latencia)
    return True


_frase_a_intencion = _cargar()
_catalogo_ingles = _cargar_catalogo()
