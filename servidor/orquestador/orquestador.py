"""El programa principal del orquestador.

Son cinco cosas, y ninguna es interesante: leer el entorno, abrir la base,
conectarse a MQTT, arrancar el trabajador de la cola y no salir nunca.

Lo que sí importa es el orden, y hay dos decisiones en él:

**El arranque no falla si Telegram no está.** Si `Remitente` se construyera al
principio y su `__init__` comprobara el token con `sys.exit`, el servicio no
arrancaría sin token. El usuario confirmó que tiene que arrancar igual, así que
el `Remitente` se construye siempre y es él mismo el que registra el error.

**`finally` con parada ordenada.** MQTT, el trabajador de la cola y el bot se
cierran en ese orden. Al revés, el bot seguiría mandando cosas con la base ya
cerrada, y el trabajo se perdería al final del `SIGTERM`, que es
justo cuando el orquestador se reinicia para una actualización.
"""

import logging
import os
import signal
import sys
import threading
import time

import alertas
import bot
import config
import db
import cola
import mqtt_cliente
import registro
import sanear as sanear_modulo
import telegram_cliente
import voz

registro_log = logging.getLogger("orquestador")


def configurar_log() -> None:
    niveles = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "AVISO": logging.WARNING,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    nivel = niveles.get(config.NIVEL_LOG, logging.INFO)
    formato = "%(asctime)s %(levelname)-7s %(name)-10s %(message)s"
    logging.basicConfig(level=nivel, format=formato, datefmt="%H:%M:%S")
    # paho y python-telegram-bot son muy verbosos en DEBUG y no aportan nada aquí.
    logging.getLogger("paho").setLevel(max(nivel, logging.WARNING))
    logging.getLogger("telegram.ext").setLevel(max(nivel, logging.WARNING))
    logging.getLogger("apscheduler").setLevel(max(nivel, logging.WARNING))
    # httpx y httpcore, en cambio, callan al importar `telegram_cliente`, y
    # `configurar_log()` no los repite a proposito. Motivo: si el token puede
    # filtrarse por un log de INFO de httpx, la unica garantia buena es que estar
    # importado el modulo que habla con Telegram ya imponga el silencio, sin
    # depender de que el proceso pasara por aqui. Este servicio si pasa, pero el
    # 2026-10-05 filtro un token justo por un script que no lo hizo.
    import telegram_cliente  # noqa: F401
    telegram_cliente.silenciar_loggers_de_red()

    # Y despues el filtro en los manejadores, que es la red de seguridad: aunque un
    # nivel se vuelva a bajar, o una libreria nueva escriba el token por su cuenta,
    # lo que llegue al manejador sale sin el. Va aqui y no antes del `basicConfig`
    # porque sin manejador no hay nada que filtrar.
    sanear_modulo.instalar_filtro_secretos()


def log_resumen() -> None:
    """El arranque, sin secretos.

    Se imprime en varias líneas y no como un solo `repr` de un diccionario,
    porque una sola línea de 200 caracteres es incómoda de leer en `docker logs`
    y porque es más fácil que se vea el token como "SIN TOKEN" que no como una
    cadena vacía.
    """
    resumen = config.resumen()
    registro_log.info("orquestador arrancando")
    registro_log.info("  mqtt: %s como %s (client id %s)",
                      resumen["mqtt"], resumen["usuario_mqtt"],
                      resumen["client_id"])
    registro_log.info("  sesion persistente: %s",
                      "no" if resumen["sesion_limpia"] else "si")
    registro_log.info("  telegram: %s, bot de comandos %s",
                      resumen["telegram"], resumen["bot"])
    registro_log.info("  panel: %s", resumen["panel"])
    registro_log.info("  eco de voz: %s",
                      "activo" if config.ECO_VOZ else "inactivo")
    registro_log.info("  base de datos: %s", resumen["db"])


def tarea_purga(base) -> None:
    """Borra los ids viejos y las alertas viejas, cada hora.

    En un hilo aparte con su propio `time.sleep`, y no en el bucle principal: la
    purga es un `DELETE` sobre dos tablas y puede tardar lo suyo si hay muchos
    ids, y bloquear el bucle principal pararía la recepción de alertas.
    """
    while True:
        try:
            borrados_ids, borrados_alertas = base.purgar()
            if borrados_ids or borrados_alertas:
                registro_log.info("purga: %d ids y %d alertas borrados",
                                  borrados_ids, borrados_alertas)
        except Exception as error:  # noqa: BLE001
            # Una purga que falla se repite en la próxima hora. Perder una purga
            # es solo un poco más de disco.
            registro_log.error("fallo en la purga: %s", error)
        time.sleep(config.SEGUNDOS_PURGA)


def enrutar(topico, cuerpo, base, publicar) -> None:
    """Reparte un mensaje de MQTT a quien lo procesa.

    Es una funcion y no un `if` suelto para que se pueda probar sola, y para que el
    punto donde se decide «que topico va con que procesador» sea uno solo y este a
    la vista. Hoy hay dos caminos: el texto de voz y todo lo demas (las alertas).

    El texto de voz NO pasa por `alertas.procesar`: si pasara, y como esa funcion
    decide el tipo por el topico, un `casa/sala/voz/texto` caeria en su rama de
    «topico no atendido» y no se haria nada con el.
    """
    if topico == config.TOPICO_VOZ_TEXTO:
        voz.procesar(cuerpo, publicar)
    else:
        alertas.procesar(topico, cuerpo, base)


def main() -> int:
    configurar_log()
    log_resumen()

    parando = threading.Event()

    def pedir_salida(numero, marco):
        registro_log.info("senal %d recibida, cerrando", numero)
        parando.set()

    signal.signal(signal.SIGTERM, pedir_salida)
    signal.signal(signal.SIGINT, pedir_salida)

    base = db.BaseDatos()
    registro_log.info("base de datos lista en %s", base.ruta)

    carpeta_datos = os.path.dirname(config.RUTA_DB)

    # La migración del familiar único. `db.BaseDatos()` ya ha repartido la cola vieja
    # en la tabla de envios, y para hacerlo necesitaba saber quien era el familiar de
    # entonces, que es lo que lee `familia.json`. Ahora ese mismo familiar se copia a
    # la tabla y el archivo se renombra, para que la lista de verdad sea la tabla y
    # no un archivo con un único `chat_id`.
    registro.migrar_familia_legacy(base)

    # Atajo de instalación en frio: si hay un `TELEGRAM_CHAT_ID` en el entorno y no
    # hay ningun familiar, ese chat se da de alta. Es la unicavia de registrar a
    # alguien sin pasar por el bot, y existe desde antes: `Remitente` lo usaba como
    # destino por defecto. Con el bot funcionando no hace falta, y por eso sigue
    # siendo solo un atajo.
    if config.TELEGRAM_CHAT_ID and base.contar_familiares() == 0:
        base.registrar_familiar(config.TELEGRAM_CHAT_ID, nombre="")
        registro_log.info("familiar dado de alta desde TELEGRAM_CHAT_ID (%s)",
                          db.enmascarar_chat(config.TELEGRAM_CHAT_ID))

    if base.contar_familiares():
        registro_log.info("familiares registrados: %d",
                          base.contar_familiares())
    else:
        registro_log.info("no hay ningun familiar registrado; las alertas se guardan "
                          "y salen en cuanto uno se registra")

    # Los codigos de registro se cargan una vez al arrancar. El bot de comandos vive
    # en este proceso, asi que de ahí en adelante los tiene en memoria; el archivo
    # es la fuente de verdad y lo escribe el comando `python -m registro código`,
    # que corre en otro proceso.
    registro.cargar_codigos(os.path.join(carpeta_datos, "codigos.json"))
    registro_log.info("codigos de registro cargados: %d", len(registro.CODIGOS))

    remitente = telegram_cliente.Remitente()
    if remitente.token:
        registro_log.info("token de Telegram presente")
        if remitente.verificar():
            registro_log.info("token de Telegram valido")
        else:
            registro_log.error("el token de Telegram no responde; las alertas se "
                               "encolaran pero no se envian hasta que se arregle")
    else:
        registro_log.error("no hay TELEGRAM_BOT_TOKEN. El servicio arranca y "
                           "recibe alertas, pero no puede mandarlas. Anadelo al "
                           ".env del servidor y reinicia el servicio.")

    # `alertas.procesar` necesita la base para deduplicar, y el cliente MQTT solo
    # recibe dos argumentos (el topico y el cuerpo). Se pasa una función
    # intermedia que cierre la base, en vez de cambiar la firma de una de las dos:
    # el cliente no tiene por que saber de bases de datos, y el procesador no
    # tiene por que saber de MQTT.
    #
    # El reparto por topico va aqui y NO dentro del cliente MQTT a proposito: asi
    # el transporte sigue sin saber que existe la voz, y cualquier topico que no
    # sea el de voz sigue el camino de siempre, que es `alertas.procesar`. Un
    # `TOPICO_VOZ_TEXTO` mal escrito no enruta a otro sitio: cae en alertas y sale
    # por el log como topico no atendido.
    cliente = mqtt_cliente.ClienteMQTT(
        lambda topico, cuerpo: enrutar(topico, cuerpo, base, cliente.publicar)
    )

    trabajador = cola.TrabajadorTelegram(base, remitente)

    # El bot se construye despues del cliente MQTT y se ARRANCA despues de
    # `conectar()`, mas abajo. Necesita las dos cosas: el bot publica en
    # `satelite/decir` cuando alguien pulsa "Ya lo atendi", y publicar sin estar
    # conectado devuelve MQTT_ERR_NO_CONN en silencio.
    #
    # La variable local NO puede llamarse `bot`: al principio del archivo se importa
    # el modulo `bot`, y una variable local con el mismo nombre lo tapa dentro de
    # `main`. Con TELEGRAM_ACTIVAR_BOT=1 eso era un `AttributeError: 'NoneType'
    # object has no attribute 'ClienteBot'` en cada arranque: el servicio entraba en
    # un bucle de reinicios y el bot de comandos no llegaba a existir. Con
    # TELEGRAM_ACTIVAR_BOT=0 la rama no se ejecutaba y el bug no se veía.
    cliente_bot = None
    if config.TELEGRAM_ACTIVAR_BOT:
        cliente_bot = bot.ClienteBot(base, remitente, cliente.publicar)

    try:
        cliente.conectar()
        trabajador.arrancar()

        # El bot de comandos va el ultimo, con MQTT ya conectado: si el token
        # falla, el servicio sigue recibiendo alertas y vaciando la cola, porque
        # esas dos cosas no dependen de Telegram.
        if cliente_bot is not None:
            threading.Thread(target=cliente_bot.arrancar, name="bot",
                             daemon=True).start()

        hilo_purga = threading.Thread(target=tarea_purga, args=(base,),
                                      name="purga", daemon=True)
        hilo_purga.start()

        conteo = base.contar_por_estado()
        registro_log.info("alertas ya en la base: %s",
                          conteo if conteo else "ninguna")

        while not parando.is_set():
            parando.wait(1.0)

    except Exception as error:  # noqa: BLE001
        registro_log.exception("fallo en el servicio: %s", error)
        return 1
    finally:
        registro_log.info("parando...")
        # En este orden, y no en el del `try`: primero se deja de aceptar trabajo
        # nuevo, despues se vacía la cola, y al final se cierra el almacen.
        try:
            cliente.desconectar()
            trabajador.detener()
            remitente.cerrar()
            base.cerrar()
        except Exception as error:  # noqa: BLE001
            registro_log.error("fallo al cerrar: %s", error)
        registro_log.info("orquestador detenido")

    return 0


if __name__ == "__main__":
    sys.exit(main())
