"""Los comandos y los botones del bot de Telegram.

Abre el bot de comandos en el chat privado de la familia. Hay dos comandos y un
botón:

- `/registro <código>`: da de alta ese chat en la lista de quienes reciben las
  alertas. Los que ya estaban siguen estándolo.
- `/alertas`: cuántas hay en la cola, cuántas se han mandado y cuáles son las
  últimas cinco. Contesta a cualquier familiar registrado.
- Botón **«Ya lo atendí»**: marca la alerta como resuelta, edita el mensaje de
  Telegram para que el familiar vea uno solo con la hora, y publica en
  `casa/sala/satelite/decir` el aviso de que alguien ya está con el aviso (P17).

Enviar las alertas vive en `telegram_cliente.py`. La separación es por ritmo: esto
es un bucle de eventos de Telegram que corre en su propio hilo, y lo otro es una
llamada de red que hace el trabajador de la cola.

## Varios familiares, no uno

La lista de quienes reciben las alertas está en la tabla `familiares` de SQLite
(`db.py`). Cada chat que entra con un código válido se **suma**, y nadie sale por
eso. `alertas.py` hace un envío por cada familiar registrado en el momento de
encolar el aviso, y cada envío lleva su propio contador de intentos, que es lo que
hace que el móvil sin cobertura no retrase el aviso para los demás.

Este bot no lleva la lista: la pregunta "y este chat, ¿está registrado?" la hace
siempre la base (`db.familiar_registrado`), y los tres sitios que la necesitan
preguntan lo mismo. Que el silencio de `/registro`, el permiso de `/alertas` y el
botón de «Ya lo atendí» consulten la misma definición es lo que evita un chat que
puede leer el historial pero no resolver, o al revés.

## El silencio de `/registro` es de un chat, no de todos

Un chat ya registrado que escribe `/registro` no recibe nada. Ni siquiera un «ya
estás registrado»: confirmar que el bot existe ya le dice a cualquiera que hable con
él que este es el bot de la casa, y eso solo se le dice a quien ya está dentro.

Un chat que **no** está registrado sí recibe respuesta, y necesita un código de un
solo uso para darse de alta. Ahí está el otro lado: alguien que descubra el bot en
una conversación privada puede confirmarle que existe. Es el precio de que un
segundo familiar pueda darse de alta solo, que es lo que pide el plan (P20), y no
se ve en la práctica: el código lo genera el operador del servidor y vale 10
minutos.

## Decisiones de seguridad

**Solo chat privado.** En un grupo, el bot publicaría el número de teléfono de la
persona en un canal donde lo pueden leer mil personas. `_es_privado()` descarta
todo lo que no sea un chat de dos.

**`/registro` acepta un código de un solo uso.** El código lo genera el operador
con un comando del contenedor y vale 10 minutos. Sin eso, cualquier persona que
encontrara el bot podría meterse en la lista de quien recibe los avisos de gas de
una persona mayor.

**Los `chat_id` no salen enteros al log.** Todo lo que se registra usa
`db.enmascarar_chat`, que deja los últimos cuatro dígitos. Un `chat_id` de Telegram
es el número de una persona y `docker logs` se lee con cualquiera que tenga acceso
al servidor; el entero sale por la consola del operador (`python -m registro
familiares`), que es donde está quien ya tiene el shell.

## `/silencio` se quitó

Hasta la versión anterior este bot tenía un comando `/silencio` que ponía una hora
límite y contestaba «no te aviso durante una hora». Se quitó por dos razones, y las
dos importan:

1. **No estaba en la propuesta aprobada.** Los dos comandos del plan son `/registro`
   y los botones; `/silencio` apareció al escribir el código, sin que nadie lo
   pidiera. Un comando que silencia avisos en un sistema pensado para una persona
   mayor no se agrega por cuenta propia.

2. **Era una puerta a que una alerta de gas no llegara.** El código ponía
   `silencio_hasta = ahora + 3600` y respondía que las alertas «se siguen
   guardando, solo que sin mandarlas». En esa versión la marca no la leía nadie, así
   que el gas llegaba igual: era una mentira del bot, no un silencio real. Lo
   peligroso es el otro escenario, el que aparece cuando alguien lo arregle: un flag
   de ese tipo, en cuanto alguien lo conecta al trabajador de la cola, es exactamente
   el interruptor que puede tragarse un aviso de gas. Un sistema de aviso de
   seguridad no lleva un botón de «no me avises»; si el ruido es un problema real,
   se resuelve con el volumen de la alerta, no con una orden de silencio.

El aviso de gas es lo único que este sistema manda a la familia, y no admite
excepciones: ni por comando ni por botón.
"""

import logging
import threading
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes)

import config
import db as db_modulo
import registro as registro_modulo
import telegram_cliente

registro = logging.getLogger("bot")

PREFIJO_CODIGO = "alerta:"


def teclado_alerta(id_alerta: str) -> InlineKeyboardMarkup:
    """El teclado del mensaje de alerta: «Abrir panel» y «Ya lo atendí».

    «Abrir panel» solo aparece si `PANEL_URL` tiene un valor. Un botón que lleva a
    una página en blanco o a un dominio inexistente hace que el familiar pulse lo
    que sea en la próxima alerta, y ese es el momento exacto en el que el botón
    falso se paga caro.

    El `callback_data` lleva el prefijo `alerta:` y el id de la alerta. El id es lo
    que permite editar el mensaje correcto y no el último enviado, y está
    justificado por el límite de 64 bytes de Telegram: el id del contrato ocupa 19
    caracteres (`000000-b0001-0000`).
    """
    botones = []
    if config.PANEL_URL:
        botones.append(
            InlineKeyboardButton("Abrir panel", url=config.PANEL_URL)
        )
    botones.append(
        InlineKeyboardButton("Ya lo atendí",
                             callback_data=PREFIJO_CODIGO + id_alerta)
    )
    return InlineKeyboardMarkup([[boton] for boton in botones])


class ClienteBot:
    def __init__(self, base, remitente, publicar=None):
        self.base = base
        self.remitente = remitente
        self.publicar = publicar
        self.aplicacion = None

    def arrancar(self) -> None:
        """Levanta el bot. Si no se puede, avisa y sigue sin él.

        Devolver en vez de lanzar es lo correcto: el bot de comandos es un extra
        sobre el servicio de alertas. Si el token está mal, el servicio tiene que
        seguir recibiendo de MQTT y guardando en la base, porque eso no depende
        del bot.
        """
        if not config.TELEGRAM_ACTIVAR_BOT:
            registro.info("bot de comandos inactivo (TELEGRAM_ACTIVAR_BOT=0)")
            return
        if not config.TELEGRAM_BOT_TOKEN:
            registro.warning("no hay token, el bot de comandos no arranca. "
                             "MQTT y la cola de alertas siguen funcionando.")
            return

        try:
            self.aplicacion = (
                ApplicationBuilder()
                .token(config.TELEGRAM_BOT_TOKEN)
                .build()
            )
            self.aplicacion.add_handler(
                CommandHandler("registro", self.cmd_registro)
            )
            self.aplicacion.add_handler(
                CommandHandler("alertas", self.cmd_alertas)
            )
            self.aplicacion.add_handler(
                CallbackQueryHandler(self.cmd_atendi, pattern="^" + PREFIJO_CODIGO)
            )
            self.aplicacion.add_error_handler(self._error)
            registro.info("bot de comandos arrancando")
            # `run_polling` no devuelve nunca. Por eso lo llama un hilo con su
            # propio bucle de eventos, y no `main`.
            #
            # `stop_signals=None` es obligatorio en ese hilo, y no es un detalle de
            # estilo: `run_polling` instala manejadores de SIGINT/SIGTERM/SIGABRT con
            # `loop.add_signal_handler`, y eso llama a `signal.set_wakeup_fd`, que
            # solo funciona en el hilo principal del intérprete principal. Sin ese
            # `None`, el bot moría al arrancar con
            # «RuntimeError: set_wakeup_fd only works in main thread of the main
            # interpreter» y el servicio se caía en un bucle de reinicios.
            #
            # `drop_pending_updates=False` a propósito. Con `True`, cualquier
            # `/registro` que la familia mande mientras el contenedor se reinicia se
            # borra sin avisar: Telegram lo devuelve al volver, y el proceso lo
            # descarta antes de mirarlo. La persona ve que su mensaje se envió y no
            # pasa nada, que es la forma más segura de perder el registro. Con
            # `False` el comando se atiende al arrancar. Lo único que se reprocesa son
            # mensajes de más de una sesión, y aquí todos son de consulta o de alta:
            # `/registro` es silencioso si ese chat ya está registrado y `/alertas`
            # solo lee.
            self.aplicacion.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                stop_signals=None,
                close_loop=False,
            )
        except Exception as error:  # noqa: BLE001
            registro.exception("el bot de comandos no pudo arrancar: %s",
                               telegram_cliente.sanear(error))

    # --- Registro de familiares ---------------------------------------------

    def _es_registrado(self, update: Update) -> bool:
        """¿Viene este comando de un chat que recibe las alertas?

        Una sola pregunta, la de `db.familiar_registrado`, y la misma para los tres
        sitios que la necesitan: el silencio de `/registro`, si `/alertas` contesta,
        y si el botón «Ya lo atendí» se atiende.

        Que sea la misma no es casualidad. Con dos definiciones distintas de
        "registrado" el sistema tiene un caso raro y muy raro de debug: un chat que
        puede resolver una alerta pero no puede ver el historial, o que ve el
        historial pero no puede darse de baja con `/registro`. Sale en una prueba
        concreta y no se entiende mirando el bot.
        """
        if update.effective_chat is None:
            return False
        return self.base.familiar_registrado(update.effective_chat.id)

    async def cmd_registro(self, update: Update,
                           contexto: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._es_privado(update):
            return

        chat_id = str(update.effective_chat.id)
        quien = db_modulo.enmascarar_chat(chat_id)

        # El silencio es SOLO para este chat, y solo si ya esta en la lista. Los
        # demas chats privados no reciben un «ya estas registrado»: eso confirmaria
        # que este es el bot de la casa a cualquiera que lo encontrara. Un chat que
        # no esta en la lista recibe el aviso de que necesita un código, y con un
        # código nuevo se suma a la lista sin quitar a nadie.
        if self.base.familiar_registrado(chat_id):
            return

        argumentos = contexto.args or []
        if not argumentos:
            ya_hay = self.base.contar_familiares()
            plural = "persona" if ya_hay == 1 else "personas"
            aviso = ""
            if ya_hay:
                aviso = ("\n\nAhora mismo hay %d %s registrado(s). Si te registras, "
                         "recibireis las alertas los dos." % (ya_hay, plural))
            await update.effective_message.reply_text(
                "Falta el codigo.\n\n"
                "Escribe:\n"
                "/registro CODIGO\n\n"
                "El codigo te lo da quien instalo el asistente."
                + aviso
            )
            return

        codigo = argumentos[0].strip()
        motivo = registro_modulo.comprobar_codigo(codigo)
        if motivo is not None:
            registro.warning("/registro desde el familiar %s con un codigo que no "
                             "sirve (%s)", quien, motivo)
            await update.effective_message.reply_text(
                "No se pudo registrar: %s.\n\nPide un codigo nuevo." % motivo
            )
            return

        nombre = " ".join(argumentos[1:]).strip()
        nuevo = self.base.registrar_familiar(chat_id, nombre=nombre)
        registro_modulo.marcar_codigo_usado(codigo)

        # Los avisos que se acumularon mientras no había a quien mandarlos salen
        # ahora. Se hace aquí, y no al arrancar el servicio, porque entre el arranque
        # y el registro puede entrar otra alerta: encolar el fan out justo despues
        # de insertar al familiar es donde no puede quedar una alerta sin
        # destinatario por un corte.
        atrasados = self.base.fan_out_sin_destino(chat_id)

        registro.warning("familiar %s dado de alta por /registro con un codigo de un "
                         "solo uso%s", quien,
                         "" if nuevo else " (ya estaba en la lista)")
        if atrasados:
            registro.warning("se encolaron %d aviso(s) que estaban esperando a que "
                             "hubiera un familiar, ahora para %s",
                             atrasados, quien)

        respuesta = (
            "Listo. Desde ahora te aviso aqui si hay una alerta de gas o una "
            "peticion de ayuda.\n\n"
            "Comando: /alertas, para ver el estado."
        )
        if atrasados:
            respuesta += ("\n\n%d aviso(s) que estaban esperando tambien van para "
                          "ti." % atrasados)
        if nuevo and self.base.contar_familiares() > 1:
            respuesta += ("\n\nLas alertas siguientes las recibireis todos los que "
                          "estais registrados.")
        await update.effective_message.reply_text(respuesta)

    # --- /alertas ----------------------------------------------------------

    async def cmd_alertas(self, update: Update,
                          contexto: ContextTypes.DEFAULT_TYPE) -> None:
        """El estado de las alertas, para cualquier familiar registrado.

        Es una LECTURA del historial de la casa: qué hay pendiente, qué se mandó,
        qué se resolvió, y las últimas cinco con su hora.

        Contesta a **cualquier** familiar registrado, y a nadie más. Con dos
        familiares, las dos cosas importan: si contestara solo al primero, el
        segundo vería sus alertas llegar y no podría preguntarle al bot qué pasó;
        y si contestara a cualquiera, bastaría con escribir `/alertas` a un bot
        sospechoso para confirmar que existe y qué ha pasado en la casa. Lo
        primero es una molestia, lo segundo es una fuga.

        Que la misma pregunta sea la del silencio de `/registro` y la del botón de
        «Ya lo atendí» es lo que hace que "estar registrado" signifique una sola
        cosa en todo el bot.
        """
        if not await self._es_privado(update):
            return
        if not self._es_registrado(update):
            return
        conteo = self.base.contar_por_estado()
        recientes = self.base.listar_recientes(limite=5)
        lineas = ["Estado de las alertas:", ""]
        for estado in ("pendiente", "enviada", "resuelta", "descartada"):
            lineas.append("  %s: %d" % (estado, conteo.get(estado, 0)))

        #
        # Con más de un familiar, el estado de un aviso tiene una parte que antes no
        # existía: cuántas copias salieron y cuántas no. "Enviada" con 1 de 2 es
        # información distinta de "enviada" con 2 de 2, y es justo el caso que hace
        # falta cuando un móvil lleva sin cobertura: el familiar ve que le llegó,
        # el otro no, y `/alertas` lo dice en las dos casas.
        #
        if self.base.contar_familiares() > 1:
            lineas.append("")
            lineas.append("Hay %d personas registradas." % self.base.contar_familiares())

        if recientes:
            lineas.append("")
            lineas.append("Ultimas cinco:")
            for fila in recientes:
                destinos = ""
                if fila["enviados"] or fila["sin_mandar"]:
                    destinos = "  (%d de %d)" % (fila["enviados"],
                                                 fila["enviados"] + fila["sin_mandar"])
                lineas.append("  %s  %s  %s%s" % (
                    time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(fila["creado_en"])),
                    fila["tipo"], fila["estado"], destinos))
        else:
            lineas.append("")
            lineas.append("No hay ninguna alerta todavia.")
        await update.effective_message.reply_text("\n".join(lineas))

    # --- Botón "Ya lo atendí" (P17) -----------------------------------------

    async def cmd_atendi(self, update: Update,
                         contexto: ContextTypes.DEFAULT_TYPE) -> None:
        """Marca la alerta como resuelta, edita el mensaje y avisa al asistente.

        El orden importa. Primero se marca como resuelta en la base, porque es lo
        que impide que la cola la vuelva a mandar; después se edita el mensaje, que
        es lo que el familiar ve; y por último se publica en `satelite/decir`, que
        es lo que le dice al sitio que ya hay alguien con el aviso.

        La edición va por `Remitente.editar_futuro()`, no por `editar()`. El
        manejador corre dentro del bucle de eventos del bot, y `editar()` creaba
        uno nuevo con `asyncio.run`, que en un hilo con un bucle ya corriendo falla
        con «asyncio.run() cannot be called from a running event loop». Con
        `editar_futuro` la edición viaja al hilo de Telegram y el manejador la
        espera con `await`, sin bloquear el bot entero mientras tanto.

        El `callback_query` se responde UNA vez. Telegram admite una sola respuesta
        a cada pulsación: la versión anterior llamaba a `answer()` al principio y
        otra vez al final con un texto, y la segunda no era fiable (Telegram la
        ignora o devuelve «query is too old»). Además, si lo que va a contestarse es
        un rechazo, responder «ok» y luego un aviso rojo es peor que no decir nada:
        aquí se decide primero qué se va a decir y se responde una sola vez.
        """
        consulta = update.callback_query

        id_alerta = (consulta.data or "")[len(PREFIJO_CODIGO):]
        if not id_alerta:
            await consulta.answer("Este boton no trae la alerta.", show_alert=True)
            return

        if not await self._es_privado(update, avisar=False):
            await consulta.answer("Este bot solo funciona en un chat privado.",
                                  show_alert=True)
            return

        # Que el que pulsó sea un familiar registrado. Un bot de la casa no va a
        # resolver alertas desde un grupo ni desde una cuenta que no está en la
        # lista, aunque el mensaje le haya llegado: alguien a quien se le dio de
        # baja puede tener el aviso viejo abierto en el móvil, y su pulsación ya no
        # cuenta.
        #
        # El chat sale de `update.effective_chat`, NO de `consulta.effective_chat`.
        # `effective_chat` es una propiedad de `Update`, que sabe sacarlo de un
        # callback query mirando el mensaje; el objeto `CallbackQuery` no la tiene,
        # y usarla directamente tumba el manejador con «'CallbackQuery' object has no
        # attribute 'effective_chat'». Es lo que pasaba al pulsar el botón: la alerta
        # se quedaba sin resolver y el familiar no veía ningún cambio en el chat.
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None or not self.base.familiar_registrado(chat_id):
            registro.warning("el boton 'Ya lo atendi' de la alerta %s se pulso desde "
                             "un chat que no esta registrado; se ignora", id_alerta)
            await consulta.answer("Este bot solo acepta el aviso de los familiares "
                                  "registrados.", show_alert=True)
            return
        chat_id = str(chat_id)

        ahora = int(time.time())
        fresca = self.base.marcar_resuelta(id_alerta, ahora)
        fila = self.base.obtener_alerta(id_alerta)
        tipo = fila["tipo"] if fila is not None else "alerta"

        texto = (
            "ATENDIDO.\n\n"
            "La alerta de %s se marco como atendida a las %s.\n\n"
            "Id de la alerta: %s"
            % (tipo, time.strftime("%H:%M:%S", time.localtime(ahora)), id_alerta)
        )

        # La edicion va al chat de quien pulso, que es el único sitio donde esta el
        # mensaje. Con dos familiares, editar en un chat por defecto dejaria al que
        # pulso viendo el aviso viejo con el boton, y lo dejaria pulsando otra vez.
        try:
            await self.remitente.editar_futuro(consulta.message, texto,
                                              teclado=None, chat_id=chat_id)
        except Exception as error:  # noqa: BLE001
            # Si el mensaje es demasiado viejo para editarlo, la alerta queda
            # resuelta igual en la base y el familiar tiene la respuesta por
            # `answer()`. Perder la edición es una molestia; perder el estado
            # resuelto, no.
            registro.error("no se pudo editar el mensaje de la alerta %s: %s",
                           id_alerta, telegram_cliente.sanear(error))
            await consulta.answer(
                "Anotado. El mensaje viejo no se pudo editar.", show_alert=True
            )
        else:
            await consulta.answer(
                "Ya esta attendida." if fresca else "Esta alerta ya estaba atendida."
            )

        self._avisar_al_satelite(tipo, ahora)

        registro.warning("alerta %s (%s) marcada como resuelta por el boton 'Ya lo "
                         "atendi' del familiar %s%s",
                         id_alerta, tipo, db_modulo.enmascarar_chat(chat_id),
                         "" if fresca else " (ya estaba resuelta)")

    def _avisar_al_satelite(self, tipo: str, ahora: int) -> None:
        """Publica en `casa/sala/satelite/decir` que alguien ya atendió la alerta.

        Es lo que pide el P17: el sitio tiene que enterarse de que ya hay alguien
        con el aviso, para que no siga llamando. El `ts` va como entero epoch, que
        es lo que exige la regla de antigüedad del contrato, y el mensaje va SIN
        retención, porque es un hecho que pasó en un momento.

        Si la publicación falla se registra y no se lanza: el pulsador ya resolvió
        la alerta, y no se puede deshacer por no haber podido avisar al satélite.
        """
        if self.publicar is None:
            registro.info("no hay cliente MQTT en el bot; no se avisa a satelite/decir")
            return
        texto = ("Ya atendiste la alerta de %s." % tipo)
        try:
            self.publicar(
                config.TOPICO_SATELITE_DECIR,
                {"texto": texto, "tipo": "aviso", "ts": ahora},
                qos=1, retain=False,
            )
            registro.info("publicado en satelite/decir: %s", texto)
        except Exception as error:  # noqa: BLE001
            registro.error("no se pudo publicar en satelite/decir: %s",
                           telegram_cliente.sanear(error))

    # --- Utilidades --------------------------------------------------------

    async def _error(self, update: object, contexto: ContextTypes.DEFAULT_TYPE) -> None:
        """Lo que hace el bot cuando un comando revienta.

        Sin esto, `python-telegram-bot` escribe «No error handlers are registered,
        logging exception» y su propia traza, sin pasar por `sanear()`. Dos
        problemas: el texto de la librería puede incluir la URL de la API, que lleva
        el token del bot, y no dice qué comando falló ni desde qué chat.

        Además contesta al familiar, cuando hay algo que contestar. Un comando que
        falla en silencio deja a la persona pensando que el sistema no oyó nada, que
        es el peor sitio para quedarse un aviso de alarma.
        """
        error = getattr(contexto, "error", None)
        registro.exception("fallo en un comando del bot: %s",
                           telegram_cliente.sanear(error) if error else "sin detalle")
        consulta = getattr(update, "callback_query", None)
        if consulta is not None:
            try:
                await consulta.answer("Algo fallo. Intentalo de nuevo.",
                                      show_alert=True)
            except telegram_cliente.TelegramError:
                pass
            return
        mensaje = getattr(update, "effective_message", None)
        if mensaje is not None:
            try:
                await mensaje.reply_text("Algo fallo al procesar el comando. "
                                         "Intentalo de nuevo.")
            except telegram_cliente.TelegramError:
                pass

    async def _es_privado(self, update: Update, avisar: bool = True) -> bool:
        """Rechaza todo lo que no sea un chat privado.

        Devuelve `False` y avisa. Un bot de la casa en un grupo es un problema de
        privacidad, no una molestia: publicaría el número de la persona.

        `avisar=False` es para el botón del bot, que ya responde con
        `callback_query.answer()`. Escribir además un mensaje en el grupo del que
        vino la pulsación sería gastar un mensaje del grupo para explicar que no se
        puede usar el bot ahí.
        """
        if update.effective_chat is None:
            return False
        if update.effective_chat.type != "private":
            if avisar and update.effective_message is not None:
                await update.effective_message.reply_text(
                    "Este comando solo funciona en un chat privado."
                )
            return False
        return True