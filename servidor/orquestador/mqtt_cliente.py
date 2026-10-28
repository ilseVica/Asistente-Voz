"""El cliente MQTT del orquestador.

Solo dos cosas: conectarse, y entregar cada mensaje a quien lo tenga que procesar
(`alertas.procesar` para las alertas, `voz.procesar` para el texto reconocido). La
decisión sobre qué hacer con el mensaje está en otro archivo, para que el
transporte no se mezcle con la lógica. El reparto por tópico lo hace
`orquestador.py` al construir el cliente.

Lo importante de este archivo es `clean_session=False`.

Con `clean_session=True` (el valor por defecto de paho), el broker olvida la
sesión cuando el cliente se desconecta: los mensajes QoS 1 que llegan durante el
corte se descartan, y las alertas de gas de ese rato no llegan nunca a Telegram.
Con `clean_session=False`, el broker guarda la sesión y encola los mensajes, y se
los entrega al reconectar. Eso es lo que hace que la prueba P26 ("alerta durante
un corte del orquestador") pase.

El precio de `clean_session=False` es que hay que tener `client_id` fijo, porque
si fuera aleatorio cada arranque dejaría una sesión huérfana en el broker durante
`persistent_client_expiration` (2 días en esta instalación), y esas sesiones
consumen memoria sin que nadie las limpie.

`reconnect_delay_set` es para el caso de que el broker no esté levantado: los
valores por defecto de paho (1 s) martillean la red cuando el broker está caído
del todo. Con estos, un broker caído no genera un intento por segundo.
"""

import json
import logging
import ssl
import threading

import paho.mqtt.client as mqtt

import config

registro = logging.getLogger("mqtt")


class ClienteMQTT:
    def __init__(self, al_recibir, al_reconectar=None):
        self.al_recibir = al_recibir
        # Gancho opcional que se llama cuando el cliente RECUPERA la conexión,
        # nunca en la primera. Lo usa el reloj de inactividad (`inactividad.Estado`)
        # para reiniciar su reloj tras un corte: durante el corte no llegaron los
        # `presencia`, así que avisar justo al reconectar sería una alarma ciega.
        # En la primera conexión NO se llama: si se llamara, cada reinicio del
        # orquestador sembraría el reloj con la hora de arranque y una inactividad
        # larga quedaría tapada por un reinicio.
        self.al_reconectar = al_reconectar
        self._primera_conexion = True
        self.conectado = False
        self.contador_ayuda = 0
        # Hilo en el que paho ejecuta sus callbacks (`on_connect`, `on_message`,
        # ...). Se guarda en `_al_conectar` y sirve para no esperar el PUBACK
        # cuando se publica desde dentro de un callback: ese PUBACK lo procesa el
        # mismo hilo, así que esperarlo se autobloquea hasta el `timeout`.
        self._hilo_red = None
        self.cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.MQTT_CLIENT_ID,
            protocol=mqtt.MQTTv311,
            clean_session=config.MQTT_SESION_LIMPIA,
        )

        password = config.MQTT_ORQUESTADOR_PASSWORD
        if not password:
            raise RuntimeError(
                "FALTA MQTT_ORQUESTADOR_PASSWORD en el entorno. "
                "Cargalo con: set -a; . ./.env; set +a"
            )
        self.cliente.username_pw_set(config.MQTT_USUARIO, password)

        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_disconnect = self._al_desconectar
        self.cliente.on_message = self._al_mensaje

    def _al_conectar(self, cliente, usuario, banderas, codigo,
                     propiedades=None):
        self.conectado = True
        # Todos los callbacks de paho corren en el hilo de red. Guardarlo aquí
        # (que es el primer callback que llega) permite que `publicar` reconozca
        # después si lo están llamando desde ese mismo hilo.
        self._hilo_red = threading.current_thread()
        # `codigo` es un objeto ReasonCode de paho 2.x, no un entero. No admite
        # `int()` ni comparacion con `!= 0`: hay que preguntarle con
        # `is_failure`, que es la API de la 2.x. En la 1.x esto era un `rc == 0`
        # y por eso el mismo codigo no funciona en las dos versiones.
        if codigo.is_failure:
            registro.error("conexion rechazada por el broker (rc=%s)", codigo)
            self.conectado = False
            return
        registro.info("conectado a %s:%d como %s (client id %s)",
                      config.MQTT_HOST, config.MQTT_PUERTO,
                      config.MQTT_USUARIO, config.MQTT_CLIENT_ID)
        self.suscribirse()
        # La configuración del umbral de gas se publica en CADA conexión (arranque
        # y reconexión), con retain y QoS 1. Retain para que el ESP32 y el panel
        # la reciban aunque se conecten después, y en cada conexión para que el
        # mensaje retenido siempre refleje lo que dice `config.py` de ESTE
        # proceso (si alguien cambia el `.env` y reinicia, el broker no se queda
        # con el valor viejo).
        try:
            self.publicar_config_nodo()
        except Exception:  # noqa: BLE001
            # Un fallo aquí no puede tumbar el hilo de red de paho: el resto
            # (suscripciones y mensajes) sigue funcionando igual.
            registro.exception("no se pudo publicar %s; se ignora",
                               config.TOPICO_NODO_CONFIG)
        # El aviso de reconexión va DESPUÉS de suscribirse: si el gancho publicara
        # algo, ya tiene las suscripciones puestas. La primera vez no se llama (ver
        # el comentario de `_primera_conexion` en `__init__`).
        if self._primera_conexion:
            self._primera_conexion = False
        elif self.al_reconectar is not None:
            try:
                self.al_reconectar()
            except Exception:  # noqa: BLE001
                # Un fallo aquí no puede tumbar el hilo de red de paho: el resto
                # de los mensajes se sigue recibiendo.
                registro.exception("fallo en el aviso de reconexion; se ignora")

    def suscribirse(self) -> None:
        """Se suscribe a los dos tópicos de alerta, y comprueba si pudo.

        Va aquí, dentro de `on_connect`, y NO en el `__init__`, por un motivo que
        costó un rato entender: `subscribe()` antes de `connect()` devuelve
        `MQTT_ERR_NO_CONN` y no hace nada. No lanza excepción, no avisa, y el
        cliente queda "conectado" y sin ninguna suscripción. El síntoma es
        desconcertante: el log dice "suscrito a ...", el broker está sano, otro
        suscriptor ve los mensajes, y este servicio no ve nada.

        Por eso además se comprueba el código de retorno de cada `subscribe()`. Con
        una ACL que se niega, el broker responde con un SUBACK de fallo (128) y
        paho lo ignora en silencio: es el mismo problema un nivel más arriba, y
        también se ve al log.
        """
        # Solo los topicos que este servicio necesita, y solo de lectura: la voz
        # reconocida, el estado de los satelites (Fase 2C, para repetir el gas sin
        # atender y entregar los recordatorios pendientes), y las tres señales del
        # reloj de inactividad (Fase 4): los sensores (la presencia), el estado del
        # nodo y el estado del enlace del sitio en `$SYS`. No se suscribe a
        # `alerta/#` ni a `casa/#` a proposito, para que un topico nuevo no empiece
        # a llegar sin revisarlo.
        for topico in (config.TOPICO_ALERTA_GAS, config.TOPICO_ALERTA_AYUDA,
                       config.TOPICO_VOZ_TEXTO, config.TOPICO_SATELITE_ESTADO,
                       config.TOPICO_SENSORES, config.TOPICO_NODO_ESTADO,
                       config.TOPICO_ENLACE_SITIO, config.TOPICO_LUZ_ESTADO):
            resultado, _ = self.cliente.subscribe(topico, qos=config.QOS_ALERTAS)
            if resultado != mqtt.MQTT_ERR_SUCCESS:
                registro.error("NO se pudo suscribir a %s (error %d). La ACL de "
                               "'%s' en el acl.conf quiza no le da lectura.",
                               topico, resultado, config.MQTT_USUARIO)
            else:
                registro.info("suscrito a %s con QoS %d, sesion persistente=%s",
                              topico, config.QOS_ALERTAS,
                              "no" if config.MQTT_SESION_LIMPIA else "si")

    def _al_desconectar(self, cliente, usuario, banderas, codigo,
                       propiedades=None):
        self.conectado = False
        # Un rc de exito es una desconexion limpia, que es lo que pasa cuando el
        # proceso esta saliendo. No es un error y no merece una linea de ERROR.
        if not codigo.is_failure:
            registro.info("desconectado limpiamente")
        else:
            registro.warning("desconectado (rc=%s); reintentando", codigo)

    def _al_mensaje(self, cliente, usuario, mensaje):
        """Pasa el mensaje crudo a la capa de arriba.

        Aquí no se descompila el JSON ni se mira el tipo. Lo único que se descarta
        es lo que no es UTF-8, porque a `alertas.py` le llega el texto ya.
        """
        try:
            cuerpo = mensaje.payload.decode("utf-8")
        except UnicodeDecodeError:
            registro.warning("descartado de %s: no es UTF-8", mensaje.topic)
            return
        if mensaje.topic == config.TOPICO_VOZ_TEXTO:
            registro.info("mensaje en %s%s: voz/texto (payload omitido, %d bytes)",
                          mensaje.topic, " (retenido)" if mensaje.retain else "",
                          len(cuerpo))
        else:
            registro.info("mensaje en %s%s: %s", mensaje.topic,
                          " (retenido)" if mensaje.retain else "", cuerpo)
        # El `try` NO es opcional, y esto es de lo más importante del archivo.
        #
        # `on_message` se llama DENTRO del hilo de red de paho. Si la función de
        # aquí lanza una excepción, esa excepción sube por el hilo de paho y lo
        # mata. El proceso sigue vivo, `docker ps` dice "running", el log de
        # arranque dice "conectado", y el servicio ha dejado de recibir mensajes
        # para siempre sin decir nada. Solo se entera uno porque las alertas dejan
        # de llegar a Telegram, y para entonces el corte lleva horas.
        #
        # Un mensaje mal formado no puede costar la recepción del resto.
        #
        # `retain` le dice al de arriba si es un retenido que llega por suscribirse
        # (y no algo que acaba de pasar): MQTT 3.1.1 lo pone a 1 solo en ese caso,
        # aunque quien publico lo hubiera retenido.
        try:
            self.al_recibir(mensaje.topic, cuerpo, mensaje.retain)
        except Exception:  # noqa: BLE001
            registro.exception("fallo al procesar el mensaje de %s; el resto de "
                               "los mensajes se sigue recibiendo", mensaje.topic)

    def conectar(self) -> None:
        registro.info("conectando a %s:%d...", config.MQTT_HOST,
                      config.MQTT_PUERTO)
        self.cliente.connect(
            config.MQTT_HOST, config.MQTT_PUERTO,
            keepalive=config.MQTT_KEEPALIVE,
        )
        self.cliente.loop_start()

    def desconectar(self) -> None:
        self.cliente.disconnect()
        self.cliente.loop_stop()

    # --- Publicaciones, para las pruebas -----------------------------------

    def publicar(self, topico: str, carga: dict, qos: int = 1,
                 retain: bool = False, esperar: bool = True) -> None:
        """Publica un mensaje. Lo usa el registro y las pruebas.

        `esperar=True` aguanta hasta el PUBACK con `wait_for_publish`. Eso vale
        desde el hilo propio de quien publica, pero NO desde dentro de un callback
        de paho: `on_message` corre en el hilo de red, y el PUBACK lo procesa ese
        mismo hilo, así que esperarlo ahí se autobloquea hasta el `timeout` (5 s) y
        el mensaje no sale hasta entonces.

        Por eso `publicar` detecta si lo llaman desde el hilo de red de paho (el
        que se guarda en `_hilo_red`): en ese caso no espera nunca, aunque se pida
        `esperar=True`. Así ningún camino que publique desde un callback —el eco de
        la voz, y cualquier tópico que se añada mañana— puede autobloquearse por
        olvidar pasar `esperar=False`. El camino de la voz lo pasa además de forma
        explícita, para que la intención se lea en el sitio de la llamada.

        Nota importante sobre la ACL: el usuario `orquestador` puede escribir en
        `luz/set`, `satelite/decir`, `nodo/config` y `alerta/ayuda`, pero **no** en
        `casa/sala/alerta/gas`. Publicar una alerta de gas desde aquí no da error:
        el broker acepta la conexión, rechaza el mensaje en silencio y no llega
        nada. Para probar una alerta de gas hay que usar el usuario `nodo`.
        """
        cuerpo = json.dumps(carga, separators=(",", ":"))
        resultado = self.cliente.publish(
            topico, cuerpo, qos=qos, retain=retain
        )
        en_callback = threading.current_thread() is self._hilo_red
        if esperar and not en_callback:
            resultado.wait_for_publish(timeout=5)
            if topico == config.TOPICO_SATELITE_DECIR:
                registro.info("publicado en %s: satelite/decir (payload omitido, %d bytes)",
                              topico, len(cuerpo))
            else:
                registro.info("publicado en %s: %s", topico, cuerpo)
        else:
            motivo = ("encolado desde el hilo de red de paho"
                      if en_callback else "encolado sin esperar el PUBACK")
            if topico == config.TOPICO_SATELITE_DECIR:
                registro.info("%s en %s: satelite/decir (payload omitido, %d bytes)",
                              motivo, topico, len(cuerpo))
            else:
                registro.info("%s en %s: %s", motivo, topico, cuerpo)

    def publicar_config_nodo(self) -> None:
        """Publica la configuración del umbral de gas en `nodo/config`.

        `{"gas_on_adc":1800,"gas_off_adc":1550}` con retain y QoS 1, que es el
        formato que el firmware y el panel esperan. Retain para que cualquiera
        que se suscriba después la reciba, y QoS 1 para que llegue aunque el
        ACK se pierda.
        """
        self.publicar(
            config.TOPICO_NODO_CONFIG,
            {"gas_on_adc": config.GAS_ON_ADC,
             "gas_off_adc": config.GAS_OFF_ADC},
            qos=1,
            retain=True,
        )

    def publicar_luz(self, estado: str, origen: str = "panel") -> None:
        import time
        self.publicar(
            "casa/sala/luz/set",
            {"estado": estado, "origen": origen, "ts": int(time.time())},
            qos=1,
        )

    def publicar_ayuda(self, origen: str = "voz") -> None:
        """Publica una alerta de ayuda con el formato exacto del contrato.

        `{"id":"...","origen":"voz"}`, que es lo que manda el ESP32 cuando la
        petición llega por Alexa y lo que manda el servidor cuando llega por voz
        (plan, línea 183). Antes este método publicaba `tipo`, `mensaje` y
        `urgencia`, que no son campos del contrato, y el orquestador los leía en
        lugar de mirar el tópico.

        El id se arma con el prefijo `srv-`, que es el del servidor, con el
        reloj y con un contador: tiene que ser único y tiene que pasar el filtro
        de `alertas.ID_VALIDO` (minúsculas, dígitos y guiones).
        """
        import time
        ahora = int(time.time())
        self.contador_ayuda += 1
        self.publicar(
            config.TOPICO_ALERTA_AYUDA,
            {"id": "srv-%08x-%04x" % (ahora % 0xFFFFFFFF, self.contador_ayuda),
             "origen": origen},
            qos=1,
        )

    def publicar_decir(self, texto: str, tipo: str = "respuesta") -> None:
        """Publica lo que el asistente dice en voz alta.

        El `ts` va como entero epoch, que es lo que pide la regla de antigüedad del
        contrato, y sin retención, porque es un hecho que pasó en un momento.
        """
        import time
        self.publicar(
            config.TOPICO_SATELITE_DECIR,
            {"texto": texto, "tipo": tipo, "ts": int(time.time())},
            qos=1,
        )
