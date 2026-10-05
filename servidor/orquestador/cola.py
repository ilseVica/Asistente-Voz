"""El trabajador que manda a Telegram lo que está en la cola.

Corre en un hilo aparte. La razón de que sea un hilo y no parte del bucle
principal es que el envío a Telegram es una llamada de red que puede tardar
varios segundos, y si estuviera en el bucle principal bloquearía la recepción de
MQTT. Con un corte de Internet, la llamada puede tardar diez segundos, y en esos
diez segundos el broker estaría encolando alertas sin que nadie las leyera.

El hilo es un bucle simple, sin framework de tareas ni planificador. Tres estados
y un `time.sleep` los cubren:

1. Hay alertas pendientes cuyo próximo intento ya venció: se manda una.
2. Telegram falló: se anota el error y se dice cuándo volver a intentarlo.
3. No hay nada que hacer: se duerme `PAUSA_TRABAJADOR_S` segundos.

**Una alerta nunca se descarta por un fallo de red.** Se reintenta para siempre,
con la escalera 15, 30, 60, 120 y 300 segundos, y a partir de ahí cada 300
segundos. La versión anterior gastaba cinco intentos y marcaba la alerta como
`descartada`, con lo que un corte de Internet de cinco minutos convertía un aviso
de gas en algo que no se mandaba nunca: la familia se enteraba de que no le habían
avisado, que es peor que un aviso tardío.

La contrapartida de reintentar para siempre es que nada avisa de que la cola está
atascada, así que el bucle revisa una vez por hora si hay alertas con más de 24 h
pendientes y, si las hay, escribe una línea de ERROR por cada una. Un fallo de red
tampoco se escribe nunca en crudo: `telegram_cliente.sanear` quita el token de
cualquier mensaje de error antes de que llegue al log o a la base.

Sobre el riesgo de doble envío, que es real y conocido:

Si el proceso muere **entre** la respuesta de Telegram y el `UPDATE` que marca la
alerta como enviada, el siguiente arranque la vuelve a encontrar pendiente y la
manda otra vez. La ventana es de unos cientos de milisegundos: el tiempo entre
que Telegram responde "enviado" y el siguiente `commit` de SQLite.

No se puede eliminar del todo, y conviene entender por qué. Telegram no tiene
idempotencia: no hay forma de mandarle un `id` y que diga "ya te lo mandé". Y no
se puede marcar como enviada *antes* de mandar, porque entonces un fallo de red
dejaría la alerta marcada como enviada sin haber llegado nunca a la familia, que
es el fallo que más duele: un aviso de gas que el sistema cree haber enviado y
que nadie ha visto.

Se eligió el lado malo a propósito: es preferible que la familia reciba dos
avisos de gas a que no reciba ninguno.
"""

import json
import logging
import threading
import time

import config
import alertas
import telegram_cliente
from telegram_cliente import TelegramNoConfigurado

registro = logging.getLogger("cola")


class TrabajadorTelegram:
    def __init__(self, base, remitente):
        self.base = base
        self.remitente = remitente
        self.hilo = None
        self.parar = threading.Event()
        self.ultimo_aviso = 0.0
        # Las alertas que ya se han avisado por falta de token o de chat. Sin este
        # registro, cada alerta sin destino escribe una línea de ERROR cada
        # `PAUSA_TRABAJADOR_S * 10`, que con cuatro alertas de prueba pendientes son
        # casi cinco líneas por minuto, todas iguales. El error de fondo ya está
        # dicho una vez; lo que se repite no aporta nada y tapa el resto del log.
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
        """Manda una alerta si hay alguna que toque. Devuelve si hubo trabajo."""
        pendientes = self.base.tomar_pendientes(limite=1)
        if not pendientes:
            return False

        fila = pendientes[0]
        id_alerta = fila["id"]
        tipo = fila["tipo"]
        intentos = fila["intentos"] + 1

        try:
            carga = json.loads(fila["carga_json"])
        except ValueError:
            # No debería pasar: la fila se insertó desde un JSON que ya se había
            # validado. Es el único caso en el que una alerta se descarta, porque
            # reintentar un texto ilegible no la arregla nunca.
            registro.error("la alerta %s tiene carga_json ilegible; se descarta "
                           "para siempre (no es un fallo de red)", id_alerta)
            self.base.marcar_descartada(id_alerta, intentos, "carga_json ilegible")
            return True

        texto = alertas.texto_para_telegram(carga, tipo)
        registro.info("mandando a Telegram la alerta %s (%s), intento %d",
                      id_alerta, tipo, intentos)

        try:
            self.remitente.enviar(texto, teclado=alertas.teclado_alerta(id_alerta))
        except TelegramNoConfigurado as error:
            # No es un fallo de envío: es que no hay a quién enviar. La alerta se
            # queda pendiente, SIN gastar un intento, y se vuelve a mirar más
            # adelante. Así, si alguien pone el token dentro de una hora, las
            # alertas que se habían acumulado salen sin haber perdido nada.
            #
            # El ERROR sale una sola vez por alerta. Mientras falte el chat o el
            # token, el mismo error se repite cada 50 segundos por alerta, y un log
            # lleno de la misma línea esconde lo que sí ha cambiado.
            if id_alerta in self.sin_destino_avisados:
                registro.debug("la alerta %s sigue sin destino: %s", id_alerta, error)
            else:
                self.sin_destino_avisados.add(id_alerta)
                registro.error("la alerta %s sigue en la cola: %s", id_alerta, error)
            self.base.marcar_fallida(id_alerta, intentos - 1,
                                     int(time.time()) + config.PAUSA_TRABAJADOR_S * 10,
                                     str(error))
            return True
        except Exception as error:  # noqa: BLE001
            # Un fallo de red, o un rechazo de la API. La alerta NO se descarta:
            # se queda pendiente y se reintenta para siempre con la escalera de
            # esperas de `config.REINTENTO_SEGUNDOS`.
            #
            # El error se limpia antes de escribirse: python-telegram-bot incluye la
            # URL de la API en algunos errores, y esa URL lleva el token del bot.
            motivo = telegram_cliente.sanear("%s: %s" % (type(error).__name__, error))
            espera = espera_actual(intentos)
            registro.warning("fallo al mandar la alerta %s (intento %d): %s; "
                             "reintento en %d s. La alerta NO se descarta: se "
                             "reintenta hasta que salga.",
                             id_alerta, intentos, motivo, espera)
            self.base.marcar_fallida(
                id_alerta, intentos, int(time.time()) + espera, motivo,
            )
            return True

        # Aquí está la ventana del riesgo de doble envío: entre esta línea y el
        # commit de `marcar_enviada` hay unos cientos de milisegundos. Si el
        # proceso muere ahora, el siguiente arranque mandará la alerta otra vez.
        self.base.marcar_enviada(id_alerta)
        self.sin_destino_avisados.discard(id_alerta)
        registro.warning("ALERTA %s enviada a la familia (id %s, intento %d)",
                         tipo.upper(), id_alerta, intentos)
        return True

    def _avisar_de_pendientes_viejas(self) -> None:
        """Una vez por hora, dice si hay alertas que llevan más de 24 h sin salir.

        Es el otro lado de "no se descarta nunca": una alerta que no se puede mandar
        se reintenta para siempre, y si además nadie lo dice, una cola atascada
        (token caducado, chat borrado, API caída) puede llevar días sin que nadie se
        entere. Aquí sale una línea por alerta y una con el total.

        El aviso es por hora y no por intento, porque los reintentos de una alerta
        atascada son uno cada cinco minutos: doce líneas por hora y por alerta
        taparían el log y no añadirían nada.
        """
        ahora = time.time()
        if ahora - self.ultimo_aviso < config.SEGUNDOS_ENTRE_AVISOS:
            return
        self.ultimo_aviso = ahora

        try:
            viejas = self.base.listar_pendientes_antigas(
                config.SEGUNDOS_AVISO_PENDIENTE, int(ahora),
            )
        except Exception as error:  # noqa: BLE001
            registro.error("no se pudo revisar si hay alertas pendientes viejas: %s",
                           error)
            return

        if not viejas:
            return
        for fila in viejas:
            horas = (int(ahora) - fila["creado_en"]) // 3600
            registro.error(
                "AVISO: la alerta %s (%s) lleva %d h pendiente y no se ha podido "
                "mandar. Se sigue reintentando. Ultimo error: %s",
                fila["id"], fila["tipo"], horas,
                fila["ultimo_error"] or "ninguno",
            )
        registro.error("AVISO: %d alertas llevan mas de %d h sin poder mandarse",
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
