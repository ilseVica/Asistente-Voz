"""Las constantes del contrato MQTT del nodo, en un solo lugar.

Están separadas del simulador para que el orquestador pueda importar este archivo
y no se desincronicen. No importa nada: solo texto.

Los tópicos y los QoS están en `contrato.md` del plan, Fase 4, y en el `acl.conf`
del servidor. Si cambia uno de los tres, hay que cambiar los cuatro.
"""

# --- Tópicos ---------------------------------------------------------------

TOPICO_LUZ_SET = "casa/sala/luz/set"
TOPICO_LUZ_ESTADO = "casa/sala/luz/estado"
TOPICO_SENSORES = "casa/sala/sensores"
TOPICO_NODO_ESTADO = "casa/sala/nodo/estado"
TOPICO_NODO_CONFIG = "casa/sala/nodo/config"
TOPICO_PRUEBA_GAS = "casa/sala/nodo/prueba_gas"
TOPICO_ALERTA_GAS = "casa/sala/alerta/gas"
TOPICO_ALERTA_AYUDA = "casa/sala/alerta/ayuda"
TOPICO_ALERTA_INACTIVIDAD = "casa/sala/alerta/inactividad"

# --- QoS -------------------------------------------------------------------

QOS_ORDENES = 1  # luz/set. Se confirma, porque el emisor necesita saber que llegó.
QOS_ESTADO = 0  # luz/estado, sensores, nodo/estado. Se publica y se sigue.
QOS_ALERTAS = 1  # alerta/gas, alerta/ayuda. No se pierden en una desconexión.
QOS_PRUEBA_GAS = 1  # nodo/prueba_gas, que es una orden del servidor.

# --- Gas -------------------------------------------------------------------
#
# COPIA de firmware/include/config.h y del contrato del plan. Si cambia alli,
# cambia aqui, o el simulador y el ESP32 empiezan a abrir y cerrar episodios en
# momentos distintos, y una prueba que pasa con el simulador falla en el
# hardware.
#
# Los dos valores por defecto son los de `nodo/config` en el plan (linea 188):
# el episodio abre con 3 lecturas seguidas de 1800 o mas, y cierra cuando la
# lectura baja de 1550 durante 10 segundos seguidos.
GAS_ON_ADC_POR_DEFECTO = 1800
GAS_OFF_ADC_POR_DEFECTO = 1550
GAS_LECTURAS_PARA_ABRIR = 3
GAS_CIERRE_SOSTENIDO_S = 10
PERIODO_LECTURA_GAS_S = 1.0
GAS_ADC_MAXIMO = 4095

# El firmware publica `nodo/estado` cada 60 s (PERIODO_NODO_ESTADO_MS MS_60S en
# config.h). El panel decide si el sitio esta conectado con esa lectura (90 s de
# plazo), asi que esta es una de las decisiones compartidas: si el simulador
# publicara solo al conectar y al cambiar el gas, la placa lo mantendria verde
# mientras aqui el panel lo marcaria "sin conexion" cada par de minutos.
PERIODO_NODO_ESTADO_S = 60.0

# El gas normal, que es el valor que el firmware usa como "sin gas"
# (SIM_GAS_POR_DEFECTO).
GAS_NORMAL = 312

# --- Sentido del estado de la luz ------------------------------------------
#
# `luz/estado` es un topico RETENIDO que escribe quien publica el ultimo. Si el
# simulador se suscribiera a el, tendria que poder escribirlo para poder deshacer
# una suscripcion, y no puede: el `acl.conf` no le da lectura a `nodo` en
# `casa/sala/luz/estado`. Escribo eso aqui porque la version anterior del
# simulador se suscribia, y con MQTT 3.1.1 un rechazo por ACL no da error: el
# log decia "suscrito" y no llegaba nada nunca.

# --- Retención -------------------------------------------------------------

RETENIDO_SI = True  # luz/estado y nodo/estado
RETENIDO_NO = False  # sensores y las alertas

# --- Identidad -------------------------------------------------------------

# Client id del ESP32 real. El simulado usa otro, porque dos clientes con el mismo
# id en el mismo broker se expulsan de su sesión: el último que entra echa al
# primero. El simulador y el ESP32 pueden estar conectados a la vez durante las
# pruebas, así que los ids tienen que ser distintos.
CLIENT_ID_ESP32 = "nodo-sala"
CLIENT_ID_SIMULADO = "nodo-simulado"

USUARIO_MQTT = "nodo"

# MAC del ESP32 real, en minúsculas y sin dos puntos: el prefijo del id de alerta.
MAC_ESP32 = "8cabb0"

# Pseudo-MAC del simulador. Tiene que verse hexadecimal para respetar el formato
# del contrato, aunque no sea una MAC real. "000000" es la opción menos confusa:
# es obviamente de mentira, y a la vez cabe en los seis caracteres que pide el
# formato. Se usa "sim001" o "simulad" en versiones previas de este archivo; ambos
# rompían el formato de seis hexadecimales.
MAC_SIMULADO = "000000"


def id_alerta(mac: str, arranque: int, contador: int) -> str:
    """Arma un id de alerta con el formato del contrato.

    El formato es `<6 hex> - b<4 hex> - <4 hex>`, y describe tres cosas: qué
    dispositivo la mandó, en qué momento, y cuántas veces.

    La version anterior de esta función tomaba solo un contador y ponia un cero en
    el ultimo bloque, y el simulador lo llamaba siempre con un contador que
    arrancaba en 1. Con eso, dos ejecuciones distintas del simulador producían el
    MISMO id para la primera alerta, y el orquestador la descartaba por duplicada
    sin avisar: el segundo `gas 3000` de una demostración no llegaba a Telegram y
    no había ningún error en ninguna parte. Es justo el fallo que la P28 del plan
    comprueba en el ESP32 ("se reinicia y la alerta llega con un id nuevo").

    Los dos números son persistentes, igual que en el firmware, donde salen de NVS:
    `arranque` cuenta los arranques del ESP32 (`persistencia_iniciar`) y `contador`
    las alertas que ha publicado (`siguiente_contador`). Por eso el id del hardware
    no se repite nunca, y por eso el simulador necesita su propio archivo donde
    llevar la cuenta.

    Se usa `b` en ambos bloques y no `m` o `c`, porque el `snprintf` del firmware es
    "%s-b%04x-%04x" y el orquestador valida el prefijo `b`.
    """
    return "%s-b%04x-%04x" % (mac, arranque & 0xFFFF, contador & 0xFFFF)
