"""Los comandos y los botones del bot de Telegram.

Abre el bot de comandos en el chat privado de la familia. Hay tres comandos y un
botón:

- `/registro <código>`: da de alta ese chat en la lista de quienes reciben las
  alertas. Los que ya estaban siguen estándolo.
- `/alertas`: cuántas hay en la cola, cuántas se han mandado y cuáles son las
  últimas cinco. Contesta a cualquier familiar registrado.
- `/panel`: un código de un solo uso al panel web. Solo para un familiar activo,
  y solo si `PANEL_URL` está puesta.
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

import html
import logging
import threading
import time

from telegram import (CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup,
                      Update)
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes)

import alertas as alertas_modulo
import config
import db as db_modulo
import panel_acceso
import registro as registro_modulo
import telegram_cliente
import textos

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
            InlineKeyboardButton(textos.BOTON_ABRIR_PANEL, url=config.PANEL_URL)
        )
    botones.append(
        InlineKeyboardButton(textos.BOTON_ATENDI,
                             callback_data=PREFIJO_CODIGO + id_alerta)
    )
    return InlineKeyboardMarkup([[boton] for boton in botones])


def texto_pedir_registro(base) -> str:
    """El texto que contesta a un chat que no está registrado.

    Lo usan `/registro` sin código **y** `/panel`, y tienen que ser el MISMO texto,
    por eso está aquí y no escrito dos veces.

    Es lo que hace que `/panel` no revele nada nuevo: si un chat que no está
    registrado escribiera `/panel` y recibiera «para el panel necesitas estar
    registrado», sabría que esta casa tiene un panel; con este texto, que es el
    mismo que ya recibe por `/registro`, no aprende nada que no supiera. Escribir
    la respuesta «de no registrado» dos veces, una para cada comando, es la forma
    de que un día una diga «panel» y la otra no, y esa diferencia es medio punto de
    entrada a lo que sea que haya detrás.
    """
    return textos.pedir_registro(base.contar_familiares())


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
                CommandHandler("panel", self.cmd_panel)
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
            await update.effective_message.reply_text(
                texto_pedir_registro(self.base)
            )
            return

        codigo = argumentos[0].strip()
        motivo = registro_modulo.comprobar_codigo(codigo)
        if motivo is not None:
            registro.warning("/registro desde el familiar %s con un codigo que no "
                             "sirve (%s)", quien, motivo)
            await update.effective_message.reply_text(
                textos.registro_fallido(motivo)
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

        respuesta = textos.registro_listo(
            atrasados, nuevo and self.base.contar_familiares() > 1)
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
        lineas = [textos.ALERTAS_TITULO, ""]
        for estado, etiqueta in textos.ALERTAS_ESTADOS:
            lineas.append("%s: %d" % (etiqueta, conteo.get(estado, 0)))

        #
        # Con más de un familiar, el estado de un aviso tiene una parte que antes no
        # existía: cuántas copias salieron y cuántas no. "Enviada" a 1 de 2 es
        # información distinta de "enviada" a las 2, y es justo el caso que hace
        # falta cuando un móvil lleva sin cobertura: el familiar ve que le llegó,
        # el otro no, y `/alertas` lo dice en las dos casas (`textos.destinos`).
        #
        lineas.append("")
        lineas.append(textos.personas_registradas(self.base.contar_familiares()))

        lineas.append("")
        if recientes:
            lineas.append(textos.ALERTAS_ULTIMAS)
            for fila in recientes:
                lineas.append(textos.linea_reciente(
                    fila["creado_en"], fila["tipo"], fila["estado"],
                    fila["enviados"], fila["enviados"] + fila["sin_mandar"]))
        else:
            lineas.append(textos.ALERTAS_NINGUNA)
        await update.effective_message.reply_text("\n".join(lineas))

    # --- /panel: el código del panel web ------------------------------------

    async def cmd_panel(self, update: Update,
                        contexto: ContextTypes.DEFAULT_TYPE) -> None:
        """Manda un código de acceso de un solo uso al panel web.

        El código tiene 6 caracteres, vale 10 minutos y se gasta el primero que lo
        canjea. Se manda como HTML con parse_mode=ParseMode.HTML: el código va en
        <code>, que Telegram pinta en monoespaciado, y el teclado lleva un botón
        «Copiar código» (copy_text, que lo pone en el portapapeles con un toque) y
        otro «Abrir panel» con la URL. El código no va dentro de la URL.

        El único texto que lleva marcas HTML es el de textos.PANEL_CODIGO, que es de
        este paquete; el código y la URL se pasan por html.escape antes de meterlos
        en la plantilla. Si algún día se cambia el texto, hay que acordarse de que
        .format() no escapa nada.

        Tres rechazos antes de generar nada, y el orden importa:

        1. **No es un chat privado.** En un grupo saldría el número de la persona.
        2. **No está registrado o está desactivado.** Contesta EXACTAMENTE lo mismo que
           `/registro` sin código, sin decir que el panel existe. `/registro` ya le
           responde ese texto a cualquier chat privado, así que aquí no se revela nada
           nuevo; y un bot de la casa que dice «para el panel hay que estar registrado»
           a cualquiera que lo encuentre ya es medio punto de entrada. Se pide
           `familiar_activo` y no `familiar_registrado`: un familiar dado de baja sigue
           en la base para el historial, pero no puede pedir código.
        3. **`PANEL_URL` está vacía.** No se manda un código que no lleva a ningún
           sitio. Un panel que no existe y un bot que manda códigos para él hace que
           el familiar desconfíe del botón de la próxima alerta de gas, que es el
           momento exacto en que un botón falso se paga caro.

        Lo que sale del log: el familiar, enmascarado, y que se ha creado un código.
        Nunca el código. `docker logs` lo lee cualquiera que tenga acceso al
        servidor, y un código en claro ahí es una entrada al panel de la familia.
        """
        if not await self._es_privado(update):
            return

        chat_id = str(update.effective_chat.id)
        quien = db_modulo.enmascarar_chat(chat_id)

        if not self.base.familiar_activo(chat_id):
            registro.warning("/panel desde el chat %s, que no esta activo; no se "
                             "le manda ningun codigo", quien)
            await update.effective_message.reply_text(
                texto_pedir_registro(self.base)
            )
            return

        if not config.PANEL_URL:
            registro.warning("/panel sin PANEL_URL en el entorno: no se manda ningun "
                             "codigo. El panel todavia no esta desplegado.")
            await update.effective_message.reply_text(textos.PANEL_NO_DISPONIBLE)
            return

        ahora = int(time.time())
        codigo = panel_acceso.crear_codigo_acceso(self.base, chat_id, ahora)
        registro.warning("codigo del panel creado para el familiar %s; vale hasta "
                         "las %s", quien,
                         time.strftime("%H:%M:%S",
                                       time.localtime(ahora + registro_modulo.SEGUNDOS_CODIGO)))
        # El codigo y la URL se escapan antes de meterlos en la plantilla; el texto
        # de textos.PANEL_CODIGO ya esta pensado como HTML.
        mensaje = textos.PANEL_CODIGO.format(codigo=html.escape(codigo),
                                             url=html.escape(config.PANEL_URL))
        teclado = InlineKeyboardMarkup([
            [InlineKeyboardButton(textos.PANEL_BOTON_COPIAR,
                                  copy_text=CopyTextButton(text=codigo))],
            [InlineKeyboardButton(textos.PANEL_BOTON_ABRIR,
                                  url=config.PANEL_URL)],
        ])
        await update.effective_message.reply_text(
            mensaje, parse_mode=ParseMode.HTML, reply_markup=teclado,
        )

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
            await consulta.answer(textos.BOTON_SIN_ALERTA, show_alert=True)
            return

        if not await self._es_privado(update, avisar=False):
            await consulta.answer(textos.SOLO_CHAT_PRIVADO, show_alert=True)
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
            await consulta.answer(textos.SOLO_REGISTRADOS, show_alert=True)
            return
        chat_id = str(chat_id)

        ahora = int(time.time())
        # La resolución va por `alertas.resolver()`, la MISMA función que usa el
        # panel web. Ver su comentario: avisa al satélite solo si la alerta estaba
        # sin resolver, que es lo que el README de este archivo dice desde hace
        # tiempo y lo que el código no cumplía.
        fila, fresca = alertas_modulo.resolver(
            self.base, self.publicar, id_alerta, ahora,
            quien="el familiar %s" % db_modulo.enmascarar_chat(chat_id),
            origen="el boton del bot",
            # El NOMBRE va aparte y queda en `alertas.resuelto_por`, para la
            # linea de tiempo del panel. Nunca el chat_id: un número no le
            # significa nada a nadie en una pantalla de familia.
            nombre_quien=self.base.nombre_de(chat_id),
        )
        tipo = fila["tipo"] if fila is not None else "alerta"

        # La hora que sale en el texto es `resuelto_en`, la de cuando se atendio, y
        # no la de ahora: si este familiar pulsa el boton DESPUES de que la hubiera
        # atendido otro desde el panel, el mensaje tiene que decir la hora real de
        # atencion, que es la que ya estaba en la base.
        cuando = (fila["resuelto_en"] if fila is not None and fila["resuelto_en"]
                  else ahora)
        texto = alertas_modulo.texto_atendido(tipo, cuando, id_alerta)

        # La edicion va al chat de quien pulso, que es el único sitio donde esta el
        # mensaje. Con dos familiares, editar en un chat por defecto dejaria al que
        # pulso viendo el aviso viejo con el boton, y lo dejaria pulsando otra vez.
        #
        # `marcar_editado` se llama en los DOS caminos, tambien si la edicion
        # fallo: lo que hace es decirle a `cola._editar_resueltas` que este aviso ya
        # tiene dueño y no lo reescriba el, y una fila que no se pudo editar no va
        # a poderse editar dentro de cinco segundos (ver `db.marcar_editado`).
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
            self.base.marcar_editado(id_alerta, chat_id, ahora)
            await consulta.answer(textos.ANOTADO_SIN_EDITAR, show_alert=True)
        else:
            self.base.marcar_editado(id_alerta, chat_id, ahora)
            await consulta.answer(
                textos.YA_ATENDIDA if fresca else textos.YA_ESTABA_ATENDIDA
            )

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
                await consulta.answer(textos.ERROR_BOTON, show_alert=True)
            except telegram_cliente.TelegramError:
                pass
            return
        mensaje = getattr(update, "effective_message", None)
        if mensaje is not None:
            try:
                await mensaje.reply_text(textos.ERROR_COMANDO)
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
                    textos.SOLO_CHAT_PRIVADO
                )
            return False
        return True