"""La configuración del orquestador, leída del entorno.

Todo lo que cambia entre una máquina y otra se lee de variables de entorno, y
todo tiene un valor por defecto que sirve para desarrollar en una laptop. La idea
es que el mismo código arranque en el servidor sin editarlo.

Las contraseñas y el token **no** tienen valor por defecto, a propósito. Si
`MQTT_ORQUESTADOR_PASSWORD` no está, el proceso muere al arrancar con un mensaje
claro: es mejor que arranque y no pueda conectarse. El token de Telegram es la
excepción, porque el servicio tiene que arrancar igual sin él (ver más abajo).
"""

import os

# --- MQTT ------------------------------------------------------------------

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PUERTO = int(os.environ.get("MQTT_PUERTO", "1883"))
MQTT_USUARIO = os.environ.get("MQTT_USUARIO", "orquestador")

# La contraseña NO tiene valor por defecto, y por eso el servicio muere al
# arrancar sin ella (lo hace `mqtt_cliente.ClienteMQTT`). Es a propósito, y es
# lo contrario que se hace con el token de Telegram: un servicio que arranca y no
# puede conectarse parece sano, y nadie se entera hasta que hace falta una
# alerta. Un servicio que muere al arrancar se nota en el primer `docker logs`.
#
# El valor nunca se registra. Ni completo, ni recortado, ni con los primeros
# caracteres. Un log de arranque con un trozo de contraseña es una contraseña en
# un sitio que suele acabar pegado en un chat.
MQTT_ORQUESTADOR_PASSWORD = os.environ.get("MQTT_ORQUESTADOR_PASSWORD", "")

# El client id es FIJO y es el mismo que el usuario MQTT. Las dos cosas a la vez
# a propósito:
#
# - Fijo, porque el orquestador usa `clean_session=False`. Con una sesión
#   persistente, el broker guarda los mensajes QoS 1 que llegan mientras el
#   proceso está caído, y se los entrega al reconectar. Eso es lo que hace que las
#   alertas de gas no se pierdan si el orquestador se reinicia.
# - Que no se solape con otro cliente, porque el broker echa al primero cuando
#   entra otro con el mismo id.
#
# El riesgo asumido: si se levanta un segundo orquestador, el primero pierde su
# sesión y el broker le entrega los mensajes pendientes al segundo. Es el
# comportamiento estándar de MQTT, y es preferible a un id aleatorio, que
# acumularía sesiones muertas durante 2 días (`persistent_client_expiration`).
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "orquestador")

# `clean_session=False` es lo que habilita la cola del broker. Sin esto, las
# alertas de gas que llegan durante un corte se pierden, y el aviso nunca llega a
# Telegram. No es un detalle: es la diferencia entre que la familia se entere o
# no.
MQTT_SESION_LIMPIA = False

MQTT_KEEPALIVE = int(os.environ.get("MQTT_KEEPALIVE", "60"))

# --- Tópicos ---------------------------------------------------------------
#
# Solo gas y ayuda. `alerta/inactividad` queda fuera a propósito: el plan lo
# menciona, pero la parte de esta fase es el camino de las alertas urgentes, y
# una alerta de inactividad necesita un reloj de avisos que todavía no existe.
# Suscribirse a `alerta/#` y que el tipo decida sería más corto, y también peor:
# cualquier topico nuevo que se agregue empezaría a llegar sin que nadie lo haya
# revisado, y esta versión manda a Telegram lo que no conoce.
TOPICO_ALERTA_GAS = "casa/sala/alerta/gas"
TOPICO_ALERTA_AYUDA = "casa/sala/alerta/ayuda"

# Donde se publica lo que el asistente dice en voz alta. Lo usa el bot cuando el
# familiar pulsa "Ya lo atendi", para que el sitio se entere de que alguien ya
# está con el aviso. Todavía no hay satélite, así que esto solo se ve escuchando
# el broker con el usuario `satelite`.
TOPICO_SATELITE_DECIR = "casa/sala/satelite/decir"

QOS_ALERTAS = 1

# Ventana del antirrebote de ayuda, en segundos (P05). El ESP32 ya ignora nuevas
# peticiones durante 60 s; este es el segundo filtro, para el caso de que la alerta
# la publique el servidor (voz) y no el nodo.
ANTIRREBOTE_AYUDA_S = 60

# Rango del ADC del MQ-2 con atenuación de 11 dB, que es lo que lee `analogRead()`.
ADC_MINIMO = 0
ADC_MAXIMO = 4095

# Orígenes que admite el contrato para las órdenes, el estado de la luz y las
# alertas de ayuda (plan, línea 195).
ORIGENES_VALIDOS = ("voz", "alexa", "panel", "boton")

# --- Telegram --------------------------------------------------------------

# Sin valor por defecto y sin advertencias: el token no se escribe en el código
# ni en este archivo. El `.env` del servidor lo tiene.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# El chat de destino. Sale de `/registro`, no de aquí: el bot tiene que descubrir
# quién es la familia, y si estuviera en el entorno, cualquiera que publicara una
# alerta sabría a qué número mandar.
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# El botón "Abrir panel". Vacío está bien y confirmado: el panel todavía no
# existe, y un botón que lleva a `about:blank` es peor que no tenerlo.
PANEL_URL = os.environ.get("PANEL_URL", "")

TELEGRAM_ACTIVAR_BOT = os.environ.get("TELEGRAM_ACTIVAR_BOT", "0") == "1"

# Cuánto se espera entre reintentos de envío, y cuántas veces.
#
# La lista es la espera antes del reintento N, y NO es un máximo: se reintenta PARA
# SIEMPRE. Los valores son los de la propuesta aprobada (apartado 3.8): 15, 30, 60,
# 120 y 300 segundos, y a partir de ahí 300 segundos cada vez.
#
# Que no haya máximo es la decisión más importante de este archivo. La versión
# anterior terminaba los reintentos y marcaba la alerta como "descartada", y eso
# convertía un corte de Internet de cinco minutos en la pérdida definitiva de un
# aviso de gas: la familia se enteraba de que no le habían avisado, que es peor que
# un aviso tardío. Con espera creciente y sin límite, una alerta que no se puede
# mandar sigue pendiente, y sale en cuanto vuelve la red.
#
# Lo que sí se acota es el ruido: a partir del quinto fallo la espera ya no baja de
# cinco minutos, así que una caída larga no martillea la API de Telegram.
REINTENTO_SEGUNDOS = [15, 30, 60, 120, 300]

# Cuánto lleva una alerta sin poder mandarse antes de que el trabajador se ponga a
# avisar por log. La propuesta lo pide a las 24 horas, una vez por hora: que la
# alerta no se pierda nunca no significa que pueda fallar en silencio una semana.
SEGUNDOS_AVISO_PENDIENTE = 24 * 3600
SEGUNDOS_ENTRE_AVISOS = 3600

# Cuánto se espera entre un fallo de red de Telegram y el siguiente intento del
# bucle principal, para no martillear la API.
PAUSA_TRABAJADOR_S = 5.0

# --- Base de datos ---------------------------------------------------------

RUTA_DB = os.environ.get("ORQUESTADOR_DB", "/datos/orquestador.sqlite3")

# Cada cuánto se purgan los ids viejos y las alertas viejas.
SEGUNDOS_PURGA = 3600

# Cuánto se guarda cada cosa. Los ids van antes porque son los que crecen más
# rápido: hay uno por cada alerta publicada, más los duplicados que se descartan.
#
# Estas dos cifras son la razón de que `alertas` NO tenga llave foránea a
# `ids_procesados`: si la tuviera, al purgar los ids a los 7 días, la alerta de
# hace diez días se perdería en cascada, o la purga fallaría.
DIAS_PURGA_IDS = 7
DIAS_PURGA_ALERTAS = 30

# --- Registro --------------------------------------------------------------

NIVEL_LOG = os.environ.get("NIVEL_LOG", "INFO").upper()


def resumen() -> dict:
    """Un resumen sin secretos, para el log de arranque.

    No incluye el token ni las contraseñas, ni siquiera recortadas. Si alguien
    pega este log en un chat, no se Filtersa nada.
    """
    return {
        "mqtt": "%s:%d" % (MQTT_HOST, MQTT_PUERTO),
        "usuario_mqtt": MQTT_USUARIO,
        "password_mqtt": "presente" if MQTT_ORQUESTADOR_PASSWORD else "FALTA",
        "client_id": MQTT_CLIENT_ID,
        "sesion_limpia": MQTT_SESION_LIMPIA,
        "telegram": "configurado" if TELEGRAM_BOT_TOKEN else "SIN TOKEN",
        "bot": "activo" if TELEGRAM_ACTIVAR_BOT else "inactivo",
        "panel": PANEL_URL if PANEL_URL else "vacio",
        "db": RUTA_DB,
    }
