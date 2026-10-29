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
import inactividad
import mqtt_cliente
import recordatorios
import registro
import sanear as sanear_modulo
import telegram_cliente
import textos
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


def tarea_recordatorios(base, publicar, satelites, parando) -> None:
    """El ciclo de los recordatorios, cada `SEGUNDOS_CICLO_RECORDATORIOS`.

    Va en un hilo aparte por el mismo motivo que la purga: es una consulta a
    SQLite más, cuando toca, una publicación de MQTT, y hacerlo en el bucle
    principal pararía la recepción de alertas durante la consulta. La `publicar`
    que se pasa es la del cliente MQTT, que espera el PUBACK: este hilo no es el
    de red de paho, así que sí puede esperarlo.

    Usa `parando.wait()` en vez de `sleep()` para que el SIGTERM lo despierte al
    instante y no tarde hasta 30 s en salir.
    """
    while not parando.is_set():
        try:
            procesados = recordatorios.procesar_vencidos(base, publicar, satelites)
            if procesados:
                registro_log.info("recordatorios vencidos procesados: %d",
                                  procesados)
        except Exception as error:  # noqa: BLE001
            registro_log.error("fallo en el ciclo de recordatorios: %s", error)
        parando.wait(config.SEGUNDOS_CICLO_RECORDATORIOS)


def tarea_inactividad(reloj, publicar, parando) -> None:
    """El ciclo del reloj de inactividad, cada `SEGUNDOS_CICLO_INACTIVIDAD`.

    Va en un hilo aparte, como la purga y los recordatorios: `verificar` consulta
    SQLite (el tiempo configurado) y puede publicar en MQTT y encolar una alerta, y
    hacerlo en el bucle principal pararía la recepción de mensajes. Se relee el
    ajuste en cada vuelta, así que un cambio del panel se aplica sin reiniciar.

    Usa `parando.wait()` en vez de `sleep()` para que el SIGTERM lo despierte al
    instante.
    """
    while not parando.is_set():
        try:
            if reloj.verificar(publicar):
                registro_log.info("reloj de inactividad: alerta encolada")
        except Exception as error:  # noqa: BLE001
            registro_log.error("fallo en el ciclo de inactividad: %s", error)
        parando.wait(config.SEGUNDOS_CICLO_INACTIVIDAD)


def tarea_conversacion(base, publicar, remitente, satelites, parando) -> None:
    """Procesa los mensajes nuevos de la conversacion (Paso 6)."""
    while not parando.is_set():
        try:
            for fila in base.mensajes_pendientes_conversacion():
                id_conv = fila["id"]
                texto = (fila["texto"] or "").strip()
                origen = fila["origen"]
                autor = fila["autor"] or "Familiar"
                autor_chat = fila["autor_chat_id"]
                hay_sat = (satelites is not None and satelites.hay_conectado())
                es_aviso = (fila["tipo"] == "aviso")
                if (not es_aviso) and hay_sat:
                    try:
                        publicar(config.TOPICO_SATELITE_DECIR,
                                 {"texto": "Mensaje de %s: %s. Si quiere responder, mantenga presionado el botón y hable."
                                  % (autor, texto), "tipo": "chat",
                                  "ts": int(time.time())},
                                 qos=1)
                        base.actualizar_estado_conversacion(id_conv, "se_dijo")
                        voz.abrir_ventana_respuesta()
                    except Exception as error:  # noqa: BLE001
                        registro_log.error("no se pudo decir el mensaje %s: %s",
                                           id_conv, error)
                        base.actualizar_estado_conversacion(id_conv, "no_se_pudo_decir")
                elif not es_aviso:
                    base.actualizar_estado_conversacion(id_conv, "no_se_pudo_decir")
                else:
                    base.actualizar_estado_conversacion(id_conv, "se_dijo")

                # Fan out por Telegram.
                estado_final = base.obtener_conversacion(id_conv)["estado"]
                if origen == "panel":
                    for fam in base.familiares_con_telegram():
                        chat = str(fam["chat_id"])
                        try:
                            mensaje = remitente.enviar(
                                textos.CHAT_DESDE_PANEL.format(
                                    nombre=autor, texto=texto),
                                chat_id=chat,
                            )
                            base.crear_envio_conversacion(
                                id_conv, chat, mensaje_id=getattr(mensaje, "message_id", None))
                        except Exception as error:  # noqa: BLE001
                            registro_log.warning("no se pudo mandar el mensaje del chat a %s: %s",
                                                 db.enmascarar_chat(chat),
                                                 telegram_cliente.sanear(error))
                    # Editar todos cuando el estado sea final.
                    if estado_final in ("se_dijo", "no_se_pudo_decir"):
                        for envio in base.envios_conversacion_a_editar(id_conv):
                            nuevo_texto = (textos.CHAT_CONFIRMACION_SE_DJO if estado_final == "se_dijo"
                                           else textos.CHAT_CONFIRMACION_NO_SE_PUDO).format(texto=texto)
                            try:
                                remitente.editar_mensaje(envio["chat_id"], envio["mensaje_id"],
                                                         nuevo_texto)
                            except Exception as error:  # noqa: BLE001
                                registro_log.warning("no se pudo editar el mensaje del chat: %s",
                                                     telegram_cliente.sanear(error))
                            base.marcar_envio_conversacion_editado(id_conv, envio["chat_id"])
                else:
                    # origen telegram: el autor ya recibio confirmacion en bot.py;
                    # aqui solo se asegura que este marcada como notificada. Los demas
                    # ya se notificaron desde bot.py.
                    pass

                # Marcar notificado para el panel (polling) - no hace falta, panel lee por id.
                base.marcar_conversacion_notificada(id_conv)
        except Exception as error:  # noqa: BLE001
            registro_log.error("fallo en el ciclo de conversacion: %s", error)
        # Respuestas de la sala: notificar por Telegram.
        try:
            for fila in base.mensajes_enviados_sin_notificar():
                if fila["tipo"] != "respuesta":
                    # alertas ya se notifican por su pipeline; solo marcar.
                    base.marcar_conversacion_notificada(fila["id"])
                    continue
                texto = fila["texto"] or ""
                for fam in base.familiares_con_telegram():
                    chat = str(fam["chat_id"])
                    try:
                        remitente.enviar(
                            textos.CHAT_RESPUESTA_SALA.format(texto=texto),
                            chat_id=chat,
                        )
                    except Exception as error:  # noqa: BLE001
                        registro_log.warning("no se pudo mandar la respuesta de la sala a %s: %s",
                                             db.enmascarar_chat(chat),
                                             telegram_cliente.sanear(error))
                base.marcar_conversacion_notificada(fila["id"])
        except Exception as error:  # noqa: BLE001
            registro_log.error("fallo notificando respuestas: %s", error)
        parando.wait(1.0)


def enrutar(topico, cuerpo, base, publicar, retenido=False, satelites=None,
            reloj=None) -> None:
    """Reparte un mensaje de MQTT a quien lo procesa.

    Es una funcion y no un `if` suelto para que se pueda probar sola, y para que el
    punto donde se decide «que topico va con que procesador» sea uno solo y este a
    la vista. Hay cuatro caminos: el texto de voz; el estado de los satelites (para
    repetir el gas sin atender y entregar los recordatorios pendientes al que se
    conecta); las tres señales del reloj de inactividad (los sensores, el estado
    del nodo y el estado del enlace del sitio); y todo lo demas (las alertas).

    El texto de voz NO pasa por `alertas.procesar`: si pasara, y como esa funcion
    decide el tipo por el topico, un `casa/sala/voz/texto` caeria en su rama de
    «topico no atendido» y no se haria nada con el.

    Los topicos del reloj de inactividad tampoco pasan por `alertas.procesar`: no
    son alertas, son las señales con las que `inactividad.Estado` lleva la cuenta
    del tiempo sin movimiento. El del enlace se reconoce por forma
    (`inactividad.es_enlace_sitio`) y no por igualdad, porque lleva un `+`.

    `satelites` y `reloj` son los dos registros en memoria. Los dos son opcionales
    para que las pruebas viejas de `enrutar` sigan funcionando sin construirlos: sin
    `reloj`, las señales de inactividad se ignoran (que es lo que hacian antes de
    que existiera la Fase 4).
    """
    if topico == config.TOPICO_VOZ_TEXTO:
        voz.procesar(cuerpo, publicar, base)
    elif alertas.client_id_de_estado(topico) is not None:
        alertas.al_estado_satelite(topico, cuerpo, retenido, base, publicar,
                                   satelites=satelites)
    elif reloj is not None and topico == config.TOPICO_SENSORES:
        reloj.registrar_sensores(cuerpo)
        voz.actualizar_sensores(cuerpo)
    elif reloj is not None and topico == config.TOPICO_NODO_ESTADO:
        reloj.registrar_nodo(cuerpo)
        voz.actualizar_nodo(cuerpo)
    elif topico == config.TOPICO_LUZ_ESTADO:
        voz.actualizar_estado_luz(cuerpo)
    elif reloj is not None and inactividad.es_enlace_sitio(topico):
        reloj.registrar_enlace(cuerpo)
    else:
        alertas.procesar(topico, cuerpo, base, publicar)


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
    # El eco de la voz se publica desde dentro de `on_message`, o sea en el hilo
    # de red de paho. `publicar` espera el PUBACK con `wait_for_publish`, y ese
    # PUBACK lo procesa el propio hilo de red: esperarlo ahi se autobloquea los 5 s
    # del timeout y el eco sale tarde. Por eso este camino publica con
    # `esperar=False`; el mensaje se encola y sale en cuanto el callback termina.
    # Las demas publicaciones (bot, registro) van desde otros hilos y si esperan.
    def publicar_eco(topico, carga, qos=1, retain=False):
        cliente.publicar(topico, carga, qos=qos, retain=retain, esperar=False)

    # El registro de satelites conectados. Lo escriben los mensajes de estado (en
    # el hilo de red de paho) y lo lee el ciclo de recordatorios (en su hilo), por
    # eso lleva su propio cerrojo dentro. Se siembra solo: el estado de un satelite
    # conectado llega retenido al suscribirse.
    satelites = recordatorios.Satelites()

    # El reloj de la inactividad (Fase 4). Se lee/hereda `ultimo_movimiento` de la
    # base al construirlo (o se siembra con la hora de arranque), y a partir de ahi
    # lo mueven los mensajes de sensores/nodo/enlace (en el hilo de red de paho) y
    # el ciclo de su hilo. Lleva su propio cerrojo dentro.
    reloj = inactividad.Estado(base)

    cliente = mqtt_cliente.ClienteMQTT(
        lambda topico, cuerpo, retenido=False: enrutar(topico, cuerpo, base,
                                                       publicar_eco, retenido,
                                                       satelites, reloj),
        # El gancho de reconexión reinicia el reloj tras un corte de MQTT (no en la
        # primera conexión): durante el corte no llegaron los `presencia`, así que
        # avisar justo al reconectar sería una alarma ciega.
        al_reconectar=reloj.registrar_reconexion,
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

        # El ciclo de recordatorios. Se arranca DESPUES de `cliente.conectar()`,
        # porque el primer pase puede querer publicar y publicar sin estar
        # conectado devuelve MQTT_ERR_NO_CONN en silencio. Se pasa
        # `cliente.publicar` (no `publicar_eco`): este hilo no es el de red de
        # paho, asi que puede esperar el PUBACK.
        hilo_recordatorios = threading.Thread(
            target=tarea_recordatorios, args=(base, cliente.publicar, satelites,
                                              parando),
            name="recordatorios", daemon=True)
        hilo_recordatorios.start()

        # El ciclo del reloj de inactividad. También después de `conectar()`, por el
        # mismo motivo que los recordatorios: un primer pase puede querer publicar
        # la alerta en MQTT. Se pasa `cliente.publicar` (no `publicar_eco`): este
        # hilo no es el de red de paho y puede esperar el PUBACK.
        hilo_inactividad = threading.Thread(
            target=tarea_inactividad, args=(reloj, cliente.publicar, parando),
            name="inactividad", daemon=True)
        hilo_inactividad.start()

        hilo_conversacion = threading.Thread(
            target=tarea_conversacion, args=(base, cliente.publicar, remitente,
                                             satelites, parando),
            name="conversacion", daemon=True)
        hilo_conversacion.start()

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
