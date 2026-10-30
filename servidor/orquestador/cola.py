"""El trabajador que manda a Telegram lo que está en la cola.

Corre en un hilo aparte. La razón de que sea un hilo y no parte del bucle
principal es que el envío a Telegram es una llamada de red que puede tardar
varios segundos, y si estuviera en el bucle principal bloquearía la recepción de
MQTT. Con un corte de Internet, la llamada puede tardar diez segundos, y en esos
diez segundos el broker estaría encolando alertas sin que nadie las leyera.

El hilo es un bucle simple, sin framework de tareas ni planificador. Tres estados
y un `time.sleep` los cubren:

1. Hay envíos pendientes cuyo próximo intento ya venció: se manda uno.
2. Telegram falló: se anota el error y se dice cuándo volver a intentarlo.
3. No hay nada que hacer: se duerme `PAUSA_TRABAJADOR_S` segundos.

## La unidad de trabajo es un envío, no una alerta

Esto es lo que cambió con los familiares múltiples, y es la decisión que sostiene
todo lo demás: el bucle toma una fila de `envios`, que es **un aviso con un familiar de destino**, y no una fila de `alertas`, que sería un aviso sin destinatario.

La diferencia se ve en el fallo. Si un familiar tiene el móvil sin cobertura, su
envío falla y se reintenta con su propia escalera de esperas, mientras el envío del
otro familiar sale en el mismo bucle. Con los reintentos en la tabla de alertas, el
móvil apagado haría esperar a cada aviso nuevo hasta que el otro lo encendiera, y
un aviso de gas se retrasaría por el teléfono de una persona que no es la que está
en casa.

Cada reintento sube solo el contador de esa fila: los dos familiares llevan la
cuenta por separado, y el que va bien no va a arrastrar al que va mal.

**Un envío nunca se descarta por un fallo de red.** Se reintenta para siempre, con
la escalera 15, 30, 60, 120 y 300 segundos, y a partir de ahí cada 300 segundos. La
versión anterior gastaba cinco intentos y marcaba la alerta como `descartada`, con
lo que un corte de Internet de cinco minutos convertía un aviso de gas en algo que
no se mandaba nunca: la familia se enteraba de que no le habían avisado, que es
peor que un aviso tardío.

La contrapartida de reintentar para siempre es que nada avisa de que la cola está
atascada, así que el bucle revisa una vez por hora si hay envíos con más de 24 h
pendientes y, si los hay, escribe una línea de ERROR por cada uno. Un fallo de red
tampoco se escribe nunca en crudo: `telegram_cliente.sanear` quita el token de
cualquier mensaje de error antes de que llegue al log o a la base.

Sobre el riesgo de doble envío, que es real y conocido:

Si el proceso muere **entre** la respuesta de Telegram y el `UPDATE` que marca el
envío como enviado, el siguiente arranque lo vuelve a encontrar pendiente y lo
manda otra vez. La ventana es de unos cientos de milisegundos: el tiempo entre
que Telegram responde "enviado" y el siguiente `commit` de SQLite.

No se puede eliminar del todo, y conviene entender por qué. Telegram no tiene
idempotencia: no hay forma de mandarle un `id` y que diga "ya te lo mandé". Y no se
puede marcar como enviado *antes* de mandar, porque entonces un fallo de red
dejaría el envío marcado como enviado sin haber llegado nunca a la familia, que
es el fallo que más duele: un aviso de gas que el sistema cree haber enviado y
que nadie ha visto.

Se eligió el lado malo a propósito: es preferible que la familia reciba dos
avisos de gas a que no reciba ninguno.

## Los chats no salen enteros al log

Cada línea dice a qué familiar va el envío con `db.enmascarar_chat`, que deja los
últimos cuatro dígitos. Con dos familiares eso basta para distinguirlos, y con uno
basta para decir que se registró y cuándo, sin publicar el número de una persona
en algo que se lee con cualquiera que tenga el shell del servidor.
"""

import json
import logging
import threading
import time

import config
import db as db_modulo
import alertas
import telegram_cliente
import textos
from telegram_cliente import TelegramNoConfigurado

registro = logging.getLogger("cola")


class TrabajadorTelegram:
    def __init__(self, base, remitente):
        self.base = base
        self.remitente = remitente
        self.hilo = None
        self.parar = threading.Event()
        self.ultimo_aviso = 0.0
        # Los envíos que ya se han avisado por falta de token o de destino. Sin este
        # registro, cada envío sin destino escribe una línea de ERROR cada
        # `PAUSA_TRABAJADOR_S * 10`, que con cuatro alertas de prueba pendientes son
        # casi cinco líneas por minuto, todas iguales. El error de fondo ya está
        # dicho una vez; lo que se repite no aporta nada y tapa el resto del log.
        #
        # La clave es (id_alerta, chat_id) y no solo el id: con dos familiares, si
        # uno no tiene destino y el otro sí, avisar solo por id_alerta callaría el
        # error del primero cada vez que el segundo tuviera un problema.
        self.sin_destino_avisados = set()

    def arrancar(self) -> None:
        self.hilo = threading.Thread(target=self._bucle, name="cola",
                                     daemon=True)
        self.hilo.start()
        registro.info("trabajador de la cola arrancado")

    def detener(self) -> None:
        self.parar.set()
        if self.hilo is not None:
            self.hilo.join(timeout=15)

    def _bucle(self) -> None:
        while not self.parar.is_set():
            try:
                trabajo = self._intentar_uno()
                self._avisar_de_pendientes_viejas()
                self._editar_resueltas()
            except Exception as error:  # noqa: BLE001
                # Un fallo inesperado en el trabajador no puede tumbar el
                # servicio: las alertas seguirían llegando a MQTT y la cola
                # llenándose. Se registra y se sigue.
                registro.exception("fallo inesperado en el trabajador: %s", error)
                trabajo = False
            if not trabajo:
                # `wait` y no `sleep`: devuelve en cuanto alguien marque parar, en
                # lugar de esperar los cinco segundos completos al apagar.
                self.parar.wait(config.PAUSA_TRABAJADOR_S)

    def _intentar_uno(self) -> bool:
        """Manda un envío si hay alguno que toque. Devuelve si hubo trabajo.

        Coge UN envío, no todos los que toquen. Mandarlos todos en una vuelta
        parecería más rápido, pero si Telegram está caído serían N llamadas
        fallidas de N seguidos en lugar de una, y con la escalera de esperas el
        primer reintento se aleja de golpe. Uno por vuelta, y el bucle vuelve a
        mirar en `PAUSA_TRABAJADOR_S`.
        """
        pendientes = self.base.tomar_envios_pendientes(limite=1)
        if not pendientes:
            return False

        fila = pendientes[0]
        id_alerta = fila["alerta_id"]
        chat_id = fila["chat_id"]
        tipo = fila["tipo"]
        intentos = fila["intentos"] + 1
        quien = db_modulo.enmascarar_chat(chat_id)
        clave = (id_alerta, chat_id)

        try:
            carga = json.loads(fila["carga_json"])
        except ValueError:
            # No debería pasar: la fila se insertó desde un JSON que ya se había
            # validado. Es el único caso en el que una alerta se descarta, porque
            # reintentar un texto ilegible no la arregla nunca.
            registro.error("la alerta %s tiene carga_json ilegible; se descarta "
                           "para siempre (no es un fallo de red)", id_alerta)
            self.base.marcar_alerta_descartada(id_alerta, "carga_json ilegible")
            return True

        texto = alertas.texto_para_telegram(carga, tipo)
        registro.info("mandando a Telegram la alerta %s (%s) al familiar %s, "
                      "intento %d", id_alerta, tipo, quien, intentos)

        try:
            mensaje = self.remitente.enviar(
                texto, teclado=alertas.teclado_alerta(id_alerta),
                chat_id=chat_id,
            )
        except TelegramNoConfigurado as error:
            # No es un fallo de envío: es que no hay token, o la fila de este
            # envío se quedó sin familiar. El envío se queda pendiente, SIN gastar
            # un intento, y se vuelve a mirar más adelante. Así, si alguien pone el
            # token dentro de una hora, los envíos que se habían acumulado salen sin
            # haber perdido nada.
            #
            # El ERROR sale una sola vez por envío. Mientras falte el token o el
            # chat, el mismo error se repite cada 50 segundos por envío, y un log
            # lleno de la misma línea esconde lo que sí ha cambiado.
            if clave in self.sin_destino_avisados:
                registro.debug("el envio de %s a %s sigue sin destino: %s",
                               id_alerta, quien, error)
            else:
                self.sin_destino_avisados.add(clave)
                registro.error("el envio de la alerta %s al familiar %s sigue en la "
                               "cola: %s", id_alerta, quien, error)
            self.base.marcar_envio_fallido(id_alerta, chat_id, intentos - 1,
                                           int(time.time())
                                           + config.PAUSA_TRABAJADOR_S * 10,
                                           str(error))
            return True
        except Exception as error:  # noqa: BLE001
            # Error permanente: el envio deja de reintentarse y queda marcado como
            # fallido. El familiar NO se inactiva: eso es decisión del operador.
            if telegram_cliente.es_error_permanente(error):
                motivo = type(error).__name__
                registro.warning("fallo PERMANENTE al mandar la alerta %s al familiar "
                                 "%s (intento %d): %s; no se reintenta. El familiar "
                                 "no se inactiva. Los envios a los demas familiares "
                                 "no se ven afectados.", id_alerta, quien, intentos,
                                 motivo)
                self.base.marcar_envio_fallido(
                    id_alerta, chat_id, intentos,
                                           db_modulo.ENVIO_FALLIDO_PERMANENTE, motivo,
                )
                return True

            # Un fallo de red, o un rechazo de la API. El envío NO se descarta:
            # se queda pendiente y se reintenta para siempre con la escalera de
            # esperas de `config.REINTENTO_SEGUNDOS`.
            #
            # Un `BadRequest` que NO sea permanente (formato HTML, longitud, etc.)
            # se sigue reintentando como un fallo de red, pero se registra como
            # ERROR con el tipo de error y sin el texto del mensaje.
            #
            # Y no toca los envíos de los otros familiares: cada uno tiene su fila
            # y su reloj. Este fallo es de este familiar, no de la alerta.
            #
            # El error se limpia antes de escribirse: python-telegram-bot incluye la
            # URL de la API en algunos errores, y esa URL lleva el token del bot.
            if telegram_cliente.es_badrequest(error):
                motivo = type(error).__name__
                espera = espera_actual(intentos)
                registro.error("fallo al mandar la alerta %s al familiar %s (intento "
                               "%d): %s; reintento en %d s. Este envio NO se "
                               "descarta: se reintenta hasta que salga. Los envios "
                               "a los demas familiares no se ven afectados.",
                               id_alerta, quien, intentos, motivo, espera)
            else:
                motivo = telegram_cliente.sanear(
                    "%s: %s" % (type(error).__name__, error))
                espera = espera_actual(intentos)
                registro.warning("fallo al mandar la alerta %s al familiar %s (intento "
                                 "%d): %s; reintento en %d s. Este envio NO se "
                                 "descarta: se reintenta hasta que salga. Los envios "
                                 "a los demas familiares no se ven afectados.",
                                 id_alerta, quien, intentos, motivo, espera)
            self.base.marcar_envio_fallido(
                id_alerta, chat_id, intentos, int(time.time()) + espera, motivo,
            )
            return True

        # Aquí está la ventana del riesgo de doble envío: entre esta línea y el
        # commit de `marcar_enviado` hay unos cientos de milisegundos. Si el
        # proceso muere ahora, el siguiente arranque mandará el envío otra vez, a
        # ESE familiar, y solo a ese.
        # El `message_id` viaja aqui y no en otro sitio: es lo que luego permite
        # reescribir ESTE mensaje si la alerta se atiende desde el panel, que no
        # tiene token para hacerlo en el momento.
        self.base.marcar_enviado(
            id_alerta, chat_id,
            mensaje_id=getattr(mensaje, "message_id", None),
        )
        self.sin_destino_avisados.discard(clave)
        registro.warning("ALERTA %s enviada al familiar %s (id %s, intento %d)",
                         tipo.upper(), quien, id_alerta, intentos)
        return True

    def _editar_resueltas(self) -> None:
        """Reescribe en Telegram los avisos de alertas atendidas fuera del bot.

        El panel y el bot resuelven por el MISMO camino (`alertas.resolver`), pero
        solo el bot puede editar el mensaje mientras lo hace: tiene el objeto
        `Message` en la mano y es su proceso el que lleva el token. El panel es otro
        proceso, resuelve en la base y se queda sin más. Esta tarea es la otra
        mitad: mira los avisos ya enviados cuya alerta está resuelta y a los que les
        falta la reescritura, y los deja como los deja el botón («ATENDIDA», sin
        botones), para que la familia no vea un aviso con los botones puestos mientras
        ya hay alguien con el aviso.

        No hace falta saber quién la resolvió. La condición es solo «está resuelta y
        todavía no se ha reescrito», y cuando la resolución salió del botón ese
        mismo botón ya marcó `editado_en`, con lo que lo suyo no se toca dos veces.

        Marca la fila aunque la edición falle, y esa decisión está comentada en
        `db.marcar_editado`: el único fallo real es que Telegram rechace editar ese
        mensaje (aviso de más de 48 horas, chat borrado), y reintentarlo cada cinco
        segundos para siempre no lo arregla y tapa el log.
        """
        for fila in self.base.envios_a_editar(limite=10):
            if fila["resuelto_por"] == "sala:falsa_alarma":
                texto = textos.aviso_falsa_alarma(fila["resuelto_en"])
            else:
                texto = alertas.texto_atendido(fila["tipo"], fila["resuelto_en"],
                                               fila["alerta_id"])
            quien = db_modulo.enmascarar_chat(fila["chat_id"])
            try:
                self.remitente.editar_mensaje(fila["chat_id"], fila["mensaje_id"],
                                              texto)
            except Exception as error:  # noqa: BLE001
                # El mismo saneado que el resto: un error de la API de Telegram
                # puede traer la URL de la API, y esa URL lleva el token.
                registro.warning("no se pudo reescribir el aviso de la alerta %s "
                                 "para %s: %s", fila["alerta_id"], quien,
                                 telegram_cliente.sanear(error))
            else:
                registro.info("aviso de la alerta %s reescrito como ATENDIDA para "
                              "%s (atendida desde el panel)",
                              fila["alerta_id"], quien)
            self.base.marcar_editado(fila["alerta_id"], fila["chat_id"])

    def _avisar_de_pendientes_viejas(self) -> None:
        """Una vez por hora, dice qué envíos llevan más de 24 h sin salir.

        Es el otro lado de "no se descarta nunca": un envío que no se puede mandar
        se reintenta para siempre, y si además nadie lo dice, una cola atascada
        (token caducado, chat borrado, API caída) puede llevar días sin que nadie se
        entere. Aquí sale una línea por envío y una con el total.

        El aviso es por hora y no por intento, porque los reintentos de un envío
        atascado son uno cada cinco minutos: doce líneas por hora y por envío
        taparían el log y no añadirían nada.
        """
        ahora = time.time()
        if ahora - self.ultimo_aviso < config.SEGUNDOS_ENTRE_AVISOS:
            return
        self.ultimo_aviso = ahora

        try:
            viejas = self.base.listar_envios_pendientes_antigas(
                config.SEGUNDOS_AVISO_PENDIENTE, int(ahora),
            )
        except Exception as error:  # noqa: BLE001
            registro.error("no se pudo revisar si hay envios pendientes viejos: %s",
                           error)
            return

        if not viejas:
            return
        for fila in viejas:
            horas = (int(ahora) - fila["alerta_creado_en"]) // 3600
            registro.error(
                "AVISO: la alerta %s (%s) al familiar %s lleva %d h sin llegar. Se "
                "sigue reintentando solo ese envio. Ultimo error: %s",
                fila["alerta_id"], fila["tipo"],
                db_modulo.enmascarar_chat(fila["chat_id"]), horas,
                fila["ultimo_error"] or "ninguno",
            )
        registro.error("AVISO: %d envios llevan mas de %d h sin poder mandarse",
                       len(viejas), config.SEGUNDOS_AVISO_PENDIENTE // 3600)



def espera_actual(intentos: int) -> int:
    """Cuántos segundos esperar antes del siguiente intento.

    La lista es `config.REINTENTO_SEGUNDOS` (15, 30, 60, 120, 300) y NO tiene
    máximo: a partir del quinto fallo se espera siempre 300 segundos. Los reintentos
    son indefinidos, así que el último valor de la lista es también el último para
    siempre.
    """
    if intentos >= len(config.REINTENTO_SEGUNDOS):
        return config.REINTENTO_SEGUNDOS[-1]
    return config.REINTENTO_SEGUNDOS[intentos - 1]
