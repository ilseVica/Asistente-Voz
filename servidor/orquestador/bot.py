"""Los comandos y los botones del bot de Telegram.

Abre el bot de comandos en el chat privado de la familia. Hay dos comandos y un
botón:

- `/registro <codigo>`: da de alta el chat al que se mandan las alertas.
- `/alertas`: cuántas hay en la cola, cuántas se han mandado y cuáles son las
  últimas cinco.
- Botón **«Ya lo atendí»**: marca la alerta como resuelta, edita el mensaje de
  Telegram para que el familiar vea uno solo con la hora, y publica en
  `casa/sala/satelite/decir` el aviso de que alguien ya está con el aviso (P17).

Enviar las alertas vive en `telegram_cliente.py`. La separación es por ritmo: esto
es un bucle de eventos de Telegram que corre en su propio hilo, y lo otro es una
llamada de red que hace el trabajador de la cola.

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

## Decisiones de seguridad

**Solo chat privado.** En un grupo, el bot publicaría el número de teléfono de la
persona en un canal donde lo pueden leer mil personas. `_es_privado()` descarta
todo lo que no sea un chat de dos.

**`/registro` solo acepta un código de un solo uso.** El código lo genera el
operador con un comando del contenedor y vale 10 minutos. Sin eso, cualquier
persona que encontrara el bot se podría dar de alta y cambiar el destino de los
avisos de gas de una persona mayor.

**`/registro` necesita un código de un solo uso, y el silencio es para el chat que
ya está registrado.** Es lo que evita que cualquiera que escriba al bot pueda
cambiar el destino de las alertas de gas. Y al chat ya registrado no se le
responde nada, ni siquiera «ya estás registrado»: confirmar que el bot existe ya
le dice a cualquiera que hable con él que este es el bot de la casa.

Ese silencio es **solo para el chat registrado**. Un chat distinto sí puede
registrarse, con un código nuevo, y pasa a ser el destino de las alertas: en una
casa pueden ser dos los que llevan el teléfono. Antes el silencio era global y un
segundo familiar no se podía registrar nunca sin borrar `familia.json` a mano.

Por el mismo motivo, `/alertas` tampoco responde a un chat que no sea el
registrado. Es una lectura del historial de la casa, y responder confirmaría que
el bot existe y qué ha pasado en ella.

El chat se guarda en disco (en `familia.json`, vía `registro.py`) y no solo en
memoria, para que un reinicio del servicio no deje las alertas sin destino hasta
que alguien escriba `/registro` otra vez. Y en un reinicio no hay nadie escribiendo.
"""

import logging
import threading
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes)

import config
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
    def __init__(self, base, remitente, familia=None, publicar=None):
        self.base = base
        self.remitente = remitente
        self.familia = familia
        self.publicar = publicar
        self.registrado = familia.esta_registrado() if familia else bool(
            remitente.chat_id
        )
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
            # mensajes de más de una sesión, y aqui todos son de consulta o de alta:
            # `/registro` es silencioso si ya está registrado y `/alertas` solo lee.
            self.aplicacion.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                stop_signals=None,
                close_loop=False,
            )
        except Exception as error:  # noqa: BLE001
            registro.exception("el bot de comandos no pudo arrancar: %s",
                               telegram_cliente.sanear(error))

    # --- /registro ---------------------------------------------------------

    # --- Registro de familiares ---------------------------------------------

    def _chat_registrado(self) -> str:
        """El `chat_id` que hay ahora mismo como destino de las alertas."""
        if self.familia is not None and self.familia.esta_registrado():
            return str(self.familia.datos.get("chat_id") or "")
        return str(self.remitente.chat_id or "")

    def _es_el_registrado(self, update: Update) -> bool:
        """¿Viene este comando del chat registrado?

        `self.registrado` a secas no sirve: es un booleano, y dice «hay alguien
        registrado», no «es esta persona». Con solo el booleano, cualquier chat
        privado que hallara el bot podría leer el historial de alertas de la casa.
        """
        registrado = self._chat_registrado()
        if not registrado:
            return False
        return str(update.effective_chat.id) == registrado

    async def cmd_registro(self, update: Update,
                           contexto: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._es_privado(update):
            return

        chat_id = str(update.effective_chat.id)

        # El silencio NO es para todo el mundo, es para el chat que ya esta
        # registrado. Ver el comentario de la clase: responder «ya estas
        # registrado» confirma que este es el bot de la casa, y eso solo se le
        # dice a quien ya esta dentro.
        #
        # Para un chat DISTINTO al registrado, `/registro` exige un codigo nuevo y
        # si lo tiene, cambia el destino de las alertas. Antes el silencio era
        # global y un segundo familiar no se podia registrar nunca: la casa se
        # quedaba con un unico destino para siempre, y cambiarlo exigia borrar
        # `familia.json` a mano desde el contenedor.
        if self.registrado and chat_id == self._chat_registrado():
            return

        otro_destino = ""
        if self.registrado:
            otro_destino = (
                "Aviso: las alertas dejan de llegar al chat %s y pasan a este.\n\n"
                % self._chat_registrado()
            )

        argumentos = contexto.args or []
        if not argumentos:
            await update.effective_message.reply_text(
                "Falta el codigo.\n\n"
                "Escribe:\n"
                "/registro CODIGO\n\n"
                "El codigo te lo da quien instalo el asistente."
            )
            return

        codigo = argumentos[0].strip()
        motivo = registro_modulo.comprobar_codigo(codigo)
        if motivo is not None:
            registro.warning("/registro con un codigo que no sirve (%s)", motivo)
            await update.effective_message.reply_text(
                "No se pudo registrar: %s.\n\nPide un codigo nuevo." % motivo
            )
            return

        nombre = " ".join(argumentos[1:]).strip()
        self.registrado = True
        self.remitente.chat_id = chat_id
        self.remitente.disponible = bool(self.remitente.token)
        registro_modulo.marcar_codigo_usado(codigo)
        if self.familia is not None:
            try:
                self.familia.registrar(chat_id, nombre=nombre)
            except OSError as error:
                # Sin el archivo, el registro se pierde en el próximo reinicio.
                # Se avisa, pero el chat de esta sesión sigue valiendo.
                registro.error("el chat %s se registro en memoria pero no en "
                               "disco: %s", chat_id, error)
        registro.warning("chat %s registrado por /registro con un codigo de un "
                         "solo uso%s", chat_id,
                         " (cambia el destino anterior)" if otro_destino else "")
        await update.effective_message.reply_text(
            "Listo. Desde ahora te aviso aqui si hay una alerta de gas o una "
            "peticion de ayuda.\n\n"
            + otro_destino
            + "Comando: /alertas, para ver el estado."
        )

    # --- /alertas ----------------------------------------------------------

    async def cmd_alertas(self, update: Update,
                          contexto: ContextTypes.DEFAULT_TYPE) -> None:
        """El estado de las alertas, para el chat registrado.

        Es una LECTURA del historial de la casa: qué hay pendiente, qué se mandó,
        qué se resolvió, y las últimas cinco con su hora. No se puede filtrar por
        chat porque solo hay una familia destinataria, así que cualquier chat
        privado que hable con el bot vería el mismo historial.

        Por eso, y a diferencia de `/registro`, aquí no se responde a un chat que
        no sea el registrado: se queda en silencio. Si `/alertas` contestara a
        cualquiera, bastaría con escribir `/alertas` a un bot sospechoso para que
        confirmara que existe, que recibe alertas y cuándo, que es justo lo que el
        silencio de `/registro` evita.
        """
        if not await self._es_privado(update):
            return
        if not self._es_el_registrado(update):
            return
        conteo = self.base.contar_por_estado()
        recientes = self.base.listar_recientes(limite=5)
        lineas = ["Estado de las alertas:", ""]
        for estado in ("pendiente", "enviada", "resuelta", "descartada"):
            lineas.append("  %s: %d" % (estado, conteo.get(estado, 0)))
        if recientes:
            lineas.append("")
            lineas.append("Ultimas cinco:")
            for fila in recientes:
                lineas.append("  %s  %s  %s" % (
                    time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(fila["creado_en"])),
                    fila["tipo"], fila["estado"]))
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

        # Que el que pulsó sea el chat registrado. Un bot de la casa no va a
        # resolver alertas desde un grupo o desde una cuenta que no es la familia.
        #
        # El chat sale de `update.effective_chat`, NO de `consulta.effective_chat`.
        # `effective_chat` es una propiedad de `Update`, que sabe sacarlo de un
        # callback query mirando el mensaje; el objeto `CallbackQuery` no la tiene,
        # y usarla directamente tumba el manejador con «'CallbackQuery' object has no
        # attribute 'effective_chat'». Es lo que pasaba al pulsar el botón: la alerta
        # se quedaba sin resolver y el familiar no veía ningún cambio en el chat.
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None or str(chat_id) != str(self.remitente.chat_id):
            registro.warning("el boton 'Ya lo atendi' de la alerta %s se pulso desde "
                             "un chat que no es el registrado; se ignora", id_alerta)
            await consulta.answer("Este bot solo acepta el aviso de quien esta "
                                  "registrado.", show_alert=True)
            return

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

        try:
            await self.remitente.editar_futuro(consulta.message, texto, teclado=None)
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
                         "atendi' del chat %s%s",
                         id_alerta, tipo, chat_id,
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