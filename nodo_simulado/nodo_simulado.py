"""Simulador del nodo ESP32.

Habla MQTT con el mismo usuario, los mismos tópicos, los mismos formatos y las
mismas reglas que el firmware real, para poder desarrollar y probar el resto del
sistema sin el ESP32 conectado. No es un emulador del firmware: no simula el
hardware, simula el nodo en la red.

La regla que gobierna este archivo es que donde el simulador y el ESP32 tienen que
tomar la misma decisión, el simulador la toma igual. Si el simulador acepta un
mensaje que el ESP32 rechaza, o abre un episodio de gas antes, una prueba pasa
aquí y falla en la placa, que es el peor resultado posible de un simulador. Por eso
las reglas de `luz/set`, las de la alarma de gas y las de `prueba_gas` son copias
de `firmware/src/contrato.cpp`, `firmware/src/gas.cpp` y
`firmware/include/config.h`.

Para usarlo hacen falta dos cosas: el broker levantado en `servidor/`, y la
contraseña de `nodo` en el entorno. Ver `README.md`.

    python3 -m venv .venv
    .venv/bin/pip install -r requerimientos.txt
    set -a; . ../servidor/.env; set +a
    .venv/bin/python nodo_simulado.py
"""

import json
import os
import random
import select
import signal
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import contrato

# --- Contadores persistentes ----------------------------------------------
#
# COPIA de `firmware/src/persistencia.cpp`, que guarda los dos numeros del id de
# alerta en NVS. El simulador los guarda en un archivo JSON.
#
# Sin esto, reiniciar el simulador reiniciaba los contadores y la primera alerta
# de la nueva ejecución salía con el MISMO id que la primera de la anterior. El
# orquestador la descartaba por duplicada, sin avisar: el `gas 3000` de la
# demostración se perdía en silencio. La P28 del plan pide lo contrario para el
# ESP32 ("se reinicia y la alerta llega con un id nuevo"), asi que el simulador
# tiene que comportarse igual.
#
# El archivo se llama `contadores.json` y NO `estado.json`. La primera versión de
# estos contadores usaba `estado.json`, que es el archivo que escribe
# `escribir_estado()` con el pid y la hora, y los dos se pisaban: el contador leía
# un JSON sin las claves que buscaba, contaba cero y encima lo sobrescribía con sus
# dos números. El síntoma era que el reinicio no arreglaba nada y los ids se
# repetían, y la causa era un nombre de archivo repetido en el mismo script.
RUTA_CONTADORES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "contadores.json")

arranque_actual = 0
contador_alertas = 0

# --- Estado ----------------------------------------------------------------

# Los mismos valores por defecto que el firmware, para que nodo/estado del
# simulado tenga la misma forma que el del ESP32.
luz_encendida = False
temperatura = 21.0
humedad = 45.0
presencia = True
alertas_pendientes = 0

# La ventana de antiguedad de luz/set. COPIA de ORDEN_MAX_ANTIGUEDAD_S de
# firmware/include/config.h. Si cambia alli, cambia aqui, o el simulador y el
# ESP32 empiezan a aceptar mensajes distintos.
ORDEN_MAX_ANTIGUEDAD_S = 10

# Umbrales de gas. Vienen de `contrato.py`, que los copia de
# `firmware/include/config.h` y del `nodo/config` del plan.Arrancan en los valores
# por defecto del contrato y los cambia `nodo/config`, que es lo que hace el
# firmware con ellos (los guarda en memoria no volatil y sigue funcionando con el
# ultimo valor aunque no haya servidor).
gas_on_adc = contrato.GAS_ON_ADC_POR_DEFECTO
gas_off_adc = contrato.GAS_OFF_ADC_POR_DEFECTO

# La ultima lectura de gas. La inicializa `sensores_leer()` en el firmware con
# SIM_GAS_POR_DEFECTO, y aqui con el mismo valor.
gas_adc = contrato.GAS_NORMAL

# La alarma de gas, con la misma maquina de estados que `firmware/src/gas.cpp`:
#
#   1. Muestreo a 1 Hz. Una lectura por segundo, no por vuelta del bucle: a la
#      cadencia del bucle las tres lecturas se contarian en milisegundos y un
#      pico de ruido abriria un episodio.
#   2. El episodio abre con TRES lecturas seguidas iguales o mayores que
#      gas_on_adc, y genera UNA alerta.
#   3. Cierra cuando la lectura lleva GAS_CIERRE_SOSTENIDO_S seguidos por debajo de
#      gas_off_adc. Si vuelve a subir antes, se cancela el cierre y NO hay alerta
#      nueva: sigue siendo el mismo episodio.
episodio_gas_abierto = False
lecturas_sobre_umbral = 0
inicio_bajo_umbral = 0.0
proxima_lectura = 0.0

# Cada cuántos segundos se publica el latido de sensores.
SEGUNDOS_LATIDO = 60.0
ultimo_latido = 0.0

# Valor forzado por el comando de consola o por `prueba_gas`. Mientras no sea None,
# la lectura muestreada es ese valor en lugar del del sensor.
gas_forzado = None

# Oscilación alrededor del umbral, para la P09. Es un estado del script de
# propósito: la P09 pide que el gas oscile durante un minuto y salga UN solo
# episodio, y eso solo se puede demostrar si el valor cambia el solo.
oscilando = False

salir = False


def ahora() -> float:
    return time.time()


def estado_cargar() -> None:
    """Lee los contadores del arranque anterior y suma uno al de arranques.

    COPIA de `persistencia_iniciar`. Si el archivo no existe o está dañado, se
    arranca de cero: es la primera ejecución, o el archivo se corrompió, y en los
    dos casos seguir es mejor que negarse a arrancar. Un simulador que no arranca
    no sirve ni para probar ni para explicar el problema.
    """
    global arranque_actual, contador_alertas

    arranque_actual = 0
    contador_alertas = 0
    try:
        with open(RUTA_CONTADORES, "r", encoding="utf-8") as archivo:
            guardado = json.load(archivo)
        if isinstance(guardado, dict):
            arranque_actual = int(guardado.get("arranque", 0))
            contador_alertas = int(guardado.get("contador", 0))
    except (OSError, ValueError, TypeError):
        # Un archivo ilegible se aparta, para que el siguiente guardado no falle
        # por culpa de un archivo que nadie va a mirar.
        try:
            os.replace(RUTA_CONTADORES, RUTA_CONTADORES + ".roto")
        except OSError:
            pass

    arranque_actual += 1
    estado_guardar()


def estado_guardar() -> None:
    """Escribe los contadores. Es el equivalente de `nvs.putUShort` del firmware."""
    try:
        temporal = RUTA_CONTADORES + ".tmp"
        with open(temporal, "w", encoding="utf-8") as archivo:
            json.dump({"arranque": arranque_actual, "contador": contador_alertas},
                      archivo)
        os.replace(temporal, RUTA_CONTADORES)
    except OSError as error:
        # Si no puede guardar, el simulador sigue: pierde la unicidad del id entre
        # ejecuciones, y hay que decirlo, porque el síntoma (una alerta descartada
        # por duplicada) aparece más tarde y en otro proceso.
        log("simulador", "no se pudieron guardar los contadores de id en %s (%s); "
            "los ids se podrían repetir si se reinicia el simulador"
            % (RUTA_CONTADORES, error.strerror))


def siguiente_id_alerta() -> str:
    """Un id nuevo, con los dos contadores ya guardados.

    Equivale a `id_alerta_construir(persistencia_arranque_actual(),
    siguiente_contador())` del firmware. El contador se incrementa ANTES de armar el
    id, igual que allí, para que el primer aviso de una ejecución sea el 1 y no el 0.
    """
    global contador_alertas
    contador_alertas += 1
    estado_guardar()
    return contrato.id_alerta(contrato.MAC_SIMULADO, arranque_actual, contador_alertas)


def log(etiqueta: str, mensaje: str, *valores) -> None:
    """Una línea por evento, con la hora local.

    Acepta los mismos argumentos de formato que un `printf`: `log("gas",
    "episodio abierto con %d", valor)`. Se formatea aquí y no en cada llamada,
    para que ningún mensaje se quede a medias si falta un argumento: un
    `TypeError` dentro de un manejador de MQTT mata el hilo de red de paho, y el
    proceso sigue vivo pero deja de recibir mensajes.

    Sin niveles de log como el firmware, pero con la misma forma: etiqueta entre
    corchetes, porque cuando hay dos procesos hablando en el monitor (el broker y
    el simulador) hay que saber de quién es cada línea.
    """
    if valores:
        try:
            mensaje = mensaje % valores
        except (TypeError, ValueError) as error:
            mensaje = "%s [formateo fallido: %s]" % (mensaje, error)
    sello = datetime.now().astimezone().strftime("%H:%M:%S")
    print("%s [%s] %s" % (sello, etiqueta, mensaje), flush=True)


# El archivo de "el proceso esta vivo", para el humano y para las pruebas de humo.
# No tiene nada que ver con los contadores de arriba: uno cuenta y el otro
# describe, y por eso tienen dos nombres distintos en vez de pelearse por uno.
RUTA_VIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "vivo.json")


def escribir_estado(conectado: bool) -> None:
    """Deja un archivo de estado para que se sepa que el simulador esta vivo.

    No es parte del contrato MQTT ni lo lee nadie: es para el humano y para las
    pruebas de humo. Se escribe en el archivo y no en un topic porque asi se puede
    mirar sin encender el broker.

    Si el directorio no permite escribir, se avisa una vez y se sigue. Que un
    archivo no se pueda escribir no es motivo para que el simulador no publique.
    """
    carga = {
        "pid": os.getpid(),
        "client_id": contrato.CLIENT_ID_SIMULADO,
        "usuario": contrato.USUARIO_MQTT,
        "mac_simulada": contrato.MAC_SIMULADO,
        "conectado": conectado,
        "actualizado": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(RUTA_VIVO, "w", encoding="utf-8") as archivo:
            json.dump(carga, archivo, indent=2)
            archivo.write("\n")
    except OSError as error:
        log("aviso", "no se pudo escribir %s (%s); se sigue"
            % (os.path.basename(RUTA_VIVO), error))


# --- Publicaciones ---------------------------------------------------------


def publicar_luz_estado(origen: str) -> None:
    """luz/estado, QoS 0 y retenido.

    Retenido, porque es el estado actual: si alguien se suscribe tarde, o si el
    ESP32 se reinicia, tiene que saber cómo está la luz sin esperar al siguiente
    cambio. Por eso el simulador lo publica una vez al conectarse.
    """
    carga = {
        "estado": "on" if luz_encendida else "off",
        "origen": origen,
        "ts": int(ahora()),
    }
    cliente.publish(
        contrato.TOPICO_LUZ_ESTADO,
        json.dumps(carga, separators=(",", ":")),
        qos=contrato.QOS_ESTADO,
        retain=contrato.RETENIDO_SI,
    )
    log("luz", "estado %s, origen %s" % (carga["estado"], origen))


def publicar_nodo_estado(conectado: bool = True) -> None:
    """nodo/estado, QoS 0 y retenido.

    Los mismos ocho campos que el firmware. El `ts` es un entero epoch o `null`,
    igual que en `luz/set`: el mismo campo tiene que ser del mismo tipo en todos
    los tópicos, o el consumidor tiene que adivinar de dónde viene cada valor.
    """
    carga = {
        "conectado": conectado,
        "ts": int(ahora()) if conectado else None,
        "ip": os.environ.get("SIMULADO_IP", "10.0.0.99"),
        "fw": "simulador-1.0",
        "gas_adc": gas_adc,
        "alertas_pendientes": alertas_pendientes,
        "gas_episodio": episodio_gas_abierto,
        "alexa": False,
    }
    cliente.publish(
        contrato.TOPICO_NODO_ESTADO,
        json.dumps(carga, separators=(",", ":")),
        qos=contrato.QOS_ESTADO,
        retain=contrato.RETENIDO_SI,
    )
    log("nodo", json.dumps(carga, separators=(",", ":")))


def publicar_sensores(forzar: bool = False) -> None:
    """sensores, QoS 0 y SIN retención.

    Sin retención a propósito: este tópico es una serie de lecturas, no un estado.
    Retenerlo dejaría en el broker el último valor para siempre, y cualquier
    suscriptor nuevo recibiría una lectura vieja como si fuera actual.

    Los valores se mueven en pasos pequeños alrededor de la media, para que el
    filtro de cambios mínimos del firmware (0.3 °C, 2 %) tenga algo que filtrar.

    Los cuatro campos son los del contrato (`temp_c`, `hum_pct`, `gas_adc`,
    `presencia`). Antes se publicaban `temp`, `hum` y sin `gas_adc`, que no son los
    nombres del plan, así que el panel no habría podido leerlos.
    """
    global temperatura, humedad, ultimo_latido
    if forzar or (ahora() - ultimo_latido) >= SEGUNDOS_LATIDO:
        temperatura += random.uniform(-0.25, 0.25)
        humedad += random.uniform(-1.5, 1.5)
        temperatura = max(18.0, min(26.0, temperatura))
        humedad = max(30.0, min(70.0, humedad))
        carga = {
            "temp_c": round(temperatura, 1),
            "hum_pct": round(humedad),
            "gas_adc": gas_adc,
            "presencia": presencia,
        }
        cliente.publish(
            contrato.TOPICO_SENSORES,
            json.dumps(carga, separators=(",", ":")),
            qos=contrato.QOS_ESTADO,
            retain=contrato.RETENIDO_NO,
        )
        ultimo_latido = ahora()
        log("sensores", json.dumps(carga, separators=(",", ":")))


def publicar_alerta_gas(valor: int) -> None:
    """alerta/gas, QoS 1 y sin retención.

    El cuerpo tiene EXACTAMENTE los dos campos del contrato: `id` y `gas_adc`
    (plan, línea 182). Antes mandaba `tipo`, `nivel`, `valor` y `ts`, que el
    contrato no define. El orquestador leía `tipo` y `nivel`, así que una alerta
    con el formato del contrato se leía con todo vacío; y el nivel lo inventaba el
    propio simulador con un `>= 2000`, que es una regla que solo existe en este
    archivo y en ningún otro sitio.

    Sin retención, porque una alerta es un hecho que pasó en un momento. Si queda
    retenida, un suscriptor que se conecte después la recibe como si acabara de
    ocurrir, y con el mismo `id`.

    El `id` es lo que permite que el orquestador no mande dos veces el mismo aviso
    a Telegram, aunque el mensaje llegue dos veces.
    """
    carga = {
        "id": siguiente_id_alerta(),
        "gas_adc": valor,
    }
    cliente.publish(
        contrato.TOPICO_ALERTA_GAS,
        json.dumps(carga, separators=(",", ":")),
        qos=contrato.QOS_ALERTAS,
        retain=contrato.RETENIDO_NO,
    )
    log("alerta", "gas: " + json.dumps(carga, separators=(",", ":")))


def publicar_alerta_ayuda(origen: str = "alexa") -> None:
    """alerta/ayuda, QoS 1 y sin retención.

    El cuerpo tiene EXACTAMENTE los dos campos del contrato: `id` y `origen`
    (plan, línea 183). El origen por defecto es "alexa", que es lo que manda el
    ESP32 cuando el Echo Pop activa el dispositivo virtual Ayuda, y es lo mismo que
    genera el comando de consola.

    Nota que la escribe el usuario `nodo`, y no `orquestador`: el `acl.conf` le da
    a `nodo` escritura en `alerta/ayuda`, y a `orquestador` también. Los dos
    pueden, y por eso el orquestador tiene que sacarle el tipo al tópico y no
    buscarlo en el mensaje.
    """
    carga = {
        "id": siguiente_id_alerta(),
        "origen": origen,
    }
    cliente.publish(
        contrato.TOPICO_ALERTA_AYUDA,
        json.dumps(carga, separators=(",", ":")),
        qos=contrato.QOS_ALERTAS,
        retain=contrato.RETENIDO_NO,
    )
    log("alerta", "ayuda: " + json.dumps(carga, separators=(",", ":")))


# --- Reacciones ------------------------------------------------------------


def aplicar_luz_set(mensaje: bytes) -> None:
    """luz/set: el comando que si manda la casa.

    Estas reglas son COPIA de las del firmware (`src/contrato.cpp`,
    `manejar_luz_set`), y no una version aproximada. Es importante que coincidan
    exactamente: si el simulador aceptara un mensaje que el ESP32 rechaza, una
    prueba pasaria aqui y fallaria en el hardware, que es el peor resultado
    posible de un simulador.

    Las cuatro comprobaciones, en el mismo orden que el firmware:

    1. `estado` tiene que ser "on" u "off".
    2. `ts` tiene que estar, y ser un entero epoch.
    3. La antiguedad (`ahora - ts`) tiene que estar entre -30 y +10 segundos.
    4. `origen` NO se comprueba: si falta o no se conoce, se asume "panel" y se
       sigue. El firmware hace lo mismo, y es a proposito: el unico que publica
       luz/set es el servidor, y un origen raro no es motivo para apagar la luz.

    El punto 3 es la regla que cierra el agujero de seguridad. Sin ella, un mensaje
    de luz sin `ts` o con un `ts` viejo se acepta, y cualquier cosa que encuentre
    el topic puede encender la luz de una persona mayor a las tres de la manana, sin
    forma de saber que el mensaje es viejo. Con ella, una orden acumulada durante
    un corte llega cinco minutos despues y se descarta sola, que es justo lo que
    tiene que pasar.
    """
    global luz_encendida
    try:
        carga = json.loads(mensaje.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        log("luz/set", "descartado: JSON ilegible")
        return
    if not isinstance(carga, dict):
        log("luz/set", "descartado: no es un objeto JSON")
        return

    estado = carga.get("estado")
    if estado not in ("on", "off"):
        log("luz/set", "descartado: estado %r desconocido" % (estado,))
        return

    # El firmware solo aplica la regla de antiguedad si tiene hora valida, y sin
    # hora descarta la orden. El simulador siempre tiene hora (es el reloj del
    # sistema), asi que no hace falta esa comprobacion aqui. Si este script se
    # usara en un equipo sin reloj, esto habria que revisar.
    marca = carga.get("ts")
    if marca is None:
        log("luz/set", "descartado: sin ts (no se puede verificar la antiguedad)")
        return
    if not isinstance(marca, int) or isinstance(marca, bool):
        log("luz/set", "descartado: ts no numerico")
        return

    antiguedad = int(ahora()) - marca
    if antiguedad > ORDEN_MAX_ANTIGUEDAD_S:
        log("luz/set", "descartado: ts de hace %d s (max %d)", antiguedad,
            ORDEN_MAX_ANTIGUEDAD_S)
        return
    if antiguedad < -30:
        log("luz/set", "descartado: ts %d s en el futuro", -antiguedad)
        return

    origen = carga.get("origen")
    if origen not in ("voz", "alexa", "panel", "boton"):
        if origen is None:
            log("luz/set", "sin origen, se asume panel")
        else:
            log("luz/set", "origen %r desconocido, se asume panel" % (origen,))
        origen = "panel"

    nuevo = estado == "on"
    if nuevo == luz_encendida:
        log("luz/set", "el estado ya era %s, no se publica nada" % estado)
        return
    luz_encendida = nuevo
    publicar_luz_estado(origen)


def aplicar_nodo_config(mensaje: bytes) -> None:
    """nodo/config: los umbrales del gas, y ahora sí los aplica.

    Es COPIA de `manejar_nodo_config` de `firmware/src/contrato.cpp`, con una
    diferencia que hay que tener presente: el ESP32 guarda el umbral de apertura
    en memoria no volátil (para seguir con el último valor aunque el servidor no
    vuelva nunca) y el simulador lo guarda en una variable, porque el proceso se
    reinicia sin perder nada importante. El efecto es el mismo durante una sesión.

    La versión anterior de este archivo receiptaba el tópico y lo registraba, pero
    NO cambiaba su comportamiento, con el argumento de que si lo aplicara una
    prueba de configuración pasaría aquí y fallaría en el hardware. El argumento
    estaba al revés: el firmware SÍ lo aplica, así que no aplicarlo hacía que
    cualquier prueba de umbrales fuera falsa en el simulador y verdadera en la
    placa, que es exactamente el resultado que hay que evitar.

    `gas_off_adc` es el umbral de CIERRE. Los dos vienen del mismo mensaje y los
    dos se aplican.
    """
    global gas_on_adc, gas_off_adc

    try:
        carga = json.loads(mensaje.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        log("nodo/config", "descartado: no es JSON legible")
        return
    if not isinstance(carga, dict):
        log("nodo/config", "descartado: no es un objeto JSON")
        return

    if "gas_on_adc" in carga:
        valor = carga["gas_on_adc"]
        if isinstance(valor, int) and 0 < valor <= contrato.GAS_ADC_MAXIMO:
            gas_on_adc = valor
            log("nodo/config", "umbral de apertura ahora %d", gas_on_adc)
        else:
            log("nodo/config", "gas_on_adc %r invalido; se conserva %d",
                valor, gas_on_adc)
    if "gas_off_adc" in carga:
        valor = carga["gas_off_adc"]
        if isinstance(valor, int) and 0 <= valor < gas_on_adc:
            gas_off_adc = valor
            log("nodo/config", "umbral de cierre ahora %d", gas_off_adc)
        else:
            log("nodo/config", "gas_off_adc %r invalido; se conserva %d",
                valor, gas_off_adc)

    publicar_nodo_estado()


def aplicar_prueba_gas(mensaje: bytes) -> None:
    """nodo/prueba_gas: forzar el valor de gas.

    Solo existe en los builds con PRUEBAS=1 del firmware. El simulador lo acepta
    siempre, porque para eso está: es la forma de provocar una alarma sin tener
    que humear el sensor, y es lo que usa el guion de demostración.

    Acepta los DOS formatos, y hay una razón para eso:

    - `{"gas_adc":3000}`, que es el ejemplo del contrato (plan, línea 189), el que
      usa el guion de demostración y el que ahora entiende tambien el firmware:
      `manejar_prueba_gas` lo lee con ArduinoJson desde que se corrigio.
    - `3000`, en texto plano, que sigue aceptando el firmware con un aviso en el
      log. Es el formato antiguo.

    Admitir los dos es lo que permite usar el MISMO comando en la demostración con
    el simulador y con la placa, en vez de tener que recordar cuál de los dos formatos
    espera cada uno.
    """
    global gas_forzado
    texto = mensaje.decode("utf-8", errors="replace").strip()

    try:
        carga = json.loads(texto)
    except ValueError:
        carga = None

    if isinstance(carga, dict):
        valor = carga.get("gas_adc")
        if valor is None:
            log("prueba_gas", "descartado: no trae gas_adc")
            return
    elif isinstance(carga, (int, float)):
        valor = carga
    else:
        # Texto plano: el formato del firmware.
        try:
            valor = int(texto)
        except ValueError:
            log("prueba_gas", "descartado: %r no es un numero ni un JSON con "
                "gas_adc", texto[:40])
            return

    if not isinstance(valor, int) or isinstance(valor, bool):
        log("prueba_gas", "descartado: gas_adc %r no es un entero" % (valor,))
        return
    if valor < 0 or valor > contrato.GAS_ADC_MAXIMO:
        log("prueba_gas", "descartado: gas_adc %d fuera de 0..%d",
            valor, contrato.GAS_ADC_MAXIMO)
        return

    gas_forzado = valor
    log("prueba_gas", "gas forzado a %d; el muestreo de 1 Hz lo ve en la siguiente "
        "lectura" % valor)


def sensores_leer_gas() -> int:
    """La lectura de gas de este segundo, como la ve `sensores_leer()` del firmware."""
    if gas_forzado is not None:
        return gas_forzado
    return contrato.GAS_NORMAL


def gas_tarea() -> None:
    """El muestreo de 1 Hz y la maquina de estados del episodio.

    COPIA de `gas_tarea()` de `firmware/src/gas.cpp`, con los mismos nombres para
    las mismas cosas:

    - Se muestrea a 1 Hz. Si se muestreara en cada vuelta del bucle (que aquí es de
      0.5 s y en el ESP32 de 50 ms), las tres lecturas se contarían casi seguidas y
      un solo pico abriría un episodio.

    - El episodio abre con `GAS_LECTURAS_PARA_ABRIR` lecturas seguidas iguales o
      mayores que `gas_on_adc`, y genera UNA alerta. Un pico de una sola lectura no
      abre nada.

    - Cierra cuando la lectura lleva `GAS_CIERRE_SOSTENIDO_S` seguidos por debajo de
      `gas_off_adc`. Si vuelve a subir antes, se cancela el cierre y NO hay alerta
      nueva, porque es el mismo episodio.

    El buzzer no se simula: no hay pin. Lo que hace el firmware durante el episodio
    es el sonido local, y eso no se puede demostrar sin la placa.
    """
    global gas_adc, episodio_gas_abierto, lecturas_sobre_umbral
    global inicio_bajo_umbral, proxima_lectura, gas_forzado, oscilando

    momento = ahora()
    if proxima_lectura != 0.0 and (momento - proxima_lectura) < 0:
        return
    proxima_lectura = momento + contrato.PERIODO_LECTURA_GAS_S

    if oscilando:
        # Alterna por encima y por debajo del umbral de apertura, cada segundo. Con
        # la regla de tres lecturas seguidas, subir y bajar sin parar tres veces
        # seguidas NO abre episodio, que es justo lo que la P09 comprueba.
        gas_forzado = (gas_on_adc + 60) if gas_forzado is None or gas_forzado < gas_on_adc \
            else (gas_on_adc - 60)

    gas_adc = sensores_leer_gas()

    if not episodio_gas_abierto:
        if gas_adc >= gas_on_adc:
            lecturas_sobre_umbral += 1
            log("gas", "lectura %d sobre el umbral %d (%d de %d)"
                % (gas_adc, gas_on_adc, lecturas_sobre_umbral,
                   contrato.GAS_LECTURAS_PARA_ABRIR))
            if lecturas_sobre_umbral >= contrato.GAS_LECTURAS_PARA_ABRIR:
                lecturas_sobre_umbral = 0
                abrir_episodio_gas(gas_adc)
                return
        else:
            lecturas_sobre_umbral = 0
        return

    # Episodio abierto: se mira el cierre.
    if gas_adc < gas_off_adc:
        if inicio_bajo_umbral == 0.0:
            inicio_bajo_umbral = momento
        elif (momento - inicio_bajo_umbral) >= contrato.GAS_CIERRE_SOSTENIDO_S:
            cerrar_episodio_gas(gas_adc)
    else:
        # Vuelve a subir: se cancela el cierre en curso, sin alerta nueva.
        inicio_bajo_umbral = 0.0


def abrir_episodio_gas(valor: int) -> None:
    global episodio_gas_abierto, inicio_bajo_umbral
    if not episodio_gas_abierto:
        episodio_gas_abierto = True
        inicio_bajo_umbral = 0.0
        log("gas", "EPISODIO ABIERTO con gas_adc %d (umbral %d)", valor, gas_on_adc)
        publicar_alerta_gas(valor)
        publicar_nodo_estado()


def cerrar_episodio_gas(valor: int = None) -> None:
    global episodio_gas_abierto, inicio_bajo_umbral
    if episodio_gas_abierto:
        episodio_gas_abierto = False
        inicio_bajo_umbral = 0.0
        log("gas", "EPISODIO CERRADO, gas=%s", valor if valor is not None else gas_adc)
        publicar_nodo_estado()


def al_conectar(cliente, usuario, banderas, codigo, propiedades=None) -> None:
    """Todo lo que hay que hacer al abrir sesion.

    Se suscribe a TRES topicos, y solo a tres:

      casa/sala/luz/set          QoS 1   la orden de la luz
      casa/sala/nodo/config      QoS 1   los umbrales del gas
      casa/sala/nodo/prueba_gas  QoS 1   el comando de prueba

    La version anterior se suscribia tambien a `casa/sala/luz/estado`, y no puede:
    el `acl.conf` no le da lectura de `casa/sala/luz/estado` al usuario `nodo`, que
    es el usuario de este script. Con MQTT 3.1.1 un rechazo por ACL no da error a
    quien se suscribe: el broker responde con un SUBACK de fallo y paho lo ignora,
    asi que el log decia "suscrito a cuatro topicos" y de `luz/estado` no llegaba
    nunca nada. Peor: el nodo es quien ESCRIBE en ese topico, así que solo
    escucharse a sí mismo no sirve de nada.

    `prueba_gas` va con QoS 1 y no con QoS 0 como estaba. El comando es una orden
    del servidor (la laptop, con `mosquitto_pub`), y el contrato lo declara con QoS
    1 (plan, linea 222). Con QoS 0 no se confirma la entrega, y un comando de
    prueba perdido no se nota hasta que no suena la alarma: en una demostración,
    eso es un silencio de treinta segundos sin explicación.

    Y se publican los dos retenidos. Sin esto, un suscriptor nuevo no vería
    `luz/estado` ni `nodo/estado` hasta que hubiera un cambio, y no hay forma de
    saber si la luz está apagada porque está apagada o porque nadie lo ha dicho.
    """
    # En paho 2.x, la firma de `on_connect` es
    # (cliente, userdata, flags, reason_code, properties). El segundo argumento es
    # el `userdata` de la llamada, que aqui no se usa y va a None, y el tercero son
    # las banderas, no el nombre de usuario. La version anterior imprimia el segundo
    # argumento con la etiqueta "conectado como", y por eso el arranque decia
    # "conectado como None": no era un fallo de conexion, era una etiqueta mal
    # puesta. El nombre de usuario y el client id salen del propio cliente.
    if codigo.is_failure:
        log("mqtt", "el broker rechazo la conexion (rc=%s)" % codigo)
        escribir_estado(conectado=False)
        return

    log("mqtt", "conectado a %s:%d como %s con client id %s (rc=%s)",
        os.environ.get("MQTT_HOST", "localhost"),
        int(os.environ.get("MQTT_PUERTO", "1883")),
        contrato.USUARIO_MQTT, contrato.CLIENT_ID_SIMULADO, codigo)

    escribir_estado(conectado=True)

    for topico, qos in (
        (contrato.TOPICO_LUZ_SET, contrato.QOS_ORDENES),
        (contrato.TOPICO_NODO_CONFIG, contrato.QOS_ORDENES),
        (contrato.TOPICO_PRUEBA_GAS, contrato.QOS_PRUEBA_GAS),
    ):
        resultado, _ = cliente.subscribe(topico, qos=qos)
        if resultado != mqtt.MQTT_ERR_SUCCESS:
            log("mqtt", "NO se pudo suscribir a %s (error %d). El acl.conf no le "
                "da lectura de ese topico al usuario %s"
                % (topico, resultado, contrato.USUARIO_MQTT))
        else:
            log("mqtt", "suscrito a %s con QoS %d" % (topico, qos))

    log("mqtt", "umbrales de gas: abre con %d, cierra con %d",
        gas_on_adc, gas_off_adc)
    publicar_luz_estado("panel")
    publicar_nodo_estado()
    publicar_sensores(forzar=True)


def al_recibir(cliente, usuario, mensaje) -> None:
    """Reparte el mensaje al manejador que le toca.

    Cada manejador es responsable de validar su propio JSON. Aquí solo se
    descarta lo que no es texto UTF-8, porque eso no lo puede arreglar nadie más.
    """
    topico = mensaje.topic
    try:
        cuerpo = mensaje.payload.decode("utf-8")
    except UnicodeDecodeError:
        log("mqtt", "descartado de %s: no es UTF-8" % topico)
        return
    log("mqtt", "recibido en %s: %s" % (topico, cuerpo))
    if topico == contrato.TOPICO_LUZ_SET:
        aplicar_luz_set(mensaje.payload)
    elif topico == contrato.TOPICO_NODO_CONFIG:
        aplicar_nodo_config(mensaje.payload)
    elif topico == contrato.TOPICO_PRUEBA_GAS:
        aplicar_prueba_gas(mensaje.payload)


def al_desconectar(cliente, usuario, banderas, codigo, propiedades=None) -> None:
    """Se apaga el Last Will: el `conectado` en nodo/estado va a `false`.

    Solo si la desconexión fue inesperada. Si el proceso está saliendo por
    `SIGTERM` o `SIGINT`, la sesion se cierra limpia y el broker no necesita el
    will: publicarlo a mano ahí sería mentir, porque el proceso sigue vivo.
    """
    if salir:
        log("mqtt", "sesion cerrada limpiamente; no se publica el will")
        return
    log("mqtt", "desconectado (rc=%s), se publica conectado=false" % codigo)
    escribir_estado(conectado=False)
    cliente.publish(
        contrato.TOPICO_NODO_ESTADO,
        json.dumps({"conectado": False}, separators=(",", ":")),
        qos=contrato.QOS_ESTADO,
        retain=contrato.RETENIDO_SI,
    )


def pedir_salida(numero, marco) -> None:
    global salir
    log("simulador", "señal %d recibida, cerrando" % numero)
    salir = True


# --- Arranque --------------------------------------------------------------


def main() -> int:
    global cliente, salir

    try:
        import paho.mqtt
        version_mqtt = paho.mqtt.__version__
    except Exception:
        version_mqtt = "?"

    password = os.environ.get("MQTT_NODO_PASSWORD")
    if not password:
        print("FALTA MQTT_NODO_PASSWORD en el entorno.", file=sys.stderr)
        print("Cárgala con:  set -a; . ../servidor/.env; set +a", file=sys.stderr)
        return 2

    cliente = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=contrato.CLIENT_ID_SIMULADO,
        protocol=mqtt.MQTTv311,
        clean_session=False,
    )
    # El will se declara UNA vez, al crear el cliente. Se publica si el proceso
    # desaparece sin cerrar sesion: se apaga la corriente, se mata el proceso con
    # SIGKILL, o se cae la red.
    cliente.will_set(
        contrato.TOPICO_NODO_ESTADO,
        json.dumps({"conectado": False}, separators=(",", ":")),
        qos=contrato.QOS_ESTADO,
        retain=contrato.RETENIDO_SI,
    )
    cliente.username_pw_set(contrato.USUARIO_MQTT, password)
    cliente.on_connect = al_conectar
    cliente.on_message = al_recibir
    cliente.on_disconnect = al_desconectar

    signal.signal(signal.SIGINT, pedir_salida)
    signal.signal(signal.SIGTERM, pedir_salida)

    direccion = os.environ.get("MQTT_HOST", "localhost")
    puerto = int(os.environ.get("MQTT_PUERTO", "1883"))

    estado_cargar()

    log("simulador", "arrancando con paho-mqtt %s" % version_mqtt)
    log("simulador", "client id %s, usuario %s" % (contrato.CLIENT_ID_SIMULADO,
                                                    contrato.USUARIO_MQTT))
    log("simulador", "arranque %d, alertas publicadas desde el inicio: %d"
        % (arranque_actual, contador_alertas))
    log("simulador", "pseudo-MAC %s para los ids de alerta" % contrato.MAC_SIMULADO)
    log("simulador", "conectando a %s:%d" % (direccion, puerto))

    cliente.connect(direccion, puerto, keepalive=60)
    cliente.loop_start()

    try:
        # El bucle NO llama a cliente.loop(): `loop_start()` ya corre un hilo
        # propio que se encarga de eso. Llamar a `loop()` desde aqui ademas
        # haria que dos hilos compitan por la misma cola de paquetes, y el
        # resultado es que se pierden callbacks, entre ellos `on_connect`.
        #
        # Lo que hace este bucle es lo suyo: el muestreo del gas a 1 Hz, el
        # latido de sensores, leer los comandos de consola y mirar si toca salir.
        #
        # La version anterior NO muestreaba el gas en el bucle: la alarma se
        # abria en el hilo de paho, en cuanto llegaba `prueba_gas`, y con la
        # primera lectura. Eso rompia la regla de las tres lecturas seguidas, que es
        # la que evita que un pico de ruido abra un episodio, y hacia que el gas
        # normal del propio simulador (312, muy por debajo del umbral) no tuviera
        # ningun papel en el cierre.
        #
        # `sys.stdin` se lee sin bloquear con `select`, porque un `input()` en este
        # bucle impediria muestrear el gas mientras nadie escribe nada, que es
        # justamente cuando tiene que pasar.
        entrada = sys.stdin
        if not entrada.isatty():
            # Sin terminal (por ejemplo, `docker run` sin -t): no hay comandos de
            # consola, y se avisa para que nadie se quede esperando una respuesta.
            log("simulador", "sin terminal: los comandos de consola no estan "
                             "disponibles. El gas se controla por nodo/prueba_gas.")

        while not salir:
            gas_tarea()
            publicar_sensores()
            _leer_comandos(entrada)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        log("simulador", "desconectando")
        cliente.disconnect()
        cliente.loop_stop()
        escribir_estado(conectado=False)
        log("simulador", "terminado")
    return 0


def _leer_comandos(entrada) -> None:
    """Lee una linea de la consola, si hay, y la ejecuta.

    `select` con timeout 0 en vez de `input()`: el bucle principal no puede quedarse
    esperando a que alguien escriba, porque mientras espera no llega el muestreo del
    gas y una alarma abierta no se cierra nunca.
    """
    try:
        lista, _, _ = select.select([entrada], [], [], 0)
    except (OSError, ValueError):
        return
    if not lista:
        return
    linea = entrada.readline()
    if not linea:
        # EOF: la entrada estandar se cerro (por ejemplo, `docker run` sin `-i`). Se
        # deja de leer, pero el simulador sigue publicando, que es lo importante.
        return
    ejecutar_comando(linea.strip())


def ejecutar_comando(linea: str) -> None:
    """Ejecuta un comando de consola. Cada uno responde con una linea."""
    global gas_forzado, oscilando, presencia, temperatura, humedad, luz_encendida

    if not linea:
        return
    partes = linea.split()
    comando = partes[0].lower()
    argumentos = partes[1:]

    if comando in ("salir", "exit", "quit"):
        global salir
        salir = True
        log("consola", "saliendo")
        return

    if comando == "estado":
        log("consola",
            "umbrales: abre %d, cierra %d | gas %d | luz %s | episodio %s | "
            "lecturas sobre umbral %d | alertas publicadas %d | gas forzado %s",
            gas_on_adc, gas_off_adc, gas_adc,
            "encendida" if luz_encendida else "apagada",
            "abierto" if episodio_gas_abierto else "cerrado",
            lecturas_sobre_umbral, contador_alertas,
            gas_forzado if gas_forzado is not None else "ninguno")
        return

    if comando == "gas":
        if not argumentos:
            log("consola", "uso: gas <valor> | gas normal | gas oscilar")
            return
        if argumentos[0] == "normal":
            oscilando = False
            gas_forzado = contrato.GAS_NORMAL
            log("consola", "gas forzado a %d (aire limpio)" % contrato.GAS_NORMAL)
            publicar_sensores(forzar=True)
            return
        if argumentos[0] == "oscilar":
            # Para la P09: oscila alrededor del umbral de apertura, y solo tiene
            # que abrir UN episodio en un minuto.
            oscilando = True
            gas_forzado = gas_on_adc - 60
            log("consola", "oscilando alrededor de %d; el episodio solo abre con "
                "tres lecturas seguidas sobre el umbral" % gas_on_adc)
            return
        try:
            oscilando = False
            gas_forzado = int(argumentos[0])
        except ValueError:
            log("consola", "gas %r no es un numero" % argumentos[0])
            return
        log("consola", "gas forzado a %d; el muestreo de 1 Hz lo ve en la "
            "siguiente lectura" % gas_forzado)
        return

    if comando == "ayuda":
        # El origen por defecto es "alexa", que es lo que el ESP32 manda cuando el
        # Echo Pop activa el dispositivo Ayuda. El cuerpo es el del contrato:
        # {"id":"...","origen":"alexa"}.
        publicar_alerta_ayuda(argumentos[0] if argumentos else "alexa")
        log("consola", "alerta de ayuda publicada (el id es nuevo cada vez)")
        return

    if comando == "boton":
        luz_encendida = not luz_encendida
        publicar_luz_estado("boton")
        return

    if comando == "luz":
        if not argumentos or argumentos[0] not in ("on", "off"):
            log("consola", "uso: luz on | luz off")
            return
        nuevo = argumentos[0] == "on"
        if nuevo == luz_encendida:
            log("consola", "la luz ya estaba %s" % argumentos[0])
            return
        luz_encendida = nuevo
        publicar_luz_estado("panel")
        return

    if comando in ("temp", "temp_c"):
        if not argumentos:
            log("consola", "uso: temp <valor>")
            return
        try:
            temperatura = float(argumentos[0])
        except ValueError:
            log("consola", "temp %r no es un numero" % argumentos[0])
            return
        publicar_sensores(forzar=True)
        return

    if comando in ("hum", "hum_pct"):
        if not argumentos:
            log("consola", "uso: hum <valor>")
            return
        try:
            humedad = float(argumentos[0])
        except ValueError:
            log("consola", "hum %r no es un numero" % argumentos[0])
            return
        publicar_sensores(forzar=True)
        return

    if comando == "presencia":
        if not argumentos or argumentos[0] not in ("on", "off"):
            log("consola", "uso: presencia on | presencia off")
            return
        nuevo = argumentos[0] == "on"
        if nuevo != presencia:
            presencia = nuevo
            log("consola", "presencia %s" % argumentos[0])
        publicar_sensores(forzar=True)
        return

    log("consola", "comando %r desconocido. Comandos: ayuda, gas <valor>, gas "
        "normal, gas oscilar, boton, luz on, luz off, presencia on, presencia off, "
        "temp <valor>, hum <valor>, estado, salir" % comando)


cliente = None

if __name__ == "__main__":
    sys.exit(main())
