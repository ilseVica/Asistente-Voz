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
import time

import config
import sanear as sanear_modulo
import textos

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

    Los campos EXTRA no se rechazan. Se avisan y la alerta sigue adelante, y es
    deliberado: un campo de más no es una razón para tragarse un aviso de gas. La
    regla del simulador y la del firmware se pueden ampliar sin que este servicio
    empiece a perder alertas por un campo nuevo que nadie ha revisado.

    Y `tipo` no es una excepción: es un campo más que no está en el contrato, así que
    cae en la misma bolsa. Antes tenía su propio aviso, y una versión anterior lo
    trataba casi como un error. Un `tipo` equivocado no es un mensaje roto: es un
    mensaje que trae de más una etiqueta que aquí no se usa, y manda el tópico igual.
    Lo que sí se rechaza sigue siendo lo que deja el aviso inservible: que no tenga
    `id`, que no sea JSON, o que el `gas_adc` no sea un número en rango.
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

    if tipo == "gas":
        error = _validar_gas(carga)
    else:
        error = _validar_ayuda(carga)
    if error is not None:
        return None, None, None, error

    # Las claves del contrato, para este tipo de alerta, son las unicas que no
    # cuentan como sobrantes. `tipo` cuenta como sobrante, y sale en la lista con
    # los demas: es el mismo problema y merece el mismo aviso.
    obligatorias = {"id", "gas_adc"} if tipo == "gas" else {"id", "origen"}
    sobrantes = sorted(set(carga) - obligatorias)
    if sobrantes:
        # Se acepta y se avisa. NO se rechaza.
        #
        # Descartar una alerta de gas por un campo de sobra es el peor fallo que
        # puede tener este archivo: la familia no recibe el aviso y el log dice
        # «rechazada», que parece una decision del sistema y no un error. El
        # problema real de un campo de mas es del que publica, y se le avisa con
        # un warning en vez de dejarle la familia sin saber nada.
        #
        # En la lista sale `tipo` si viene, que es el caso mas comun: el firmware
        # viejo mandaba ese campo, y un `tipo` que contradice al topico es
        # precisamente el caso en el que conviene gritar un poco mas.
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
    la voz, y el contrato solo admite cuatro valores (plan, línea 195). Un origen
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
    incluye es el id de la alerta al final («Referencia de la alerta»), para que
    la familia pueda leerlo en voz alta y quien atienda pueda buscarlo en la base.

    Las frases viven en `textos.py`. El lugar del sensor sale de
    `config.UBICACION_GAS`, y el origen de la ayuda se traduce a palabras
    (`textos.ORIGENES`); un origen que no se sabe traducir sale como «desde la
    casa» y su código queda en el log.
    """
    id_alerta = carga.get("id", "?")
    if tipo == "gas":
        if not textos.lugar_conocido(config.UBICACION_GAS):
            registro.warning("UBICACION_GAS=%r no esta en la tabla de lugares; el "
                             "articulo se deduce. Escribalo con articulo si sale mal",
                             config.UBICACION_GAS)
        return textos.alerta_gas(textos.lugar_con_articulo(config.UBICACION_GAS),
                                 carga.get("gas_adc", "?"), id_alerta)
    if tipo == "ayuda":
        # La peticion se acepta sin `origen` (ver `_validar_ayuda`), asi que el
        # texto tiene que salir bien tambien sin el.
        origen = carga.get("origen")
        if origen and not (isinstance(origen, str) and origen in textos.ORIGENES):
            registro.warning("alerta de ayuda %s con origen %r, que no se sabe "
                             "decir con palabras; el aviso dice «desde la casa»",
                             id_alerta, origen)
        return textos.alerta_ayuda(origen, id_alerta)
    return textos.alerta_desconocida(json.dumps(carga, separators=(",", ":")),
                                     id_alerta)


def teclado_alerta(id_alerta: str):
    """El teclado de botones del mensaje de alerta.

    Se construye en `bot.py` y se devuelve aquí para que quien llama no tenga que
    importar `telegram`: `cola.py` solo necesita pasarselo a `Remitente.enviar`.
    """
    import bot
    return bot.teclado_alerta(id_alerta)


def texto_atendido(tipo: str, cuando: int, id_alerta: str) -> str:
    """El texto con el que se reescribe un aviso de Telegram ya atendido.

    En un sitio y no en dos. El botón «Ya lo atendí» del chat y la tarea que
    reescribe los avisos atendidos desde el panel tienen que dejar el mensaje
    IGUAL, o el familiar vería dos redacciones distintas de lo mismo según por dónde
    hubiera atendido. De aquí sale la que usa `bot.cmd_atendi`, que antes la tenía
    escrita a mano.

    `cuando` es `alertas.resuelto_en`, la hora en que se atendió, y no la del envío
    ni la de la reescritura: es la que responde a «¿a qué hora atendiste?».
    """
    return textos.atendida(tipo, cuando, id_alerta)


def resolver(base, publicar, id_alerta: str, ahora: int = None,
             quien: str = "desconocido", origen: str = "") -> tuple:
    """Atiende una alerta. Devuelve `(fila, fresca)`.

    Esta función es la ÚNICA que resuelve una alerta, y la usan dos llamadores: el
    botón «Ya lo atendí» del bot y el botón del panel web. Vive aquí, y no en
    `bot.py`, porque el panel es un proceso aparte que no importa `telegram` y no
    tiene por qué saber nada de Telegram.

    Hace tres cosas, y el orden importa:

    1. `base.marcar_resuelta()`, que es lo que impide que la cola vuelva a mandar
       el aviso y cancela los envíos que quedaban pendientes de los demás
       familiares. Va primero porque es lo que evita el reenvío.
    2. Avisar al satélite en `satelite/decir`, para que el sitio deje de llamar.
    3. Escribir en el log.

    **El aviso al satélite sale SOLO si la alerta estaba sin resolver.** Ese `if
    fresca` es el arreglo de un desacuerdo que tenía el código y el README: el
    README decía que una segunda pulsación «no vuelve a avisar al satélite», y el
    código no lo cumplía, porque `bot.cmd_atendi` llamaba a `_avisar_al_satelite`
    sin mirar el resultado de `marcar_resuelta`.

    Con un solo botón eso era un detalle. Con dos, resolver dos veces es lo
    NORMAL: el familiar lo atiende en el panel y luego pulsa el botón de Telegram
    que tenía abierto, o lo pulsan dos familiares a la vez. Sin este `if`, cada
    pulsación publicaba en `satelite/decir` y el sitio oía «su familia ya
    recibió la alerta» tantas veces como se pulsara.

    Que avise solo la primera vez no pierde información: el sitio solo necesita
    saber que ya hay alguien con el aviso. Volver a decirlo es ruido, y en una
    casa el ruido en voz alta es un molesto.

    `publicar` puede ser `None` (el bot arranca sin cliente MQTT) o fallar. En los
    dos casos se registra y NO se lanza: la alerta ya está resuelta en la base, y
    eso no se deshace porque no se haya podido avisar al satélite.

    `quien` y `origen` van solo al log, para poder decir quién atendió y desde
    dónde. `quien` debe venir ya enmascarado (`db.enmascarar_chat`) si es un
    `chat_id`.
    """
    if ahora is None:
        ahora = int(time.time())

    fresca = base.marcar_resuelta(id_alerta, ahora)
    fila = base.obtener_alerta(id_alerta)
    tipo = fila["tipo"] if fila is not None else "alerta"

    if fresca:
        if publicar is None:
            registro.info("alerta %s resuelta; no hay cliente MQTT, no se avisa a "
                          "satelite/decir", id_alerta)
        else:
            texto = textos.aviso_atendida(tipo)
            try:
                publicar(
                    config.TOPICO_SATELITE_DECIR,
                    {"texto": texto, "tipo": "aviso", "ts": ahora},
                    qos=1, retain=False,
                )
                registro.info("publicado en satelite/decir: %s", texto)
            except Exception as error:  # noqa: BLE001
                registro.error("no se pudo publicar en satelite/decir: %s",
                               sanear_modulo.sanear(error))
    else:
        registro.info("alerta %s (%s): ya estaba resuelta, no se vuelve a avisar al "
                      "satelite", id_alerta, tipo)

    registro.warning("alerta %s (%s) marcada como resuelta por %s desde %s%s",
                     id_alerta, tipo, quien, origen or "el panel",
                     "" if fresca else " (ya estaba resuelta)")

    return fila, fresca


# --- Alertas habladas (Fase 2C) ------------------------------------------------

# Cuanto hacia atras se repite un gas sin atender a un satelite que se conecta.
SEGUNDOS_REPETIR_GAS = 2 * 3600

# `casa/sala/satelite/<client_id>/estado`: el client id es el cuarto nivel.
_TOPICO_ESTADO_SATELITE = re.compile(r"\Acasa/sala/satelite/([^/+#]+)/estado\Z")


def client_id_de_estado(topico: str):
    """El client id de un topico de estado de satelite, o `None` si no lo es."""
    coincide = _TOPICO_ESTADO_SATELITE.match(topico or "")
    return coincide.group(1) if coincide else None


def anunciar(tipo: str, publicar, destino: str = None, ahora: int = None) -> bool:
    """Publica en `satelite/decir` la alerta hablada. Devuelve si se publico.

    Tipo `alerta`, con `ts` de AHORA (el de la publicacion, no el de la alerta):
    asi la regla de antiguedad del satelite la deja pasar. Sin `destino`, la dicen
    todos los satelites; con `destino`, solo ese.

    Nunca lanza: un fallo aqui no puede impedir que la alerta llegue a la familia
    por Telegram, que va por otro camino (la cola), y ya esta guardada.
    """
    texto = textos.alerta_hablada(tipo, textos.lugar_con_articulo(config.UBICACION_GAS))
    if texto is None or publicar is None:
        return False
    carga = {"texto": texto, "tipo": "alerta",
             "ts": int(time.time()) if ahora is None else ahora}
    if destino is not None:
        carga["destino"] = destino
    try:
        publicar(config.TOPICO_SATELITE_DECIR, carga, qos=1)
    except Exception as error:  # noqa: BLE001
        registro.error("no se pudo publicar la alerta hablada (%s): %s", tipo,
                       sanear_modulo.sanear(error))
        return False
    registro.warning("alerta hablada de %s publicada en satelite/decir%s", tipo,
                     " para %s" % destino if destino else "")
    return True


def al_estado_satelite(topico: str, cuerpo: str, retenido: bool, base, publicar,
                       ahora: int = None) -> int:
    """Un satelite cambio de estado. Devuelve cuantas alertas se le repitieron.

    Solo reacciona al paso a `conectado: true` de un mensaje NO retenido. Los
    retenidos llegan cada vez que el orquestador se suscribe (al arrancar o al
    reconectar con el broker), y no significan que el satelite acabe de entrar: si
    contaran, cada reinicio del orquestador repetiria el gas en todas las casas.

    Lo que se repite, y solo a ese satelite (`destino`), es cada alerta de gas sin
    atender de las ultimas 2 horas. Si no hay ninguna, no se dice nada.
    """
    client_id = client_id_de_estado(topico)
    if client_id is None:
        return 0
    try:
        carga = json.loads(cuerpo)
    except ValueError:
        registro.warning("estado del satelite %s que no es JSON; se ignora", client_id)
        return 0
    if not isinstance(carga, dict) or carga.get("conectado") is not True:
        registro.info("satelite %s: %s", client_id, "desconectado"
                      if isinstance(carga, dict) and carga.get("conectado") is False
                      else "estado sin conectado:true")
        return 0
    if retenido:
        registro.info("satelite %s conectado (retenido): no se repite nada", client_id)
        return 0
    if ahora is None:
        ahora = int(time.time())
    pendientes = base.gas_sin_atender_desde(ahora - SEGUNDOS_REPETIR_GAS)
    if not pendientes:
        registro.info("satelite %s conectado; no hay gas sin atender", client_id)
        return 0
    repetidas = 0
    for fila in pendientes:
        if anunciar("gas", publicar, destino=client_id, ahora=ahora):
            repetidas += 1
    registro.warning("satelite %s conectado: alertas de gas sin atender que se le "
                     "repiten: %d", client_id, repetidas)
    return repetidas


def procesar(topico: str, cuerpo: str, base, publicar=None) -> bool:
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

    # La casa se entera en voz alta. Va DESPUES de guardar la alerta, y un fallo
    # aqui no la deshace: lo de la familia (Telegram, la cola) no cambia.
    anunciar(tipo, publicar)

    # `insertar_alerta` ya ha creado un envio por cada familiar registrado. Que no
    # haya ninguno no es un error: el aviso queda pendiente a la espera de que se
    # registre alguien, y en ese momento se le hace fan out
    # (`db.fan_out_sin_destino`).
    destinatarios = base.contar_familiares()
    if destinatarios:
        registro.warning("ALERTA %s de %s encolada para %d familiar(es)",
                         tipo.upper(), id_alerta, destinatarios)
    else:
        registro.warning("ALERTA %s de %s encolada, pero no hay ningun familiar "
                         "registrado: se queda esperando a que se registre uno",
                         tipo.upper(), id_alerta)
    return True
