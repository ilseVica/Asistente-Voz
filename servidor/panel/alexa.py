"""La skill de Alexa como endpoint web del panel.

Alexa manda un POST con un RequestEnvelope firmado. Aqui se verifica la firma, la
cadena de certificados y la marca de tiempo (ask-sdk-webservice-support), se
comprueba el applicationId y, si todo va bien, se despacha la intencion: luz o
ayuda. La respuesta es la que Alexa dice en voz alta.
"""

import json
import logging
import time
from datetime import datetime, timezone

import config
import estado as estado_modulo

registro = logging.getLogger("panel-alexa")

CONTADOR_AYUDA = [0]


def mask_id(valor) -> str:
    if not valor:
        return "sin-id"
    texto = str(valor)
    if len(texto) <= 8:
        return texto[:2] + "..." + texto[-2:]
    return texto[:4] + "..." + texto[-4:]


def _skill():
    from ask_sdk_core.skill_builder import SkillBuilder

    return SkillBuilder().create()


class ApplicationIdVerifier:
    def __init__(self, esperado: str):
        self.esperado = esperado

    def verify(self, headers, serialized_request_env, deserialized_request_env):
        from ask_sdk_webservice_support.verifier import VerificationException

        app_id = None
        sesion = getattr(deserialized_request_env, "session", None)
        if sesion is not None and getattr(sesion, "application", None) is not None:
            app_id = getattr(sesion.application, "application_id", None)
        if app_id is None:
            contexto = getattr(deserialized_request_env, "context", None)
            sistema = getattr(contexto, "system", None) if contexto is not None else None
            aplicacion = getattr(sistema, "application", None) if sistema is not None else None
            app_id = getattr(aplicacion, "application_id", None) if aplicacion is not None else None
        if not app_id:
            raise VerificationException("applicationId ausente")
        if app_id != self.esperado:
            raise VerificationException("applicationId ajeno")


def _verifiers():
    from ask_sdk_webservice_support.verifier import RequestVerifier, TimestampVerifier

    verificadores = [RequestVerifier(), TimestampVerifier()]
    if getattr(config, "ALEXA_SKILL_ID", ""):
        verificadores.append(ApplicationIdVerifier(config.ALEXA_SKILL_ID))
    return verificadores


def verificar(headers, body_text: str):
    from ask_sdk_model import RequestEnvelope

    skill = _skill()
    envelope = skill.serializer.deserialize(payload=body_text, obj_type=RequestEnvelope)
    for verificador in _verifiers():
        verificador.verify(headers, body_text, envelope)
    return envelope


def _salida(texto: str, terminar: bool = True) -> dict:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": texto},
            "shouldEndSession": terminar,
        },
    }


def _usuario(envelope) -> str:
    sesion = getattr(envelope, "session", None)
    if sesion is not None and getattr(sesion, "user", None) is not None:
        return getattr(sesion.user, "user_id", "") or ""
    contexto = getattr(envelope, "context", None)
    sistema = getattr(contexto, "system", None) if contexto is not None else None
    usuario = getattr(sistema, "user", None) if sistema is not None else None
    return getattr(usuario, "user_id", "") or "" if usuario is not None else ""


def _dispositivo(envelope) -> str:
    contexto = getattr(envelope, "context", None)
    sistema = getattr(contexto, "system", None) if contexto is not None else None
    dispositivo = getattr(sistema, "device", None) if sistema is not None else None
    return getattr(dispositivo, "device_id", "") or "" if dispositivo is not None else ""


def luz_encendida(estados, base) -> bool | None:
    if estados is None:
        return None
    try:
        return estados.luz_encendida(base) if hasattr(estados, "luz_encendida") \
            else estados.instantanea(base)["luz"]["encendida"]
    except Exception:  # noqa: BLE001
        return None


def esperar_confirmacion_luz(estados, base, deseado: bool, timeout: float = 3.0) -> bool:
    limite = time.time() + timeout
    while time.time() < limite:
        if luz_encendida(estados, base) is deseado:
            return True
        time.sleep(0.1)
    return luz_encendida(estados, base) is deseado


def _luz(estado: str, mqtt, estados, base) -> tuple:
    from ask_sdk_model import IntentRequest  # noqa: F401

    if mqtt is None or not getattr(mqtt, "conectado", True):
        return 200, _salida("No puedo comunicarme con la casa en este momento.")
    ts = int(time.time())
    try:
        mqtt.publicar_luz(estado, origen="alexa", ts=ts)
    except Exception:  # noqa: BLE001
        registro.exception("alexa: no se pudo publicar la orden de luz")
        return 200, _salida("No pude enviar la orden.")
    confirmado = esperar_confirmacion_luz(estados, base, estado == "on")
    if confirmado:
        texto = "He encendido la luz" if estado == "on" else "He apagado la luz"
    else:
        verbo = "encender" if estado == "on" else "apagar"
        texto = "Pedí %s la luz, pero la casa no confirmó" % verbo
    return 200, _salida(texto)


def _ayuda(mqtt, estados, base) -> tuple:
    if mqtt is None or not getattr(mqtt, "conectado", True):
        return 200, _salida("No puedo comunicarme con la casa en este momento.")
    if base is not None and hasattr(base, "hay_ayuda_reciente") and base.hay_ayuda_reciente():
        import textos

        return 200, _salida(textos.RESPUESTA_AYUDA_RECIENTE)
    CONTADOR_AYUDA[0] += 1
    id_alerta = "alexa-%08x-%04x" % (int(time.time()) % 0xFFFFFFFF, CONTADOR_AYUDA[0])
    try:
        mqtt.publicar("casa/sala/alerta/ayuda", {"id": id_alerta, "origen": "alexa"}, qos=1)
    except Exception:  # noqa: BLE001
        registro.exception("alexa: no se pudo publicar la ayuda")
        return 200, _salida("No pude pedir ayuda.")
    import textos

    return 200, _salida(textos.RESPUESTA_AYUDA)


def despachar(envelope, mqtt, estados, base) -> tuple:
    from ask_sdk_model.launch_request import LaunchRequest
    from ask_sdk_model.intent_request import IntentRequest
    from ask_sdk_model.session_ended_request import SessionEndedRequest

    usuario = _usuario(envelope)
    dispositivo = _dispositivo(envelope)
    registro.info("alexa: peticion de %s / %s", mask_id(usuario), mask_id(dispositivo))

    request = envelope.request
    if isinstance(request, LaunchRequest):
        return 200, _salida(
            "Bienvenido al asistente de casa. Puede encender o apagar la luz, "
            "o pedir ayuda.")
    if isinstance(request, SessionEndedRequest):
        return 200, {"version": "1.0", "response": {}}
    if isinstance(request, IntentRequest):
        intent = request.intent
        nombre = getattr(intent, "name", "") if intent is not None else ""
        if nombre == "AMAZON.HelpIntent":
            return 200, _salida(
                "Si necesita ayuda urgente, diga: necesito ayuda")
        if nombre in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            return 200, _salida("Hasta luego.")
        if nombre == "AMAZON.FallbackIntent":
            return 200, _salida(
                "No le entendí. Puede encender o apagar la luz, o pedir ayuda.")
        if nombre in ("EncenderLuz", "EncenderLaLuz", "PrenderLuz"):
            return _luz("on", mqtt, estados, base)
        if nombre in ("ApagarLuz", "ApagarLaLuz"):
            return _luz("off", mqtt, estados, base)
        if nombre in ("Ayuda", "PedirAyuda", "SolicitarAyuda", "NecesitoAyuda"):
            return _ayuda(mqtt, estados, base)
    return 200, _salida("No le entendí.")
