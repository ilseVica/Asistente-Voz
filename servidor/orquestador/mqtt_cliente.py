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

import paho.mqtt.client as mqtt

import config

registro = logging.getLogger("mqtt")


class ClienteMQTT:
    def __init__(self, al_recibir):
        self.al_recibir = al_recibir
        self.conectado = False
        self.contador_ayuda = 0
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
        # Solo los dos topicos de alerta y el texto de voz, y solo de lectura: este
        # servicio escucha gas, ayuda y lo que reconocio el servicio de voz, y nada
        # mas. No se suscribe a `alerta/#` ni a `casa/#` a proposito, para que un
        # topico nuevo no empiece a llegar sin revisarlo.
        for topico in (config.TOPICO_ALERTA_GAS, config.TOPICO_ALERTA_AYUDA,
                       config.TOPICO_VOZ_TEXTO):
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
        registro.info("mensaje en %s: %s", mensaje.topic, cuerpo)
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
        try:
            self.al_recibir(mensaje.topic, cuerpo)
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
                 retain: bool = False) -> None:
        """Publica un mensaje. Lo usa el registro y las pruebas.

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
        resultado.wait_for_publish(timeout=5)
        registro.info("publicado en %s: %s", topico, cuerpo)

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
