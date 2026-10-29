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
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", os.environ.get("MQTT_CLIENT_ID_PANEL", os.environ.get("PANEL_MQTT_CLIENT_ID", "orquestador")))

# `clean_session=False` es lo que habilita la cola del broker. Sin esto, las
# alertas de gas que llegan durante un corte se pierden, y el aviso nunca llega a
# Telegram. No es un detalle: es la diferencia entre que la familia se entere o
# no.
#
# La sesión se decide POR CLIENT ID y por la variable explícita del compose, en
# ese orden, para que los dos servicios no puedan expulsarse mutuamente (P26).
# - orquestador: client id "orquestador", sesión persistente (`false`), que es
#   la que hace que el broker le guarde las alertas mientras está detenido.
# - panel: client id "panel-api", sesión limpia (`true`): no tiene sesión que
#   recuperar y no debe heredar la del orquestador.
# El valor por defecto del código sigue siendo el del orquestador, pero el
# compose lo fija en los dos servicios, así que lo que manda es la variable.
_env_clean = os.environ.get("MQTT_SESION_LIMPIA")
if _env_clean is not None:
    MQTT_SESION_LIMPIA = _env_clean.lower() == "true"
else:
    MQTT_SESION_LIMPIA = (MQTT_CLIENT_ID == "panel-api")

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
# La alerta de inactividad (Fase 4) la ORIGINA este servicio, no la recibe: el
# reloj de inactividad vive aqui (`inactividad.py`) y publica en este topico, que
# el panel escucha. La familia la recibe por Telegram por la cola, como las demas.
TOPICO_ALERTA_INACTIVIDAD = "casa/sala/alerta/inactividad"

# Donde se publica lo que el asistente dice en voz alta. Lo usa el bot cuando el
# familiar pulsa "Ya lo atendi", para que el sitio se entere de que alguien ya
# está con el aviso. Todavía no hay satélite, así que esto solo se ve escuchando
# el broker con el usuario `satelite`.
TOPICO_SATELITE_DECIR = "casa/sala/satelite/decir"

# El texto que reconoce el servicio de voz (etapa A del Paso 2). El orquestador lo
# lee para decidir la intencion (Paso 3); por ahora, en esta etapa, solo hace el
# eco de prueba de mas abajo. Lo publica el usuario `voz`, y es el unico topico
# que ese usuario puede escribir.
TOPICO_VOZ_TEXTO = "casa/sala/voz/texto"

QOS_ALERTAS = 1

# Donde esta el sensor de gas, para el aviso de Telegram («El sensor de la cocina
# detectó gas»). Formato: el nombre del lugar en minusculas, con o sin articulo
# (`cocina`, `la cocina`, `el cuarto de lavado`). Sin articulo, lo pone
# `textos.lugar_con_articulo`; un lugar que no este en su tabla conviene escribirlo
# con el suyo. Sin valor, «cocina».
UBICACION_GAS = os.environ.get("UBICACION_GAS", "").strip() or "cocina"

# El eco de voz. Con 1, el orquestador publica en `satelite/decir` lo que
# entendio ("Usted dijo: ...", ver textos.py), para poder demostrar el camino completo (audio ->
# texto -> respuesta) sin el satelite, que todavia no existe. Con 0, solo lo
# registra en el log.
#
# Por defecto 1 para que la etapa A sea visible tal cual, y para que la prueba de
# punta a punta del arnes pueda mirar el eco. Es una ayuda de la etapa A, no una
# funcion del producto: cuando exista el satelite (etapa B), el `.env` lo pone a 0.
ECO_VOZ = os.environ.get("ECO_VOZ", "0").strip() not in ("0", "false", "no", "")

# Ventana del antirrebote de ayuda, en segundos (P05). El ESP32 ya ignora nuevas
# peticiones durante 60 s; este es el segundo filtro, para el caso de que la alerta
# la publique el servidor (voz) y no el nodo.
ANTIRREBOTE_AYUDA_S = 60

# Rango del ADC del MQ-2 con atenuación de 11 dB, que es lo que lee `analogRead()`.
ADC_MINIMO = 0
ADC_MAXIMO = 4095

# El umbral con el que el firmware decide que hay gas, en cuenta del ADC.
#
# El ESP32 compara su lectura contra los suyos (aquel `GPIO_GAS_UMBRAL_*` del
# firmware), pero el panel y el contrato necesitan los números para enseñar la
# barra y para decir «Lectura: 312 / Umbral: 1800». Como el umbral del firmware
# no viaja en `nodo/estado` (verificado el 2026-10-20), el orquestador publica el
# suyo en `casa/sala/nodo/config` (retenido, QoS 1) y el panel lo lee de la
# instantánea. Si el firmware cambia, cambian estas dos variables (o el `.env`),
# y no hay que tocar el código.
#
# Reglas, iguales a las del firmware: los dos dentro del rango del ADC, y
# `GAS_OFF_ADC` SIEMPRE por debajo de `GAS_ON_ADC` (si no, el relé encendido
# nunca se soltaría). Un valor que no cumpla no se usa: se vuelve al par por
# defecto, porque un umbral publicado y mal leído es peor que el de siempre.
GAS_ON_ADC = int(os.environ.get("GAS_ON_ADC", "1800"))
GAS_OFF_ADC = int(os.environ.get("GAS_OFF_ADC", "1550"))
if not (ADC_MINIMO < GAS_OFF_ADC < GAS_ON_ADC < ADC_MAXIMO):
    GAS_ON_ADC = 1800
    GAS_OFF_ADC = 1550

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

# `PANEL_URL` (el botón "Abrir panel" y los enlaces de acceso) se declara mas
# abajo, en la sección del panel web, con el resto de sus variables.

TELEGRAM_ACTIVAR_BOT = os.environ.get("TELEGRAM_ACTIVAR_BOT", "0") == "1"

# El client id del panel. Fijo, como el del orquestador y por el mismo motivo: si
# hubiera dos instancias, el broker expulsa a la primera y queda una sola, en vez
# de acumular dos conexiones con el mismo estado en memoria.
MQTT_CLIENT_ID_PANEL = os.environ.get("MQTT_CLIENT_ID_PANEL", "panel-api")

# `clean_session=True` para el panel, que es lo contrario que para el orquestador y
# por el mismo motivo de siempre: la sesión persistente existe para que el broker
# guarde lo que llega mientras el proceso está caído, y el panel no tiene nada que
# recuperar. Sus retenidos (`luz/estado`, `nodo/estado`, `satelite/estado`) le
# llegan al suscribirse, que es de donde los saca, y lo que se publica mientras está
# caído son órdenes y alertas que ya están en la base y en la cola del orquestador.
# Pedirle al broker que guarde mensajes para el panel sería ocupar espacio sin que
# nadie los lea.
MQTT_SESION_LIMPIA_PANEL = True

# Los tópicos que lee el panel. Los cuatro primeros los publica el nodo (la luz, los
# sensores y su propio estado) y el satélite (su estado); el quinto lo escribe el
# puente al conectarse, dentro de `$SYS`.
#
# Duplicar los nombres aquí es lo que hace que un cambio en el contrato se note en
# un solo sitio: si el ESP32 cambia uno, estas cuatro líneas son las que hay que
# cambiar con él, y si no, el panel se queda mirando un tópico que ya no existe y
# muestra «sin datos» sin decir por qué.
TOPICO_LUZ_ESTADO = "casa/sala/luz/estado"
TOPICO_LUZ_SET = "casa/sala/luz/set"
TOPICO_SENSORES = "casa/sala/sensores"
TOPICO_NODO_ESTADO = "casa/sala/nodo/estado"
# Un estado POR satelite (Fase 2C): `casa/sala/satelite/<client_id>/estado`. Es
# un patron con `+`: el panel y el orquestador se suscriben a todos a la vez.
TOPICO_SATELITE_ESTADO = "casa/sala/satelite/+/estado"
TOPICO_ENLACE_SITIO = "$SYS/broker/connection/+/state"

# La configuración que el orquestador manda al ESP32 y al panel (Fase 2):
# `{"gas_on_adc":1800,"gas_off_adc":1550}` en JSON, con retain y QoS 1, al
# arrancar (y al reconectar). El ACL ya deja escribirlo al orquestador
# (`servidor/mosquitto/config/acl.conf`) y prohibía LEERLO al panel: eso se
# arregla al desplegar (Fase 3), no aquí.
TOPICO_NODO_CONFIG = "casa/sala/nodo/config"

# El WebSocket. El servidor manda `latido` cada 20 s y espera un `pong`; si pasan
# 45 s sin `pong`, cierra. Los dos lados tienen latido porque el navegador no puede
# mandar un ping de protocolo, y un móvil con la pantalla apagada deja de contestar
# sin avisar de nada.
SEGUNDOS_LATIDO_WS = 20
SEGUNDOS_PONG_WS = 45

# Cuántos eventos caben en la cola de UN cliente antes de darle con la puerta (1013).
# Acotada a propósito: un panel abierto en un móvil con la pantalla apagada es una
# fuga de memoria con forma de intermitencia, y el 1013 hace que al reconectar le
# llegue la instantánea completa en vez de una lista de parches que se perdió.
COLA_EVENTOS_WS = 64

# El límite por IP de `/api/acceso`, como red de seguridad. Con 16 caracteres de un
# alfabeto de 31 (unos 8x10^23) adivinar el código no es viable, así que este límite
# no protege el código: protege de un fallo de programación que convierta el endpoint
# en otra cosa.
INTENTOS_ACCESO_POR_IP = 20
SEGUNDOS_LIMITE_ACCESO = 600

# El canje del código de 6 caracteres sí necesita su propio límite, porque 31^6 se
# recorre en un ataque con miles de intentos por segundo. Dos topes, en memoria, los
# dos en `seguridad.LimiteAccesos`:
#   - Por IP real: 5 fallos cada 10 minutos. Frena a quien prueba desde una casa.
#   - Global: 50 fallos cada 10 minutos entre TODAS las IPs. Es el que frena a quien
#     reparte el ataque entre muchas IPs, que es el que el primero no ve.
# Los dos son de fallos, no de intentos: un canje que sale bien no gasta cupo.
INTENTOS_CODIGO_POR_IP = 5
SEGUNDOS_LIMITE_CODIGO = 600
FALLOS_CODIGO_GLOBALES = 50
SEGUNDOS_LIMITE_CODIGO_GLOBAL = 600

# El límite por IP de los 401 del panel (tokens de sesión y WebSocket). El token
# tiene 43 caracteres, así que no protege el token: protege, igual que el de
# arriba, de que un fallo convierta el endpoint en otra cosa. Separado del de
# canje para no gastar el presupuesto de intentos de acceso con intentos de
# sesión: una lluvia de `Authorization:` inventadas no debe dejar sin abrir la
# puerta al vecino que sí tiene el código.
INTENTOS_TOKEN_POR_IP = 60
SEGUNDOS_LIMITE_TOKEN = 600

# Las esperas de reconexión del WebSocket del cliente, en segundos. La última se
# repite para siempre: un panel que deja de reintentar no es un panel que espera, es
# un panel que se ha caído. El desorden aleatorio de ±20 % lo pone el cliente, para
# que dos teléfonos no reintenten a la vez después de un corte del servidor.
ONDAS_RECONEXION_S = (1, 2, 4, 8, 15, 30)

# El panel NO usa sesión persistente (`clean_session=True`). No hay nada que
# recuperar: los retenidos (`luz/estado`, `nodo/estado`) llegan al suscribirse, y lo
# que se publica mientras el panel está caído son órdenes y alertas que ya están
# en SQLite y en la cola del orquestador. Pedirle al broker que guarde mensajes
# para el panel sería ocupar espacio sin que nadie los lea.

# --- Panel web --------------------------------------------------------------
#
# Lo que hay aquí son las variables del SERVICIO DEL PANEL. El bot solo necesita
# `PANEL_URL`, que ya estaba. El resto lo lee `panel/api.py`, que es quien abre
# los sockets.

# La URL que va en el botón y en los enlaces de acceso. Tiene que ser la COMPLETA
# de Tailscale (`villaca.tail722206.ts.net`): Telegram rechaza en el envío una URL
# sin dominio, con «wrong http url», así que un nombre corto funcionaría en el
# panel y nunca se publicaría en el botón. Verificado el 2026-10-05.
PANEL_URL = os.environ.get("PANEL_URL", "")

# Dónde ESCUCHA el panel, como lista de direcciones IP separadas por espacios.
# Por defecto, solo la de Tailscale: el panel lo pueden abrir los familiares de
# la lista y nadie más. En modo instalación se añade la IP de la casa, para que un
# familiar que esté en casa sin Tailscale pueda abrirlo.
#
# Es una lista y no un valor único porque `network_mode: host` no admite varios
# `--host`: `panel/api.py` abre un socket por dirección y se los pasa a uvicorn.
# `0.0.0.0` no está en ninguna parte a propósito.
#
# Sin valor, la lista es `127.0.0.1`: si alguien arranca el panel sin configurando nada,
# lo único que escucha es el propio servidor y nadie más. Lo contrario, un valor por
# defecto que abriera a la red, haría que olvidar una variable abriese el panel de la
# familia a cualquiera que llegara al puerto.
PANEL_DIRECCIONES = [
    d for d in os.environ.get("PANEL_DIRECCIONES", "").replace(",", " ").split()
    if d
] or ["127.0.0.1"]

PANEL_PUERTO = int(os.environ.get("PANEL_PUERTO", "8080"))

# El modo decide de dónde sale el estado del sitio (4.4 de la propuesta):
#
#   instalacion  -> el sitio es el nodo, y su estado sale de `nodo/estado`. Es el
#                   modo en que está el sistema hoy: el ESP32 está en la misma red
#                   y publica directo al broker, sin puente.
#   demostracion -> el sitio es la conexión del sitio, y su estado sale del
#                   retained que escribe el puente al conectarse.
#
# Sin esta variable el panel mostraría «sitio sin conexión» para siempre en modo
# instalación, porque `$SYS/broker/connection/puente-sitio/state` no lo escribe
# nadie cuando no hay puente. Un cartel de alarma que nunca se apaga es peor que
# no tenerlo.
MODOS_SITIO = ("instalacion", "demostracion")
MODO_SITIO = os.environ.get("MODO_SITIO", "instalacion").strip().lower()
if MODO_SITIO not in MODOS_SITIO:
    MODO_SITIO = "instalacion"

# Cuánto pasa sin lectura para que un componente se dé por caído (P22, P23).
SEGUNDOS_VISTO_S = int(os.environ.get("PANEL_SEGUNDOS_VISTO_S", "90"))

# Cuánto vive una sesión del panel, y cuánto se guarda lo que ya no sirve.
#
# 30 dias es el plazo que aprobó el usuario para el token, y coincide con el del
# historial de alertas: es el tiempo que el familiar se queda «dentro» sin volver
# a hacer `/panel`. Caduca SOLO por tiempo, nunca por usar el panel, porque un
# token que se renueva solo cada vez que lo tocas es un token que no caduca nunca.
DIAS_TOKEN_PANEL = 30

# Los plazos de purga de las dos tablas del panel. Los leen `db.purgar()`, que
# corre cada hora en el orquestador.
#
# El enlace, un dia. Caduca a los 10 minutos, y esperar 30 dias a tirarlo solo
# ocupa sitio. El token, 30 dias despues de cerrarse: se conserva el rastro el
# mismo tiempo que dura la sesion, para que el operador pueda ver si una baja
# funciono.
DIAS_PURGA_PANEL_ENLACES = 1
DIAS_PURGA_PANEL_TOKENS = 30

# El usuario MQTT del panel. Lo pone el compose; aquí solo se lee. La contraseña
# tiene su propia variable porque el panel NO es el orquestador y no debe poder
# leer `nodo/config` ni publicar `alerta/inactividad`.
MQTT_USUARIO_PANEL = os.environ.get("MQTT_USUARIO", "panel")
MQTT_PANEL_PASSWORD = os.environ.get("MQTT_PANEL_PASSWORD", "")

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

# --- Secretos ---------------------------------------------------------------
#
# Los valores que NO pueden salir en un log. `sanear()` los lee de aquí, y por eso
# esta lista es el UNICO sitio donde hay que declarar un secreto nuevo.
#
# Antes de estos tres bloques, `sanear()` conocía dos valores: el token de Telegram
# y la contraseña del panel. Escribirlos uno a uno en el filtro es exactamente la
# forma de que el próximo secreto se quede fuera sin que nadie se entere, y el
# coste de ese olvido es alto: una contraseña en `docker logs` es una contraseña
# que se copia, se pega y acaba en un chat.
#
# Por eso aquí NO hay una lista cerrada. Cualquier variable del entorno cuyo
# nombre acabe en `_PASSWORD` o en `_TOKEN` se considera un secreto y se tapa, se
# haya escrito o no en este archivo. `.env` ya cumple el patrón en los seis casos
# (`MQTT_NODO_PASSWORD`, `MQTT_PUERTE_PASSWORD`, `MQTT_ORQUESTADOR_PASSWORD`,
# `MQTT_SATELITE_PASSWORD`, `MQTT_SALUD_PASSWORD`, `MQTT_PANEL_PASSWORD` y
# `TELEGRAM_BOT_TOKEN`), así que un secreto nuevo queda cubierto por añadir la
# línea al `.env` y no al código.
#
# `VARIABLES_SECRETAS_EXPLICITAS` queda para lo que no siga el patrón: hoy
# `TAILSCALE_AUTHKEY` (la clave del nodo de Funnel, en el `.env` y visible solo
# para el servicio `tailscale` del compose) no acaba en ninguno de los sufijos y
# necesita el sitio donde declararlo en vez de volver a escribir la lista en el
# filtro.
#
# El filtro solo ve lo que hay en `os.environ`, así que cada contenedor tapa lo que
# tiene: el orquestador la contraseña del orquestador, el panel la suya. Cada uno
# recibe por el `compose` solo la que le hace falta, y por eso un servicio no puede
# filtrar un secreto que no tiene.
SUFIJOS_DE_SECRETO = ("_PASSWORD", "_TOKEN", "_SECRET", "_SECRETO", "_CLAVE")

# Lo que NO se tapa por no acabar en nada de lo de arriba. Ver `secretos()`.
VARIABLES_SECRETAS_EXPLICITAS = ("TAILSCALE_AUTHKEY",)

# Por debajo de esta longitud no se tapa nada. Un secreto de dos o tres letras no es
# una contraseña: es una bandera, y tapar «on» o «si» en cada línea del log deja el
# log ilegible, que es como se acaba sin leer un log que sí tenía un secreto. Las
# contraseñas que genera `mosquitto/crear-usuarios.sh` tienen 24 caracteres o más, así
# que el umbral no deja pasar ninguna de verdad.
LARGO_MINIMO_DE_SECRETO = 4


def secretos() -> tuple:
    """Los valores secretos del entorno, de más largo a más corto.

    Lo devuelve `sanear()`, y no al revés, porque la lista se DECLARA aquí y se USA
    allí. Al revés (que `sanear.py` Diazara los nombres) el filtro volvería a ser
    una lista que hay que mantener en dos sitios, que es el problema que esto
    arregla.

    De más largo a más corto, y no en el orden del entorno, por un motivo concreto:
    si un secreto es prefijo de otro, tapar el corto primero deja el final del
    largo a la vista. Ordenando por longitud descendente, el largo se tapa entero
    antes de que se intente el corto.

    Solo los valores. Los nombres no se devuelven porque quien los necesita para
    escribir un log es `config.resumen()`, que lo tiene por su cuenta.
    """
    encontrados = set()
    for nombre, valor in os.environ.items():
        if not valor:
            continue
        if not (nombre.endswith(SUFIJOS_DE_SECRETO)
                or nombre in VARIABLES_SECRETAS_EXPLICITAS):
            continue
        if len(valor) < LARGO_MINIMO_DE_SECRETO:
            continue
        encontrados.add(valor)
    return tuple(sorted(encontrados, key=len, reverse=True))

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

# Cuánto se conserva un recordatorio cancelado o ya dicho. Es más largo que el de
# las alertas a propósito: un recordatorio es «qué tenía que hacer la persona que
# vive en la casa», y la familia quiere poder reconstruir qué se le avisó durante
# más tiempo que el historial de gas. La purga solo borra los INACTIVOS (activo=0)
# cuyo `purgable_en` ya venció; un recordatorio diario activo no se toca.
DIAS_PURGA_RECORDATORIOS = 90

# Cada cuánto revisa el orquestador si algún recordatorio venció. La precisión que
# hace falta es de minutos, no de segundos, así que 30 s sobra y no cuesta nada.
# Es el mismo patrón que `SEGUNDOS_PURGA`: un hilo aparte con su `time.sleep`.
SEGUNDOS_CICLO_RECORDATORIOS = 30

# La ventana en la que un recordatorio vencido sin satélite conectado todavía se
# dice al conectarse uno, con `destino` a ese satélite. Pasada la ventana se marca
# `no_entregado` y no se dice: un recordatorio de «tomar la medicina» que llega dos
# horas tarde es peor que no llegar, porque descoloca sin avisar a tiempo.
VENTANA_RECORDATORIO_PENDIENTE_S = 30 * 60

# --- Inactividad (Fase 4) ---------------------------------------------------
#
# Cuánto tiempo sin movimiento en la sala dispara la alerta a la familia. El
# número VIVO no está aquí: vive en la tabla `configuracion` de SQLite, porque la
# familia lo ajusta desde el panel y el orquestador lo tiene que releer sin
# reiniciarse. Estas tres constantes son solo el valor de fábrica y el rango que
# la API acepta; `db.tiempo_inactividad()` devuelve el valor guardado o, si no
# hay ninguno, `TIEMPO_INACTIVIDAD_MIN_DEFECTO`.
TIEMPO_INACTIVIDAD_MIN_DEFECTO = 120
TIEMPO_INACTIVIDAD_MIN_MINIMO = 2
TIEMPO_INACTIVIDAD_MIN_MAXIMO = 720

# Cada cuánto mira el orquestador si la casa lleva demasiado tiempo sin
# movimiento. La precisión que hace falta es de minutos, así que 10 s sobra; es
# el mismo patrón que `SEGUNDOS_CICLO_RECORDATORIOS`, un hilo aparte con su
# `parando.wait`. No se reacciona a cada mensaje de sensores para que un ajuste
# del panel se aplique en la vuelta siguiente sin reiniciar nada.
SEGUNDOS_CICLO_INACTIVIDAD = 10

# Un tópico `$SYS` de enlace solo BLOQUEA la alerta si trae un 0 EXPLÍCITO. En
# modo instalación no hay puente, el tópico no existe, y entonces no llega nada:
# «sin noticia» se trata como «conectado», no como «caído». Es una decisión de
# ajuste del 2026-10-16 y la razón es que un `0` retenido de un puente que ya no
# está dejaría la alerta apagada para siempre sin que nadie lo notara.
ENLACE_SITIO_VALORES_CAIDO = ("0", "false", "off", "no")

# --- Chat de dos vias (Paso 6) ---------------------------------------------

# Ventana de respuesta tras decir un mensaje de chat, en segundos.
VENTANA_RESPUESTA_CHAT_S = int(os.environ.get("VENTANA_RESPUESTA_CHAT_S", "60"))

# Limite de caracteres de un mensaje a la sala.
LIMITE_CHAT_CARACTERES = 200

# Ritmo maximo por familiar: un mensaje cada 5 segundos.
PAUSA_CHAT_S = 5

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
        "modo_sitio": MODO_SITIO,
        "ubicacion_gas": UBICACION_GAS,
        "db": RUTA_DB,
    }

# Satelite al que el panel manda "mantener para hablar" (Fase del boton de voz).
SATELITE_HABLAR = os.environ.get("PANEL_SATELITE_HABLAR", "satelite-sala")
