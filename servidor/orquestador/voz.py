"""Lo que el orquestador hace con el texto reconocido, en la etapa A.

Esta fase todavia NO resuelve intenciones. El servicio de voz manda «que se dijo»
y aqui, por ahora, solo se hace el eco de prueba: repetir lo entendido por
`casa/sala/satelite/decir`, que es donde hablara el satelite cuando exista. Es la
forma de demostrar el camino completo (audio -> texto -> respuesta) sin la etapa B.

Dos decisiones que importan:

- **Se acepta cualquier objeto JSON con `texto`**, aunque no sea del todo del
  contrato. Un `frase_id` o un `modo` que falten no impiden contestar; perder el
  eco por un campo de mas o de menos seria un fallo silencioso, y esto es material
  de prueba. Lo que si se descarta es lo que no es JSON, o lo que no es un objeto:
  eso no es una frase, es basura, y se registra.

- **`[unk]` y el texto vacio se tratan igual**: no se entendio. El servicio de voz
  publica `[unk]` a proposito (ver su README), y quien decide que hacer con eso es
  este lado. El satelite no tiene por que saber que existe `[unk]`.

El eco se puede apagar con `ECO_VOZ=0` en el `.env`. Cuando el satelite de verdad
funcione (etapa B), se apaga y este archivo se queda para el Paso 3, que es donde
`procesar()` mirara la frase y decidira la intencion.
"""

import json
import logging
import time

import config
import textos

registro = logging.getLogger("voz")

# Lo que se contesta cuando no se entendio nada. El satelite lo dira en voz alta,
# asi que es una frase para oir, no un codigo de error. La redaccion vive en
# `textos.py`.
SIN_ENTENDER = textos.NO_ENTENDIDO

# El unico texto que el servicio de voz deberia mandar cuando no reconoce nada.
UNK = "[unk]"


def no_entendido(texto) -> bool:
    """¿El texto reconocido no es una frase util?"""
    return not isinstance(texto, str) or texto.strip() in ("", UNK)


def procesar(cuerpo: str, publicar) -> bool:
    """Hace el eco de una frase reconocida. Devuelve si se publico algo.

    `publicar(topico, carga, qos=...)` es el metodo de publicacion del cliente MQTT
    (`mqtt_cliente.ClienteMQTT.publicar`). Se pasa como argumento para que esta
    funcion se pueda probar sin broker, y para que este modulo no dependa de MQTT.
    """
    try:
        carga = json.loads(cuerpo)
    except ValueError as error:
        registro.warning("texto de voz que no es JSON: %s", error)
        return False
    if not isinstance(carga, dict):
        registro.warning("texto de voz que no es un objeto JSON: %s",
                         type(carga).__name__)
        return False

    # Descartar mensajes con antigüedad > 10 s o sin ts (regla igual que luz/set)
    ts = carga.get("ts")
    if ts is None or not isinstance(ts, (int, float)):
        registro.warning("voz: mensaje sin ts, descartado")
        return False
    try:
        now = int(time.time())
    except Exception:  # noqa: BLE001
        now = ts
    if abs(now - int(ts)) > 10:
        registro.warning("voz: mensaje antiguo (ts=%s, ahora=%s), descartado",
                         ts, now)
        return False

    texto = carga.get("texto", "")
    frase_id = carga.get("frase_id")
    modo = carga.get("modo")

    if not config.ECO_VOZ:
        # Sin eco, el texto se queda en el log. Es lo que pedira la etapa B: el
        # reconocimiento no se pierde, simplemente no se habla solo.
        registro.info("voz %s [%s]: %r (eco desactivado)",
                      frase_id, modo, texto)
        return False

    respuesta = SIN_ENTENDER if no_entendido(texto) else textos.eco(texto)
    publicar(
        config.TOPICO_SATELITE_DECIR,
        {"texto": respuesta, "tipo": "respuesta", "ts": int(time.time())},
        qos=1,
    )
    registro.info("voz %s [%s]: %r -> %r", frase_id, modo, texto, respuesta)
    return True
