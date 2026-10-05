"""Mandar las alertas a Telegram.

Tres cosas: mandar el texto de una alerta a un familiar, editar ese mensaje cuando
la familia pulsa «Ya lo atendí», y no filtrar nunca el token. Los comandos del bot
(`/registro` y `/alertas`) están en `bot.py`.

**El destino lo pasa quien llama.** Esta clase no sabe cuantos familiares hay ni
cuales son. Antes guardaba un `chat_id` fijo, y con dos familiares el mismo objeto
tenía que mandar a dos conversaciones: el destino es ahora un argumento de `enviar`
y de `editar_futuro`, y el que lo decide es el trabajador de la cola, que es quien
tiene la fila del envío y sabe a quién toca.

**El token no sale de aquí.** `python-telegram-bot` llama a la API por HTTPS y en
algunos errores el texto que devuelve httpx incluye la URL completa, que lleva el
token en claro. Antes de escribir cualquier error en el log o en la base pasa por
`sanear()`. Sin eso, un solo `NetworkError` basta para dejar el token del bot
escrito en `docker logs`, que es lo primero que se lee cuando algo falla y lo
primero que se pega en un chat.

Puede funcionar sin token, y esa es la diferencia entre un fallo molesto y una
caída del servicio:

- `Remitente.enviar()` sin token lanza `TelegramNoConfigurado`, una excepción
  propia. Si lanzara un error normal, el trabajador de la cola entraría en su
  camino de reintentos y consumiría intentos por algo que no es un fallo de envío.
  Con la excepción propia, el trabajador sabe que la alerta debe esperar sin
  gastar nada, porque lo que falta es el token, no la red.
- `ClienteBot.arancar()` sin token registra un aviso y no llama a `run_polling()`.
  El resto del servicio sigue funcionando: MQTT, la base, la deduplicación.

El usuario confirmó este comportamiento: sin token o con token inválido, el
servicio arranca igual.

## Un solo bucle de eventos para las dos cosas

`python-telegram-bot` es asíncrono, y en este proceso hay dos cosas que necesitan
un bucle: el trabajador de la cola, que es un hilo normal, y el bot de comandos, que
vive en el bucle de `run_polling`. La versión anterior resolvía los dos con
`asyncio.run(...)` en cada llamada, y eso estaba mal por dos motivos:

1. `asyncio.run` desde dentro del manejador del botón creaba un bucle nuevo
   mientras el bucle del bot estaba corriendo en ese mismo hilo. El resultado es
   `RuntimeError: asyncio.run() cannot be called from a running event loop`, y el
   botón «Ya lo atendí» no editaba el mensaje: la alerta quedaba resuelta en la base
   y el familiar no veía nada.
2. El `httpx.AsyncClient` de la librería queda atado al bucle en el que se usó. Si
   dos hilos lo comparten, uno de los dos falla con errores de bucle cerrado o de
   futures de otro bucle, de forma intermitente y solo cuando coinciden las dos
   cosas: el peor tipo de fallo, porque pasa en la demostración y no en la prueba.

Aquí hay un solo bucle, en un solo hilo, y un solo `Bot`. Los dos llaman al mismo
sitio: el trabajador con `enviar()`, que bloquea su hilo esperando el resultado, y
el bot con `editar_futuro()`, que devuelve un future para esperar con `await` sin
bloquear el bucle de Telegram. Un `Bot` y un cliente HTTP para las dos cosas, y
ningún `asyncio.run` en ninguna parte.
"""

import asyncio
import logging
import threading

from telegram import Bot
from telegram.error import TelegramError

import config

registro = logging.getLogger("telegram")

# Cuánto se espera a que responda Telegram antes de darse por vencido. Es alto a
# propósito: una alerta de gas que se da por perdida y se reintenta dentro de tres
# minutos es peor que una que tarda un poco más. Telegram responde en menos de un
# segundo casi siempre; lo que se hace aquí es principally el `read timeout` del
# cliente, no una espera artificial.
TIMEOUT_SEGUNDOS = 20.0


def sanear(texto: str) -> str:
    """Quita el token de Telegram de un texto antes de escribirlo.

    Sustituye el valor de `TELEGRAM_BOT_TOKEN` por `***`. Es la única defensa: el
    token permite leer y escribir como el bot, así que un token en un log es un
    token filtrado, y los logs se copian, se pegan y se comparten.

    Se hace una sustitución literal y no una expresión regular. El token es una
    cadena fija y conocida, así que buscar el texto exacto no puede fallar ni
    tener falsos positivos con algo que se le parezca.

    Si el token está vacío la función devuelve el texto tal cual: sin token no hay
    nada que filtrar, y así una prueba puede usarla sin inventar un token falso.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        return texto
    return str(texto).replace(config.TELEGRAM_BOT_TOKEN, "***")


class TelegramNoConfigurado(Exception):
    """Telegram no esta disponible porque falta el token o el chat.

    Es una excepcion aparte, y no un `TelegramError` de los de la libreria, por
    una razón concreta: NO es un fallo de red y no se debe reintentar con espera
    creciente. Si el token no está, las alertas que ya están en la cola tienen que
    seguir esperando, sin consumir intentos, hasta que alguien ponga el token.

    Si esto fuera un error normal, el trabajador gastaría los cinco reintentos,
    marcaría la alerta como descartada y se perdería. Y una alerta de gas perdida
    porque el token faltaba es el peor fallo posible en este sistema.
    """


class Remitente:
    """Manda los textos de alerta a un familiar.

    No guarda ningun chat: el destino lo dice quien llama. Antes esta clase tenia
    un `chat_id` fijo y el único chat de la casa, y con dos familiares eso ya no
    servia: el mismo remitente tiene que poder mandar el mismo texto a dos
    conversations distintas, y decidir cual es el que se le pasa es cosa del
    trabajador de la cola, que es quien tiene la fila del envio.

    `disponible` es solo "¿hay token?". Que haya un familiar o no ya no lo decide
    esta clase: lo decide la base, y un envio sin destino se trata aparte (ver
    `cola.TrabajadorTelegram`), porque no es lo mismo que no poder mandar por
    falta de token.
    """

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.bot = None
        self.disponible = bool(self.token)
        self._bucle = None
        self._hilo = None

        if not self.token:
            registro.error("sin TELEGRAM_BOT_TOKEN: las alertas se encolan pero "
                           "no se envian. El servicio sigue funcionando.")

        if self.token:
            self.bot = Bot(token=self.token)
            self._arrancar_bucle()

    # --- El bucle de eventos ------------------------------------------------

    def _arrancar_bucle(self) -> None:
        """Levanta el hilo con el bucle donde vive el único `Bot`.

        Es un hilo aparte y no `asyncio.new_event_loop()` en el hilo actual, porque
        el hilo actual es el del servicio, que además tiene que seguir recibiendo de
        MQTT mientras Telegram responde.
        """
        self._bucle = asyncio.new_event_loop()
        self._hilo = threading.Thread(
            target=self._bucle.run_forever, name="telegram", daemon=True
        )
        self._hilo.start()
        registro.debug("hilo de Telegram arrancado")

    def cerrar(self) -> None:
        """Para el bucle y espera a que el hilo termine.

        Lo llama el servicio al apagarse. Sin esto, el hilo se queda vivo hasta que
        el proceso muere, y en un reinicio del contenedor eso deja un aviso a medio
        enviar y un `Bot` sin cerrar.
        """
        if self._bucle is None:
            return
        try:
            self._bucle.call_soon_threadsafe(self._bucle.stop)
            if self._hilo is not None:
                self._hilo.join(timeout=5)
            self._bucle.close()
        except Exception as error:  # noqa: BLE001
            registro.debug("al cerrar el bucle de Telegram: %s", sanear(error))
        finally:
            self._bucle = None
            self._hilo = None

    def _ejecutar(self, corrutina, timeout: float = TIMEOUT_SEGUNDOS):
        """Pone una corrutina en el bucle y espera el resultado. Para otros hilos."""
        if self._bucle is None or self.bot is None:
            corrutina.close()
            raise TelegramNoConfigurado("no hay token de Telegram")
        futuro = asyncio.run_coroutine_threadsafe(corrutina, self._bucle)
        return futuro.result(timeout)

    def _futuro(self, corrutina):
        """Pone una corrutina en el bucle y devuelve un future para otro bucle.

        `editar_futuro()` es la versión para quien ya está dentro del bucle de
        Telegram: el manejador hace `await asyncio.wrap_future(...)` y sigue
        funcionando mientras la edición viaja. El hilo del manejador NO se bloquea,
        que es lo que bloqueaba la versión anterior con `asyncio.run`.
        """
        if self._bucle is None or self.bot is None:
            corrutina.close()
            raise TelegramNoConfigurado("no hay token de Telegram")
        return asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(corrutina, self._bucle)
        )

    def enviar(self, texto: str, teclado=None, chat_id: str = None) -> object:
        """Manda un mensaje a UN familiar. Lanza excepción si no se pudo.

        `chat_id` es obligatorio en la práctica: no hay un chat por defecto, porque
        hay varios y adivinar uno sería mandar un aviso de gas al movil equivocado.

        Devuelve el `Message` de Telegram, que es lo que permite editar el mismo
        mensaje después en vez de mandar otro. Editar y no mandar es lo que hace
        que el familiar vea un solo aviso en el chat en lugar de tres.

        Esta función sí propaga el error: es el trabajador de la cola quien decide
        si reintenta, cuánto espera y cuándo vuelve a mirar. Si `enviar` se tragara
        los fallos, el envío se marcaría como hecho sin haber salido, y eso es
        exactamente el fallo que no puede pasar en una alerta de gas.

        Lanza `TelegramNoConfigurado` si falta el token o el chat, y un
        `TelegramError` de la librería si el envío falló de verdad. El trabajador
        trata las dos cosas de forma distinta, por lo que dice el comentario de
        arriba.
        """
        if not self.disponible or self.bot is None:
            raise TelegramNoConfigurado(
                "falta TELEGRAM_BOT_TOKEN; el envio sigue en la cola sin consumir "
                "intentos"
            )
        if not chat_id:
            raise TelegramNoConfigurado(
                "el envio no tiene familiar de destino; sigue en la cola sin "
                "consumir intentos"
            )

        mensaje = self._ejecutar(self._enviar_async(texto, teclado, str(chat_id)))
        if not mensaje:
            raise TelegramError("Telegram acepto el envio pero no lo confirmo")
        return mensaje

    async def _enviar_async(self, texto: str, teclado=None, chat_id: str = None):
        # No hay `except` aquí a proposito: cualquier error de Telegram sube
        # hasta `enviar`, que lo deja pasar, y de ahí al trabajador, que decide si
        # reintenta. Envuelto en un `try/except TelegramError: raise` sería lo
        # mismo y ademas mas código.
        return await self.bot.send_message(
            chat_id=chat_id,
            text=texto,
            parse_mode=None,  # texto plano: ver alertas.texto_para_telegram
            disable_web_page_preview=True,
            reply_markup=teclado,
        )

    def editar(self, mensaje, texto: str, teclado=None,
               chat_id: str = None) -> None:
        """Edita un mensaje ya enviado, desde un hilo normal. Lanza si no se pudo.

        Se usa cuando la familia pulsa «Ya lo atendí»: el mensaje original se
        reescribe con la hora de atención y sin botones. Si en su lugar se mandara
        otro mensaje, el familiar vería el aviso de gas y debajo un «ok, ya lo
        atendiste», que parece que hay dos avisos.

        El `chat_id` es el del chat donde se pulsó, no un chat por defecto: con dos
        familiares, editar en el chat equivocado deja al que pulsó viendo el aviso
        viejo y le devuelve un error de Telegram que no explica nada.

        Si el mensaje es demasiado viejo para editarlo, Telegram devuelve un error
        y aquí se propaga al manejador del botón, que lo avisa con una frase. No es
        un problema del sistema: solo pasa con avisos de más de 48 horas.
        """
        if not self.disponible or self.bot is None or mensaje is None:
            raise TelegramNoConfigurado("no se puede editar: falta el token, el "
                                        "chat o el mensaje")
        self._ejecutar(self._editar_async(mensaje, texto, teclado,
                                          chat_id or getattr(mensaje, "chat_id", None)))

    def editar_futuro(self, mensaje, texto: str, teclado=None,
                      chat_id: str = None):
        """Como `editar`, pero devuelve un future para esperar con `await`.

        Es la que usa el botón del bot, que ya corre dentro de un bucle de eventos.
        Llamar a `editar()` desde ahí creaba un bucle dentro del bucle, y el botón
        fallaba con «asyncio.run() cannot be called from a running event loop».

        El `Message` va como argumento y no como `await bot.send_message(...)`
        dentro de la corrutina, porque el `Bot` de este remitente pertenece al bucle
        del hilo de Telegram, no al del bot de comandos: tocarlo desde el otro bucle
        es justo el problema que este diseño evita.
        """
        if not self.disponible or self.bot is None or mensaje is None:
            raise TelegramNoConfigurado("no se puede editar: falta el token, el "
                                        "chat o el mensaje")
        return self._futuro(self._editar_async(mensaje, texto, teclado,
                                               chat_id or getattr(mensaje, "chat_id", None)))

    async def _editar_async(self, mensaje, texto: str, teclado=None,
                            chat_id: str = None) -> None:
        await self.bot.edit_message_text(
            chat_id=chat_id,
            message_id=mensaje.message_id,
            text=texto,
            parse_mode=None,
            reply_markup=teclado,
        )

    def verificar(self) -> bool:
        """Comprueba el token contra la API de Telegram.

        Sirve para el log de arranque y para las pruebas. Un token inválido
        devuelve 401 y esta función devuelve `False`, sin lanzar.
        """
        if not self.token or self.bot is None:
            return False
        try:
            self._ejecutar(self.bot.get_me(), timeout=15.0)
            return True
        except TelegramError as error:
            registro.error("el token de Telegram no responde: %s", sanear(error))
            return False
        except Exception as error:  # noqa: BLE001
            registro.error("no se pudo comprobar el token: %s", sanear(error))
            return False
