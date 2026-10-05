"""Decide qué hacer con un mensaje que llega de MQTT.

Este archivo es el que decide. `mqtt_cliente.py` solo mueve bytes, `db.py` solo
ejecuta SQL, y aquí están las reglas que hacen que no se manden avisos repetidos ni
avisos basura:

1. El tipo de alerta lo decide el TÓPICO, no un campo del mensaje. `alerta/gas` es
   una alerta de gas y `alerta/ayuda` es una petición de ayuda, porque eso es lo
   que dice el contrato.
2. El mensaje tiene que ser JSON, y tiene que ser un objeto.
3. Tiene que traer un `id`, y el `id` tiene que ser un texto razonable.
4. Tiene que traer los campos que el contrato pide para ese tipo de alerta.
5. El `id` tiene que ser nuevo. Si ya se vio, se descarta en silencio.

El punto 1 es el que estaba mal antes, y el error era serio. La versión anterior
buscaba un campo `tipo` dentro del mensaje y lo tomaba por buena fuente. El
firmware no manda ese campo: manda `{"id":"...","gas_adc":2100}` en `alerta/gas` y
`{"id":"...","origen":"alexa"}` en `alerta/ayuda`, tal cual está en el contrato. Con
el código anterior, toda alerta real del ESP32 se rechazaba con el motivo "tipo None
no es gas ni ayuda", y lo que llegaba a Telegram eran los mensajes que publicaba el
propio orquestador en sus pruebas. Es la clase de fallo que no se ve leyendo el
código: se ve cuando una alerta de gas no suena.

Y no es una decisión de gusto usar el tópico. El campo `tipo` lo puede poner o
quitar quien publica, así que un mensaje con `id` bueno y `tipo` equivocado
terminaría en la cola de la familia como el tipo que el mensaje dice, no como el que
es. El tópico es lo que el broker aplica y lo que la ACL controla.
"""

import json
import logging
import re

import config

registro = logging.getLogger("alertas")

# --- El formato del id -----------------------------------------------------
#
# NO se valida el formato exacto del contrato (`<6 hex>-b<4 hex>-<4 hex>`), y es
# deliberado.
#
# El contrato describe el formato que genera el ESP32, y el plan lo pone como
# "el servidor también genera ids". Si aquí se exigiera ese formato exacto, un id
# como `srv-0001` sería rechazado, y el servidor es justo uno de los que manda
# alertas de ayuda. Con el filtro estricto, las alertas del propio servidor
# quedarían fuera del sistema, en silencio, porque el rechazo se registra en el
# log y no devuelve nada.
#
# Lo que se comprueba es lo mínimo que hace falta para que el campo sea utilizable
# como clave primaria y no rompa una consulta:
#
# - que sea texto y no un número, un objeto o un array;
# - que no esté vacío y no pase de 64 caracteres, que es el límite de SQLite para
#   una clave primaria de texto sin asignarle un índice aparte;
# - que solo tenga minúsculas, dígitos y guiones. Sin esto, un id con comillas o
#   con `%` se metería en consultas construidas con `LIKE` o con `%s` mal
#   puestos, y el `?` de SQLite protege contra inyección pero no contra un `LIKE`
#   con `%` dentro.
#
# En MQTT 3.1.1 no hay clave de mensaje, así que no hay forma de pedir al broker
# "exactamente una vez". Por eso el `id` es lo único que impide el duplicado.
ID_VALIDO = re.compile(r"\A[a-z0-9-]{1,64}\Z")

# El tipo sale del tópico. Estos dos son los únicos que esta parte del sistema
# manda a Telegram.
TIPO_POR_TOPICO = {
    config.TOPICO_ALERTA_GAS: "gas",
    config.TOPICO_ALERTA_AYUDA: "ayuda",
}


def id_valido(valor) -> bool:
    return isinstance(valor, str) and ID_VALIDO.match(valor) is not None


def validar(topico: str, cuerpo: str) -> tuple:
    """Devuelve `(carga, id_alerta, tipo, error)`.

    `carga` y `id_alerta` valen `None` si hubo error. `error` es una cadena
    corta con el motivo, para el log. Devolver el motivo y no solo un `False` es
    lo que hace que un rechazo se pueda depurar: sin el motivo, una alerta que no
    llega parece un fallo de Telegram, y el sitio donde se depura no es donde
    está el problema.

    El `tipo` sale del tópico, y el cuerpo se valida contra lo que el contrato pide
    para ese tipo:

        alerta/gas    {"id":"...","gas_adc":2100}
        alerta/ayuda  {"id":"...","origen":"alexa"}

    Un campo `tipo` en el mensaje se acepta pero se ignora, con una línea de aviso:
    no es un campo del contrato, y si viene, el tópico manda.

    Los campos EXTRA no se rechazan. Se avisan y la alerta sigue adelante, y es
    deliberado: un campo de más no es una razón para tragarse un aviso de gas. La
    regla del simulador y la del firmware se pueden ampliar sin que este servicio
    empiece a perder alertas por un campo nuevo que nadie ha revisado.
    """
    tipo = TIPO_POR_TOPICO.get(topico)
    if tipo is None:
        return None, None, None, (
            "topico %r no es de los dos que se atienden (%s)"
            % (topico, " y ".join(sorted(TIPO_POR_TOPICO)))
        )

    try:
        carga = json.loads(cuerpo)
    except ValueError as error:
        return None, None, None, "no es JSON: %s" % error

    if not isinstance(carga, dict):
        return None, None, None, "no es un objeto JSON, es %s" % type(carga).__name__

    id_alerta = carga.get("id")
    if id_alerta is None:
        return None, None, None, "no trae id"
    if not id_valido(id_alerta):
        return (None, None, None,
                "id %r no cumple: texto de 1 a 64 con minusculas, digitos y guiones"
                % (id_alerta,))

    if "tipo" in carga:
        registro.warning("la alerta %s trae el campo tipo %r, que no es del contrato; "
                      "manda el topico (%s)", id_alerta, carga["tipo"], tipo)

    if tipo == "gas":
        error = _validar_gas(carga)
    else:
        error = _validar_ayuda(carga)
    if error is not None:
        return None, None, None, error

    sobrantes = sorted(set(carga) - {"id", "gas_adc", "origen", "tipo"})
    if sobrantes:
        # Se acepta y se avisa. NO se rechaza.
        #
        # Descartar una alerta de gas por un campo de sobra es el peor fallo que
        # puede tener este archivo: la familia no recibe el aviso y el log dice
        # «rechazada», que parece unadecision del sistema y no un error. El
        # problema real de un campo de mas es del que publica, y se le avisa con
        # un warning en vez de dejarle la familia sin saber nada.
        #
        # `tipo` no sale en la lista porque ya tiene su aviso propio mas arriba,
        # que es el caso mas comun: el firmware viejo mandaba ese campo.
        registro.warning("la alerta %s trae campos que no son del contrato (%s); se "
                         "acepta y se ignoran", id_alerta, ", ".join(sobrantes))

    return carga, id_alerta, tipo, None


def _validar_gas(carga: dict):
    """`alerta/gas` tiene que traer `gas_adc` como entero del rango del ADC.

    El rango es 0 a 4095, que es lo que devuelve `analogRead()` con atenuación de
    11 dB. Se comprueba porque un `gas_adc` que no sea un número (o que sea un
    texto) no se puede comparar con el umbral, y una alerta que dice "gas: mucho"
    sin número no le sirve de nada a quien la lee.
    """
    if "gas_adc" not in carga:
        return "alerta de gas sin gas_adc"
    valor = carga["gas_adc"]
    if not isinstance(valor, int) or isinstance(valor, bool):
        return "gas_adc %r no es un entero" % (valor,)
    if not config.ADC_MINIMO <= valor <= config.ADC_MAXIMO:
        return "gas_adc %d fuera de %d..%d" % (valor, config.ADC_MINIMO,
                                               config.ADC_MAXIMO)
    return None


def _validar_ayuda(carga: dict):
    """`alerta/ayuda` trae `origen`, pero su ausencia NO descarta la alerta.

    El origen es lo que le dice a la familia si la petición viene de Alexa o de
    la voz, y el contrato solo admite cuatro valores (plan, linea 195). Un origen
    desconocido no descarta la alerta: se registra y se sigue, porque perder una
    petición de ayuda por un campo mal escrito sería peor que mandar un mensaje
    con un origen raro.

    Y si el `origen` no viene, tampoco se descarta. La regla general es que solo
    se rechaza una alerta por lo que la deja inservible: que no tenga `id`, que el
    gas no sea un número, o que el gas esté fuera de rango. Una peticion de ayuda
    sin `origen` sigue siendo una peticion de ayuda, y quien la pide esta teniendo
    un problema. Se avisa con un warning y el texto sale sin el origen, que es
    mejor que un silencio con un `gas_adc` en medio de la cocina.
    """
    if "origen" not in carga:
        registro.warning("alerta de ayuda %s sin origen; se acepta igual",
                         carga.get("id", "?"))
        return None
    origen = carga["origen"]
    if not isinstance(origen, str) or not origen:
        registro.warning("alerta de ayuda con origen %r, que no es un texto; se "
                         "acepta igual", origen)
        return None
    if origen not in config.ORIGENES_VALIDOS:
        registro.warning("alerta de ayuda con origen %r, que no es uno del "
                         "contrato %s; se acepta igual", origen,
                         ", ".join(config.ORIGENES_VALIDOS))
    return None


def texto_para_telegram(carga: dict, tipo: str) -> str:
    """El texto que se manda a la familia.

    Va en texto plano, sin Markdown. Dos razones: Telegram falla al parsear si
    el texto tiene un `_` suelto y el formato se rompe, y un mensaje de alarma con
    asteriscos y guiones bajos parece un mensaje de sistema, no una persona
    hablando. La alerta de una persona mayor tiene que leerse como una frase.

    Los valores salen de los campos del contrato: `gas_adc` para el gas, `origen`
    para la ayuda. Antes se leían `nivel`, `valor` y `mensaje`, que el contrato no
    define, así que salía "Nivel: desconocido" y "Lectura: ?" en cada alerta.

    Tampoco se incluye el nombre de usuario del bot. El plan lo pedía en un
    punto, pero el usuario confirmó que no hace falta: el chat es privado con la
    familia, y un bot no necesita presentarse en cada mensaje. Lo que sí se
    incluye es el id de la alerta al final, para que la familia pueda leerlo en
    voz alta y quien atienda pueda buscarlo en la base.
    """
    if tipo == "gas":
        gas_adc = carga.get("gas_adc", "?")
        texto = (
            "ALERTA DE GAS en casa.\n\n"
            "Se detecto gas en el sensor de la sala.\n"
            "Lectura del sensor: %s\n\n"
            "Por favor:\n"
            "1. No uses el interruptor de la luz.\n"
            "2. Abre las ventanas y la puerta.\n"
            "3. Sal de la casa y cierra la puerta.\n"
            "4. Llama a los servicios de emergencia.\n\n"
            "Esta alerta la manda el asistente de la casa, de forma automatica."
            % (gas_adc,)
        )
    elif tipo == "ayuda":
        origen = carga.get("origen") or ""
        if origen:
            de_quien = "(origen: %s)" % origen
        else:
            # La peticion se acepta sin `origen` (ver `_validar_ayuda`), asi que
            # el texto tiene que salir bien tambien sin el. Decir «origen:
            # desconocido» seria confuso: no es que no se sepa, es que no vino.
            de_quien = "(no dice de donde)"
        texto = (
            "PETICION DE AYUDA desde casa.\n\n"
            "Alguien en la casa pidio ayuda %s.\n\n"
            "La alerta la manda el asistente de la casa, de forma automatica."
            % de_quien
        )
    else:
        texto = "Alerta de la casa: %s" % json.dumps(carga, separators=(",", ":"))

    return "%s\n\nId de la alerta: %s" % (texto, carga.get("id", "?"))


def teclado_alerta(id_alerta: str):
    """El teclado de botones del mensaje de alerta.

    Se construye en `bot.py` y se devuelve aqui para que quien llama no tenga que
    importar `telegram`: `cola.py` solo necesita pasarselo a `Remitente.enviar`.
    """
    import bot
    return bot.teclado_alerta(id_alerta)


def procesar(topico: str, cuerpo: str, base) -> bool:
    """El camino completo de un mensaje. Devuelve si la alerta quedó en la cola.

    El orden importa y no es al revés: primero se valida, después se mira si el
    id es nuevo, y solo entonces se inserta en la cola.

    La razón de ese orden es el precio de deduplicar con una tabla aparte. Cada
    mensaje que pasa la validación deja una fila en `ids_procesados`, para siempre
    o hasta la purga de 7 días. Si se registrara el id **antes** de validar el
    tipo, un mensaje con un id válido pero un tipo desconocido dejaría su id
    quemado: si más adelante volviera el mismo mensaje con un tipo bueno, se
    descartaría como duplicado, y se perdería una alerta buena por un mensaje
    malo. Validando antes de registrar, ese caso no puede pasar.

    El antirrebote de ayuda va DESPUÉS de registrar el id y ANTES de encolar, y en
    ese orden por dos razones. El id se registra siempre, para que cinco entregas
    del mismo mensaje sean una sola. Y el antirrebote se comprueba al final, para
    que la alerta se descarte por la ventana de 60 s y no por estar repetida: el
    log dice cuál de las dos cosas pasó, que es lo que hace falta para entender por
    qué una segunda petición de ayuda no llegó.
    """
    carga, id_alerta, tipo, error = validar(topico, cuerpo)

    if error is not None:
        registro.warning("descartado de %s: %s", topico, error)
        return False

    if not base.registrar_id(id_alerta, tipo):
        # Duplicado. Es el caso normal, no un error: un mensaje QoS 1 puede llegar
        # dos veces y para eso está la tabla. Una línea de INFO y nada más.
        registro.info("duplicado, id %s ya se habia procesado (%s)", id_alerta, tipo)
        return False

    if tipo == "ayuda" and base.hay_ayuda_reciente():
        # No se inserta fila en `alertas`: no hay nada que mandar. El id sí queda
        # quemado en `ids_procesados`, que es lo que evita que una tercera entrega
        # del mismo mensaje vuelva a comprobar la ventana.
        registro.info("alerta de ayuda %s NO se encola por el antirrebote de %d s "
                      "(P05): ya hubo una ayuda en esa ventana. El id queda "
                      "registrado como visto",
                      id_alerta, config.ANTIRREBOTE_AYUDA_S)
        return False

    texto = json.dumps(carga, separators=(",", ":"))
    if not base.insertar_alerta(id_alerta, tipo, texto):
        registro.info("la alerta %s ya estaba en la cola", id_alerta)
        return False

    registro.warning("ALERTA %s de %s encolada", tipo.upper(), id_alerta)
    return True
