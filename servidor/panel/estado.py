"""El estado de la sala, en memoria, y el cliente MQTT que lo mantiene al día.

El panel no guarda el estado de la casa: no hay tabla para eso, y no debe haberla.
Cada lectura que llega de MQTT se queda en un diccionario en memoria del proceso, con
la hora en que llegó, y de ahí sale la instantánea que ve el panel. Si el panel se
reinicia, vuelve a leer los retenidos al suscribirse y tiene el estado entero otra
vez: no hay ningún estado que pueda quedarse viejo en el disco.

## Por qué en memoria

Porque el estado de la casa ya está retenido en el broker. `luz/estado`, `nodo/estado`
y `satelite/<client_id>/estado` son retenidos que el broker entrega a quien se suscriba, así que
la copia del panel es una caché con una sola fuente de verdad detrás. Guardarla en
SQLite añadiría una tabla que hay que migrar, purgar y poner de acuerdo con MQTT, y
que se pondría vieja si el panel no arrancara.

`casa/sala/sensores` NO está retenido a propósito (el ESP32 lo publica cada 60 s sin
retención). Por eso el estado de los sensores lleva `recibido_en` y la interfaz dice
«sin datos» con su antigüedad en vez de dibujar un número viejo disfrazado de actual.

## El hilo y el bucle

Los callbacks de paho llegan en SU hilo, que no es el bucle de asyncio de FastAPI. Y
el `try` alrededor de la llamada a `on_message` no es opcional: si esa función lanza,
la excepción sube por el hilo de red de paho y lo mata, el proceso sigue vivo, `docker
ps` dice «running» y el panel ha dejado de recibir mensajes para siempre sin decir
nada.

Del evento se encarga el bucle con `call_soon_threadsafe`, que es el puente correcto
entre un hilo y un bucle. Cada conexión WebSocket tiene su cola acotada
(`config.COLA_EVENTOS_WS`): si un cliente no consume y se llena, se le marca para
cerrar con el 1013 («recargando») y al reconectar recibe la instantánea completa, que
es el motivo de que el cliente trate el 1013 como una reconexión y no como un error.

## Los cuatro estados, y por qué son cuatro

`estado` no es un booleano, y esta es la parte del diseño que más evita sustos:

- **`conectado`**: hay lectura reciente.
- **`desconectado`**: hubo lectura y pasó el plazo (`PANEL_SEGUNDOS_VISTO_S`, 90 s).
  Alguien se cayó y se nota.
- **`no_instalado`**: el componente no existe en esta instalación y no hay nadie a quien
  esperar. No es un fallo, y por eso no lleva cartel rojo: el satélite todavía no
  existe, y mostrarlo en rojo sería un aviso permanente que nadie puede arreglar.
- **`sin_datos`**: se espera una lectura pero todavía no ha llegado ninguna.

## El modo decide de dónde sale el estado del sitio

Hoy se trabaja en `MODO_SITIO=instalacion`, sin puente: el ESP32 está en la misma red
que el servidor y publica directo al broker. Si el panel mirase
`$SYS/broker/connection/puente-sitio/state` sin más, mostraría «sitio sin conexión»
PARA SIEMPRE con la casa funcionando: un cartel de alarma que nunca se apaga es la
forma más rápida de que nadie mire los otros.

- `instalacion`: el sitio es el nodo, y su estado sale de `nodo/estado`
  (`sitio.fuente` = `"nodo"`). El puente no se mira.
- `demostracion`: el sitio es la conexión del sitio, y su estado sale del retained del
  puente (`sitio.fuente` = `"puente"`). El nodo se sigue mirando aparte, porque en
  demostración el nodo está detrás del puente y puede caerse sin que se caiga el
  puente.

Cambiar de modo es una línea en `.env` y reiniciar el contenedor. No hay código que
tocarlo.
"""

import asyncio
import json
import logging
import re
import threading
import time

import paho.mqtt.client as mqtt

import config

registro = logging.getLogger("panel-estado")

# Los cuatro textos de estado. Salen tal cual a la interfaz, así que viven aquí y no
# escritos en las comparaciones.
CONECTADO = "conectado"
DESCONECTADO = "desconectado"
NO_INSTALADO = "no_instalado"
SIN_DATOS = "sin_datos"


# Lo que se mete en la cola para despertarla cuando ya no va a poder con los eventos.
# `None` a propósito: no es un `dict` como los de `emitir`, así que no se puede
# confundir con un evento real ni llegar por error al `send_text`.
TESTIGO_SATURADO = None


class Oyente:
    """Un cliente WebSocket y su cola de eventos.

    Es una clase y no un `asyncio.Queue` suelto porque hace falta un sitio donde
    poner «este cliente no está consumiendo»: la cola llena se marca desde el bucle y
    el que escribe (el WebSocket) mira la marca después de cada evento.
    """

    __slots__ = ("cola", "saturado", "saturado_espera", "ultimo_pong")

    def __init__(self):
        self.cola = asyncio.Queue(maxsize=config.COLA_EVENTOS_WS)
        self.saturado = False
        # Un `asyncio.Event` aparte, y no la marca `saturado` a secas, porque quien
        # tiene que enterarse de la saturación no es el que escribe en la cola: es el
        # que puede estar bloqueado EN `send_text`, esperando a que el socket del
        # cliente drene. Ese no vuelve a mirar la marca nunca. Con un `Event`, la
        # marca es un aviso que llega a quien esté donde esté; con un `bool`, solo la
        # vería quien ya estaba mirando.
        self.saturado_espera = asyncio.Event()
        # Cuándo fue el último `pong` del cliente, en reloj monótono. Monótono y no
        # `time.time()` a propósito: si el reloj del sistema se mueve (NTP, un cambio
        # de zona), la diferencia de dos valores también se mueve, y el cierre por
        # «45 s sin pong» dispararía o no dispararía por un cambio de hora.
        self.ultimo_pong = time.monotonic()

    def poner(self, evento) -> bool:
        """Mete un evento. `False` si la cola estaba llena y había que cerrar.

        Ponerlo aquí y no en el emisor es a propósito: esta función solo se llama
        desde el bucle de asyncio (por `call_soon_threadsafe` o desde el propio
        manejador), y `Queue.put_nowait` no es seguro desde otro hilo.

        Al llenarse la cola hay que hacer DOS cosas: marcarla como saturada y
        **despertar a quien la lee**. Sin lo segundo, el cierre con 1013 no llega nunca
        en el caso que de verdad importa, que es el cliente que no consume: está
        bloqueado en `cola.get()`, la marca `saturado` se pone, y no vuelve a mirar
        hasta que llegue otro evento. Y no va a llegar ninguno, porque para que la cola
        se llenara han hecho falta `COLA_EVENTOS_WS` eventos y este es el siguiente.
        El cierre se quedaríaaplazado hasta el siguiente latido (45 s como mucho) o
        hasta que el socket se cayera, que es justo cuando ya no hacía falta cerrarlo.

        Para despertarla se saca el evento MÁS ANTIGUO y en su sitio va el testigo. El
        más antiguo, y no el nuevo, porque un evento que ya se descartó no vale nada y
        el testigo ocupa el hueco que se acaba de librar sin tener que crecer la cola.
        """
        try:
            self.cola.put_nowait(evento)
            return True
        except asyncio.QueueFull:
            self.saturado = True
            # El aviso va al `Event` y no solo a la marca, porque el cierre lo decide
            # quien esté bloqueado en `send_text`, que no vuelve a mirar la cola. Y el
            # testigo se queda, para el caso contrario (el bucle sí está en `get()`).
            # Cuesta un `put_nowait` de más y cubre los dos casos.
            self.saturado_espera.set()
            self.cola.get_nowait()
            self.cola.put_nowait(TESTIGO_SATURADO)
            return False


_TOPICO_SATELITE = re.compile(r"\Acasa/sala/satelite/([^/+#]+)/estado\Z")


def _client_id_satelite(topico: str):
    """`casa/sala/satelite/<client_id>/estado` -> client_id; si no, `None`."""
    coincide = _TOPICO_SATELITE.match(topico or "")
    return coincide.group(1) if coincide else None


class EstadoSala:
    """Lo último que ha llegado de cada tópico, más quién está escuchando."""

    def __init__(self, modo: str = None, plazo_visto_s: int = None,
                 reloj=time.time):
        self.modo = modo if modo is not None else config.MODO_SITIO
        self.plazo_visto_s = (plazo_visto_s if plazo_visto_s is not None
                              else config.SEGUNDOS_VISTO_S)
        self.reloj = reloj
        self.bucle = None
        self._candado = threading.Lock()
        self._datos = {}      # componente -> {"carga":..., "recibido_en":..., "topico":...}
        self._oyentes = []    # de Oyente

    # --- Entrada ------------------------------------------------------------

    def recibir(self, topico: str, cuerpo: str, retain: bool = False) -> None:
        """Llega un mensaje: se guarda y se avisa a quien esté escuchando.

        La carga se guarda como viene. Si es JSON, se interpreta; si no, se guarda el
        texto, porque el enlace del sitio lo escribe el puente como `1` o `0` sin
        JSON. Lo que no se hace es tirar el mensaje por no entenderlo: se guarda igual
        y quien lo mira decide.
        """
        componente = self.componente_de(topico)
        if componente is None:
            return
        ahora = int(self.reloj())
        recibido_en = None if retain else ahora
        entrada = {"carga": _cargar(cuerpo), "recibido_en": recibido_en, "topico": topico}
        with self._candado:
            if componente == "satelite":
                # Uno por satelite (Fase 2C), con su client id. Un retenido vacio
                # es un satelite que se dio de baja: se olvida.
                satelites = dict(self._datos.get("satelites", {}))
                client_id = _client_id_satelite(topico)
                if cuerpo == "":
                    satelites.pop(client_id, None)
                else:
                    satelites[client_id] = entrada
                self._datos["satelites"] = satelites
            else:
                self._datos[componente] = entrada
            datos = dict(self._datos)
        for evento in self._eventos_de(componente, datos, ahora):
            self.emitir(evento)

    @staticmethod
    def componente_de(topico: str):
        """El componente al que pertenece un tópico, o `None` si no es nuestro.

        Los cuatro primeros son los del contrato; el del enlace del sitio lleva un
        `+` en vez del nombre del puente, y por eso es el único que se reconoce por
        forma y no por igualdad.
        """
        if topico == config.TOPICO_LUZ_ESTADO:
            return "luz"
        if topico == config.TOPICO_SENSORES:
            return "sensores"
        if topico == config.TOPICO_NODO_ESTADO:
            return "nodo"
        if _client_id_satelite(topico) is not None:
            return "satelite"
        return "puente" if _es_enlace_sitio(topico) else None

    # --- La instantánea -----------------------------------------------------

    def instantanea(self, base) -> dict:
        """Todo el estado, con la forma de la propuesta, apartado 4.2.

        `alertas` y `familiares` salen de SQLite y no de MQTT: el panel no lee
        `casa/sala/alerta/#` (no tiene permiso, y no lo necesita) y las alertas están
        en la misma base que usa el bot, con el mismo estado derivado.
        """
        ahora = int(self.reloj())
        with self._candado:
            datos = dict(self._datos)
            oyentes = len(self._oyentes)
        return {
            "servidor_hora": ahora,
            "luz": self.bloque_luz(datos, ahora),
            "sensores": self.bloque_sensores(datos, ahora),
            "nodo": self.bloque_nodo(datos, ahora),
            "satelite": self.bloque_satelite(datos, ahora),
            "satelites": self.bloque_satelite(datos, ahora)["satelites"],
            "sitio": self.bloque_sitio(datos, ahora),
            "inactividad": self.bloque_inactividad(base),
            "modo": self.modo,
            "alertas": _conteo(base.contar_por_estado()),
            "familiares": base.contar_familiares_activos(),
            "conexiones": oyentes,
            # El umbral del gas en cuenta del ADC. No viene de MQTT: lo decide
            # `config.py` (el mismo archivo que usa el orquestador) y el ESP32 lo
            # recibe por `nodo/config`. Va aquí para que la barra de la tarjeta
            # pueda decir «Lectura: 312 / Umbral: 1800» sin inventarlo.
            "gas_on_adc": config.GAS_ON_ADC,
            "gas_off_adc": config.GAS_OFF_ADC,
        }

    def bloque_luz(self, datos, ahora) -> dict:
        entrada = datos.get("luz")
        if entrada is None:
            return {"encendida": None, "origen": None, "recibido_en": None,
                    "estado": SIN_DATOS}
        carga = entrada["carga"] if isinstance(entrada["carga"], dict) else {}
        estado_recibido = carga.get("estado")
        if entrada.get("recibido_en") is None:
            return {
                "encendida": (True if estado_recibido == "on"
                              else False if estado_recibido == "off" else None),
                "origen": carga.get("origen"),
                "recibido_en": None,
                "estado": SIN_DATOS,
            }
        # El retenido no lleva hora (el ESP32 publica `{"estado":..,"origen":..}`), así
        # que la que se mira es la de la recepción. Si el ESP32 lleva más de 90 s sin
        # mandar nada, lo que se sabe de la luz deja de ser actual, y el panel lo dice
        # en vez de pintar un interruptor que puede no reflejar el relé.
        return {
            "encendida": (True if estado_recibido == "on"
                          else False if estado_recibido == "off" else None),
            "origen": carga.get("origen"),
            "recibido_en": entrada["recibido_en"],
            "estado": self._estado_por_antiguedad(entrada, ahora),
        }

    def bloque_sensores(self, datos, ahora) -> dict:
        entrada = datos.get("sensores")
        if entrada is None:
            return {"temperatura_c": None, "humedad_pct": None, "movimiento": None,
                    "gas_adc": None, "recibido_en": None, "estado": SIN_DATOS}
        carga = entrada["carga"] if isinstance(entrada["carga"], dict) else {}
        if entrada.get("recibido_en") is None:
            return {
                "temperatura_c": carga.get("temp_c"),
                "humedad_pct": carga.get("hum_pct"),
                "movimiento": carga.get("presencia"),
                "gas_adc": carga.get("gas_adc"),
                "recibido_en": None,
                "estado": SIN_DATOS,
            }
        # Los nombres del contrato son `temp_c`, `hum_pct` y `presencia`. Aquí se
        # renombran a los que usa la interfaz, y el renombrado está en un solo sitio.
        return {
            "temperatura_c": carga.get("temp_c"),
            "humedad_pct": carga.get("hum_pct"),
            "movimiento": carga.get("presencia"),
            "gas_adc": carga.get("gas_adc"),
            "recibido_en": entrada["recibido_en"],
            "estado": self._estado_por_antiguedad(entrada, ahora),
        }

    def bloque_nodo(self, datos, ahora) -> dict:
        entrada = datos.get("nodo")
        if entrada is None:
            return {"estado": SIN_DATOS, "recibido_en": None, "ip": None, "fw": None}
        carga = entrada["carga"] if isinstance(entrada["carga"], dict) else {}
        if entrada.get("recibido_en") is None:
            if carga.get("conectado") is False:
                estado = DESCONECTADO
            else:
                estado = SIN_DATOS
            return {"estado": estado, "recibido_en": None,
                    "ip": carga.get("ip"), "fw": carga.get("fw")}
        if carga.get("conectado") is False:
            # El Last Will retenido es `{"conectado": false}` y eso es una caída YA
            # pasada: no hay que esperar los 90 s para contarla (P27).
            estado = DESCONECTADO
        else:
            estado = self._estado_por_antiguedad(entrada, ahora)
        return {"estado": estado, "recibido_en": entrada["recibido_en"],
                "ip": carga.get("ip"), "fw": carga.get("fw")}

    def bloque_satelite(self, datos, ahora) -> dict:
        """El estado agregado de los satélites, con la MISMA forma de siempre.

        La app lee `satelite` como un componente (`estado`, `recibido_en`), así que
        se conserva esa forma: `conectado` si al menos uno lo está. Al lado va
        `satelites`, `{client_id: bool}`, que la app de hoy ignora.

        La presencia sale de `conectado` (el estado al conectar y el Last Will), no
        de la antigüedad: el satélite publica su estado una sola vez al conectarse,
        así que con la regla de los 90 s salía «desconectado» a los 90 s de estar
        conectado. Si se cae, el broker publica su `{"conectado": false}`.
        """
        satelites = datos.get("satelites") or {}
        if not satelites:
            # Ningún satélite todavía. `no_instalado` y no `sin_datos`: no hay
            # nadie a quien esperar, así que no es un fallo y no lleva cartel.
            return {"estado": NO_INSTALADO, "recibido_en": None, "satelites": {}}
        por_satelite = {}
        recibidos = []
        for client_id, entrada in satelites.items():
            carga = entrada["carga"] if isinstance(entrada["carga"], dict) else {}
            por_satelite[client_id] = carga.get("conectado") is True
            r = entrada.get("recibido_en")
            if r is not None:
                recibidos.append(r)
        if not recibidos:
            return {"estado": CONECTADO if any(por_satelite.values()) else SIN_DATOS,
                    "recibido_en": None, "satelites": por_satelite}
        return {
            "estado": CONECTADO if any(por_satelite.values()) else DESCONECTADO,
            "recibido_en": max(recibidos),
            "satelites": por_satelite,
        }

    def bloque_inactividad(self, base) -> dict:
        """El reloj de la inactividad: cuándo fue el último movimiento y el ajuste.

        Sale de SQLite y no de MQTT, como las alertas: `ultimo_movimiento` lo
        escribe el orquestador en la tabla `configuracion`, que es la misma base que
        comparten los dos procesos. No hay tópico para esto, y tampoco haría falta
        uno: el reloj ya vive en la base, y un mensaje de más sería una segunda
        fuente de verdad que puede discrepar.

        `ultimo_movimiento` puede ser `None` (el orquestador todavía no ha visto
        ningún movimiento), y eso la interfaz lo dice con «sin datos», no con una
        hora inventada. El `tiempo_min` es el ajuste vigente, ya validado por `db`,
        y va con su rango para que el formulario del panel no repita los números.
        """
        return {
            "ultimo_movimiento": base.ultimo_movimiento(),
            "tiempo_min": base.tiempo_inactividad(),
            "minimo": config.TIEMPO_INACTIVIDAD_MIN_MINIMO,
            "maximo": config.TIEMPO_INACTIVIDAD_MIN_MAXIMO,
        }

    def bloque_sitio(self, datos, ahora) -> dict:
        """El estado del sitio, según `MODO_SITIO`. Propuesta 4.4."""
        if self.modo == "demostracion":
            entrada = datos.get("puente")
            if entrada is None:
                return {"estado": NO_INSTALADO, "recibido_en": None,
                        "fuente": "puente"}
            if not _verdad_de_texto(entrada["carga"]):
                estado = DESCONECTADO
            else:
                estado = self._estado_por_antiguedad(entrada, ahora)
            return {"estado": estado, "recibido_en": entrada["recibido_en"],
                    "fuente": "puente"}
        # Instalación: el sitio es el nodo. El puente ni se mira.
        bloque = self.bloque_nodo(datos, ahora)
        return {"estado": bloque["estado"], "recibido_en": bloque["recibido_en"],
                "fuente": "nodo"}

    def _estado_por_antiguedad(self, entrada, ahora) -> str:
        recibido = entrada.get("recibido_en") if entrada else None
        if recibido is None:
            return SIN_DATOS
        return CONECTADO if ahora - recibido <= self.plazo_visto_s \
            else DESCONECTADO

    # --- Eventos ------------------------------------------------------------

    def _eventos_de(self, componente, datos, ahora) -> list:
        """Los eventos que produce un cambio de este componente.

        En modo instalación, un cambio del nodo produce DOS eventos, el del nodo y el
        del sitio, porque el estado del sitio sale de ahí. Si se emitiera solo uno, la
        tarjeta del sitio se quedaría con lo viejo hasta la siguiente instantánea, que
        no llega hasta que alguien recargue.
        """
        if componente == "luz":
            return [{"tipo": "luz", "datos": self.bloque_luz(datos, ahora)}]
        if componente == "sensores":
            return [{"tipo": "sensores", "datos": self.bloque_sensores(datos, ahora)}]
        if componente == "nodo":
            eventos = [{"tipo": "nodo", "datos": self.bloque_nodo(datos, ahora)}]
            if self.modo == "instalacion":
                eventos.append({"tipo": "sitio",
                                "datos": self.bloque_sitio(datos, ahora)})
            return eventos
        if componente == "satelite":
            return [{"tipo": "satelite",
                     "datos": self.bloque_satelite(datos, ahora)}]
        if componente == "puente" and self.modo == "demostracion":
            return [{"tipo": "sitio", "datos": self.bloque_sitio(datos, ahora)}]
        return []

    def emitir(self, evento: dict) -> None:
        """Manda el evento a todos los que están escuchando.

        Si el hilo de MQTT no es el del bucle, se salta al bucle con
        `call_soon_threadsafe`. Lo que se ejecuta allí es `Oyente.poner`, que es lo
        único que toca las colas: meter algo en una `asyncio.Queue` desde otro hilo no
        es seguro, y el fallo sale tres días después, en forma de intermitencia.
        """
        with self._candado:
            oyentes = list(self._oyentes)
        if not oyentes or self.bucle is None:
            # Sin bucle todavía (arrancando) o ya parado (parando): el evento se
            # pierde y da igual, porque al reconectar llega la instantánea entera.
            return
        for oyente in oyentes:
            self.bucle.call_soon_threadsafe(oyente.poner, evento)

    # --- Los oyentes del WebSocket -----------------------------------------

    def scrupular(self, bucle) -> None:
        """Guarda el bucle de asyncio. Lo llama el arranque de la API."""
        self.bucle = bucle

    def oyente_nuevo(self) -> Oyente:
        oyente = Oyente()
        with self._candado:
            self._oyentes.append(oyente)
        return oyente

    def oyente_baja(self, oyente: Oyente) -> None:
        with self._candado:
            if oyente in self._oyentes:
                self._oyentes.remove(oyente)

    def conexiones(self) -> int:
        with self._candado:
            return len(self._oyentes)


class ClienteEstado:
    """El cliente MQTT del panel: usuario `panel`, sesión limpia, cinco tópicos.

    No reutiliza `mqtt_cliente.ClienteMQTT`, que es el del orquestador, y no por
    comodidad sino porque son clientes distintos de verdad: otro usuario, otra
    contraseña, `clean_session=True` en vez de `False`, y suscripciones que no tienen
    nada que ver. Reutilizarlo obligaría a que un parámetro de los dos dependiera del
    otro, y el día que se tocara uno se rompería el otro sin que ninguna prueba lo
    viera.

    El `client_id` es fijo (`panel-api`) por el mismo motivo que el del orquestador: si
    hubiera dos instancias, el broker expulsa a la vieja y queda una sola, en vez de
    acumular dos conexiones con el mismo estado en memoria.
    """

    TOPICOS = None  # se rellena abajo, cuando `config` ya está importado

    def __init__(self, estado: EstadoSala):
        self.estado = estado
        self.conectado = False
        self.cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.MQTT_CLIENT_ID_PANEL,
            protocol=mqtt.MQTTv311,
            clean_session=config.MQTT_SESION_LIMPIA_PANEL,
        )

        password = config.MQTT_PANEL_PASSWORD
        if not password:
            raise RuntimeError(
                "FALTA MQTT_PANEL_PASSWORD en el entorno. El panel no publica nada "
                "sin contraseña, y un panel sin conexión parece sano: mejor que muera "
                "al arrancar, que se nota en el primer `docker logs`."
            )
        self.cliente.username_pw_set(config.MQTT_USUARIO_PANEL, password)
        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_disconnect = self._al_desconectar
        self.cliente.on_message = self._al_mensaje

    def _al_conectar(self, cliente, usuario, banderas, codigo, propiedades=None):
        if codigo.is_failure:
            registro.error("el broker rechazo la conexion del panel (rc=%s). Comprueba "
                           "el bloque `user panel` del acl.conf y que el broker se "
                           "reinicio despues de tocarlo.", codigo)
            self.conectado = False
            return
        self.conectado = True
        registro.info("panel conectado a %s:%d como %s (client id %s, sesion limpia=%s)",
                      config.MQTT_HOST, config.MQTT_PUERTO, config.MQTT_USUARIO_PANEL,
                      config.MQTT_CLIENT_ID_PANEL,
                      "si" if config.MQTT_SESION_LIMPIA_PANEL else "no")
        self.suscribirse()

    def suscribirse(self) -> None:
        for topico in (config.TOPICO_LUZ_ESTADO, config.TOPICO_SENSORES,
                       config.TOPICO_NODO_ESTADO, config.TOPICO_SATELITE_ESTADO,
                       config.TOPICO_ENLACE_SITIO):
            # QoS 0 en todo: el estado es «lo último que se ha oído», y un mensaje
            # repetido no añade nada. Los retenidos llegan igual con QoS 0, porque
            # los publica el broker, no el cliente.
            resultado, _ = self.cliente.subscribe(topico, qos=0)
            if resultado != mqtt.MQTT_ERR_SUCCESS:
                registro.error("NO se pudo suscribir a %s (error %d). El bloque "
                               "`user panel` del acl.conf quiza no le da lectura.",
                               topico, resultado)
            else:
                registro.info("panel suscrito a %s", topico)

    def _al_desconectar(self, cliente, usuario, banderas, codigo, propiedades=None):
        self.conectado = False
        if not codigo.is_failure:
            registro.info("panel desconectado limpiamente")
        else:
            registro.warning("panel desconectado (rc=%s); reintentando", codigo)

    def _al_mensaje(self, cliente, usuario, mensaje):
        # Sin este `try`, una excepción en el procesado sube por el hilo de red de
        # paho y lo mata: el proceso sigue vivo y ya no recibe nunca más.
        try:
            cuerpo = mensaje.payload.decode("utf-8")
        except UnicodeDecodeError:
            registro.warning("descartado de %s: no es UTF-8", mensaje.topic)
            return
        registro.debug("mensaje del panel en %s: %s", mensaje.topic, cuerpo)
        try:
            self.estado.recibir(mensaje.topic, cuerpo, bool(getattr(mensaje, "retain", False)))
        except Exception:  # noqa: BLE001
            registro.exception("fallo al procesar %s; el resto de los mensajes se "
                               "sigue recibiendo", mensaje.topic)

    # --- Publicaciones ------------------------------------------------------

    def publicar(self, topico: str, carga: dict, qos: int = 1,
                 retain: bool = False) -> None:
        """Publica. Son las dos únicas escrituras del panel, y las dos son acciones.

        Escribir `luz/set` y `satelite/decir` es todo lo que el usuario `panel` puede
        hacer. No hay forma de que desde el panel salga una alerta: no tiene permiso en
        `casa/sala/alerta/#` y no hay código aquí que lo intente.
        """
        cuerpo = json.dumps(carga, separators=(",", ":"))
        resultado = self.cliente.publish(topico, cuerpo, qos=qos, retain=retain)
        resultado.wait_for_publish(timeout=5)
        registro.info("publicado por el panel en %s: %s", topico, cuerpo)

    def publicar_luz(self, estado: str, origen: str = "panel",
                 ts: int = None) -> None:
        """La orden de luz, con el `ts` que el ESP32 exige para descartar lo viejo.

        El `ts` va aquí y no lo pone el cliente: el ESP32 descarta por antigüedad lo que
        llegue viejo (P42), y una orden sin `ts` sería descartada siempre. Y el
        `origen` lo pone el que publica, no lo recibe del cliente: así es imposible que
        una orden de luz nazca con el origen de otra cosa.

        `ts` es un parámetro opcional y no algo que calcule siempre aquí, por un motivo
        concreto: quien llama necesita devolverle al móvil el mismo `ts` que ha ido por
        MQTT. Si cada uno lo calculara por su cuenta, serían dos llamadas a
        `time.time()` y bastaría con que el reloj.tickara entre medias para que el móvil
        viera un `ts` y el ESP32 recibiera otro. No rompería nada (P42 solo mira antigüedad
        y ambos valdrían), pero la respuesta sería untrue: diría que se publicó algo que
        no es exactamente lo que se publicó.

        Quien no pase `ts` (una prueba, un script) obtiene el de ahora, que es lo
        razonable.
        """
        self.publicar("casa/sala/luz/set",
                      {"estado": estado, "origen": origen,
                       "ts": int(time.time()) if ts is None else int(ts)},
                      qos=1)

    def conectar(self) -> None:
        registro.info("panel: conectando a %s:%d...", config.MQTT_HOST,
                      config.MQTT_PUERTO)
        self.cliente.connect(config.MQTT_HOST, config.MQTT_PUERTO,
                             keepalive=config.MQTT_KEEPALIVE)
        self.cliente.loop_start()

    def desconectar(self) -> None:
        self.cliente.disconnect()
        self.cliente.loop_stop()


def _cargar(cuerpo: str):
    """El JSON si lo es, y el texto tal cual si no.

    El enlace del sitio lo escribe el puente como `1` o `0`, sin JSON. Un estado
    guardado como texto no es un error: `_verdad_de_texto` lo sabe leer.
    """
    texto = (cuerpo or "").strip()
    if texto.startswith("{"):
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            return texto
    return texto


def _verdad_de_texto(carga) -> bool:
    """`1`, `true`, `connected`... todos son lo mismo: hay enlace.

    El MQTT no tiene un booleano, así que el valor llega como texto. Se cuenta como
    «no» solo lo que lo dice claramente, porque el que publica el `0` es el que sabe
    que se ha caído.
    """
    if isinstance(carga, bool):
        return carga
    if isinstance(carga, (int, float)):
        return carga != 0
    if isinstance(carga, dict):
        if "conectado" in carga:
            return bool(carga.get("conectado"))
        if "estado" in carga:
            return _verdad_de_texto(carga.get("estado"))
        return True
    return str(carga).strip().lower() not in ("", "0", "false", "off", "disconnected")


def _es_enlace_sitio(topico: str) -> bool:
    """Si el tópico es el del estado de conexión del puente con el servidor.

    Es `$SYS/broker/connection/+/state`, con el `+` siendo el `client_id` del puente
    (`puente-sitio`). Se reconoce por forma y no por igualdad, y por eso el nombre del
    puente no está escrito en el código: si el puente cambiara de client id, seguiría
    llegando en vez de dejar de publicarse en silencio.
    """
    partes = topico.split("/")
    return (len(partes) == 5 and partes[0] == "$SYS" and partes[1] == "broker"
            and partes[2] == "connection" and partes[4] == "state" and partes[3])


def _conteo(conteo: dict) -> dict:
    """Los cuatro estados de las alertas, en cero si no hay ninguna.

    En cero y no ausente: la interfaz pinta los cuatro números siempre, y si faltara
    alguno tendría que tratarlo como un caso aparte del que no existe.
    """
    return {
        "pendientes": conteo.get("pendiente", 0),
        "enviadas": conteo.get("enviada", 0),
        "resueltas": conteo.get("resuelta", 0),
        "descartadas": conteo.get("descartada", 0),
    }