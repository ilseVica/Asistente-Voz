"""El reloj de la inactividad (Fase 4): cuánto lleva la casa sin movimiento.

El firmware lee el PIR de la sala y publica `casa/sala/sensores` con
`{"presencia": true|false}`. Este módulo guarda el instante del último movimiento,
y cada `SEGUNDOS_CICLO_INACTIVIDAD` mira si ya pasó el tiempo configurado sin
ninguno. Cuando pasa, encola una alerta para la familia (por la cola de Telegram,
igual que el gas y la ayuda) y la publica en `casa/sala/alerta/inactividad` para
que el panel la vea en vivo. El asistente NO la dice en voz alta: es una alerta
silenciosa (ver `alertas.resolver`).

## Por qué es un módulo y no un `if` en el bucle de mensajes

El gas y la ayuda son HECHOS: llega un mensaje y se encola la alerta. La
inactividad es una AUSENCIA: no llega ningún mensaje, y el aviso depende de que
pase el tiempo. Eso necesita memoria (el último movimiento) que sobrevive entre
mensajes, y un ciclo que corra aunque no llegue nada. Las dos cosas viven aquí.

## La memoria vive en SQLite, no en el proceso

`ultimo_movimiento` se guarda en la tabla `configuracion`. Si viviera solo en
memoria, reiniciar el orquestador borraría el reloj y una inactividad real se
perdería en el reinicio, que es justo lo que no puede pasar en un aviso de
seguridad. Al arrancar se lee de la base; si no hay valor (primer arranque,
instalación nueva) se siembra con la hora de arranque, para no disparar una
alerta en el primer ciclo por un movimiento que nunca se registró.

## Un episodio es UNA alerta, y termina con el siguiente movimiento

`episodio_abierto` es memoria y NO se persiste, a propósito: lo que impide el
doble aviso es el id determinista `srv-inactividad-<ultimo_movimiento>` y la
deduplicación de `ids_procesados`/`alertas`. Un reinicio con el episodio abierto
no reenvía la alerta (el id ya está en la base), y la vuelve a abrir si sigue sin
haber movimiento, que es lo correcto: la casa sigue sin moverse.

El episodio se cierra con el siguiente `presencia: true`. «Ya lo atendí» NO lo
cierra y NO reinicia `ultimo_movimiento`: atender un aviso y volver a mover la
casa son cosas distintas. Si resolver el aviso reiniciara el reloj, el sistema
taparía que la persona sigue sin moverse, que es exactamente lo que hay que
vigilar. Por eso el siguiente aviso solo sale tras un movimiento nuevo Y otro
periodo completo.

## Cuándo NO se avisa, aunque pase el tiempo

  - **El nodo está desconectado** (`nodo/estado` con `conectado:false`, o todavía
    no ha dicho nada). Sin nodo no llega ningún `presencia`, así que no se puede
    saber si la persona se mueve; avisar sería una alarma ciega.
  - **El enlace del sitio está caído con un `0` EXPLÍCITO** (`$SYS/.../state`).
    En modo instalación no hay puente y el tópico no existe: «sin noticia» se
    trata como conectado, no como caído.

## Reconexiones

Cuando el nodo, el enlace o el propio cliente MQTT vuelven, el reloj se mueve a
`max(ultimo_movimiento, ahora)`: durante el corte el orquestador no vio los
`presencia`, así que no puede saber si hubo movimiento, y NO mover el reloj
dispararía una alerta falsa justo al reconectar. La PRIMERA conexión de MQTT al
arrancar no cuenta como reconexión (el cliente no llama al gancho la primera
vez): si contara, cada reinicio sembraría el reloj con la hora de arranque y una
inactividad larga quedaría tapada por un reinicio.

## Por qué el ajuste se relee en cada vuelta

El número de minutos vive en `configuracion.tiempo_inactividad_min` y lo escribe
el panel. Se lee con `base.tiempo_inactividad()` en CADA vuelta del ciclo, no una
vez al arrancar: así un cambio de la familia se aplica en la vuelta siguiente,
sin reiniciar nada y sin mandar ningún mensaje por MQTT. Es el ajuste «en
caliente» que pidió el usuario el 2026-10-16.
"""

import json
import logging
import threading
import time

import config

registro = logging.getLogger("inactividad")

# El prefijo del id de una alerta de inactividad. Con el epoch del último
# movimiento pegado (`srv-inactividad-1700000000`) el id es determinista: el mismo
# episodio produce siempre el mismo id, así que un reintento, un solape del ciclo
# o un reinicio no lo duplican. Cabe de sobra en el filtro de `alertas.ID_VALIDO`
# (minúsculas, dígitos y guiones, 64 caracteres máximo).
PREFIJO_ID = "srv-inactividad-"


def es_enlace_sitio(topico) -> bool:
    """Si el tópico es el del estado del enlace del puente (`$SYS/.../state`).

    Se reconoce por FORMA y no comparándolo con `config.TOPICO_ENLACE_SITIO`, que
    lleva un `+` (el `client_id` del puente, `puente-sitio`): un tópico que llega
    de verdad nunca lleva el `+` dentro, así que la igualdad no sirve. Es el mismo
    motivo por el que `alertas.client_id_de_estado` usa una expresión regular.
    """
    partes = (topico or "").split("/")
    return (len(partes) == 5 and partes[0] == "$SYS" and partes[1] == "broker"
            and partes[2] == "connection" and partes[4] == "state")


def _cuerpo_a_carga(cuerpo):
    """El JSON del cuerpo, o `None` si no es un objeto legible."""
    try:
        carga = json.loads(cuerpo)
    except (TypeError, ValueError):
        return None
    return carga if isinstance(carga, dict) else None


def _presencia_del_cuerpo(cuerpo):
    """`True`/`False` si el mensaje trae una presencia legible, o `None`.

    El contrato manda un booleano, pero se acepta `1`/`0` y `"true"`/`"false"`:
    un publicador que mande el dato de otra forma no es razón para perder un
    movimiento, que es la señal que apaga el reloj.
    """
    carga = _cuerpo_a_carga(cuerpo)
    if carga is None or "presencia" not in carga:
        return None
    presencia = carga.get("presencia")
    if isinstance(presencia, bool):
        return presencia
    if isinstance(presencia, (int, float)):
        return presencia != 0
    if isinstance(presencia, str):
        return presencia.strip().lower() not in ("", "0", "false", "off", "no")
    return None


def _nodo_conectado(cuerpo):
    """`True`/`False` del campo `conectado` de `nodo/estado`, o `None`."""
    carga = _cuerpo_a_carga(cuerpo)
    if carga is None:
        return None
    conectado = carga.get("conectado")
    return conectado if isinstance(conectado, bool) else None


def _enlace_caido(cuerpo):
    """Si el cuerpo del `$SYS` dice, con un `0` EXPLÍCITO, que el enlace cayó.

    Solo un «no» claro bloquea la alerta (ver `config.ENLACE_SITIO_VALORES_CAIDO`).
    Un cuerpo vacío, o cualquier cosa que no sea un «no» reconocible, se trata
    como conectado: en modo instalación el tópico no existe y no llega nada, y eso
    no puede apagar la inactividad.

    El cuerpo del puente llega como texto plano (`1` o `0`), así que NO se usa
    `_cuerpo_a_carga`, que descarta lo que no sea un objeto JSON y convertiría un
    `0` en «sin dato». Aquí se intenta leer como JSON (por si un día llega
    `{"conectado":false}`) y, si no lo es, se trata como el texto que es.
    """
    try:
        carga = json.loads(cuerpo)
    except (TypeError, ValueError):
        carga = cuerpo
    if isinstance(carga, dict):
        if "conectado" in carga:
            return carga.get("conectado") is False
        if "estado" in carga:
            carga = carga.get("estado")
    if isinstance(carga, bool):
        return carga is False
    if isinstance(carga, (int, float)):
        return carga == 0
    texto = str(carga).strip().lower()
    if texto == "":
        return False
    return texto in config.ENLACE_SITIO_VALORES_CAIDO


class Estado:
    """El reloj de la inactividad: último movimiento y estado de las señales.

    Se escribe desde el hilo de red de paho (los `on_message`, que llaman a
    `registrar_sensores` / `registrar_nodo` / `registrar_enlace`) y se lee desde
    el hilo del ciclo (`verificar`). Lleva su propio cerrojo por el mismo motivo
    que `recordatorios.Satelites`: dos hilos tocando el mismo estado sin uno es
    una carrera esperando a pasar.
    """

    def __init__(self, base, ahora=None):
        self.base = base
        self._cerrojo = threading.Lock()
        if ahora is None:
            ahora = int(time.time())
        guardado = base.ultimo_movimiento()
        if guardado is None:
            self.ultimo_movimiento = int(ahora)
            # Se persiste ya, y no solo al primer movimiento: si no, cada reinicio
            # antes de ver un `presencia` volvería a sembrar el reloj, y reiniciar
            # el servicio sería una forma de tapar una inactividad en curso.
            base.fijar_ultimo_movimiento(self.ultimo_movimiento)
            origen = "sembrado con la hora de arranque"
        else:
            self.ultimo_movimiento = int(guardado)
            origen = "leido de la base"
        self.presencia_anterior = False
        self.episodio_abierto = False
        # `None` es «todavía no sé nada de esta señal», que NO es lo mismo que
        # «desconectada». El nodo empieza en `None` a propósito: la primera
        # noticia de su estado no puede contar como una reconexión (si contara,
        # arrancar el servicio movería el reloj y taparía la inactividad).
        self.nodo_conectado = None
        self.sitio_conectado = None
        registro.info("reloj de inactividad arrancado: ultimo movimiento %s "
                      "(hace %d s)", origen, self._hace(ahora))

    def _hace(self, ahora) -> int:
        return int(ahora) - self.ultimo_movimiento

    def _mover_reloj(self, cuando, motivo) -> None:
        """Adelanta el reloj a `cuando` si es más tarde, y lo persiste.

        Nunca lo ATRASA: si el reloj del sistema va hacia atrás un momento, o un
        mensaje llega fuera de orden, el último movimiento conocido es el que
        manda. La escritura va fuera del cerrojo, pero solo después de haber
        decidido el nuevo valor dentro.
        """
        cuando = int(cuando)
        with self._cerrojo:
            if cuando <= self.ultimo_movimiento:
                return
            self.ultimo_movimiento = cuando
        self.base.fijar_ultimo_movimiento(cuando)
        registro.debug("reloj de inactividad movido por %s a %d", motivo, cuando)

    # --- Mensajes que llegan del nodo y del broker --------------------------

    def registrar_sensores(self, cuerpo, ahora=None) -> None:
        """Un `casa/sala/sensores`. Mueve el reloj con el movimiento.

        El reloj se mueve con un `presencia: true` (hay movimiento AHORA) y con el
        PRIMER `false` que siga a un `true` (es cuando se sabe que el movimiento
        paró). Un `false` que no venga detrás de un `true` no dice nada: son los
        latidos sin movimiento, y mover el reloj con ellos sería no avisar nunca.
        """
        presencia = _presencia_del_cuerpo(cuerpo)
        if presencia is None:
            registro.warning("mensaje de sensores sin 'presencia' legible; se ignora")
            return
        if ahora is None:
            ahora = int(time.time())
        with self._cerrojo:
            anterior = self.presencia_anterior
            self.presencia_anterior = presencia
            if presencia:
                self.episodio_abierto = False
            mueve = presencia or anterior
        if mueve:
            self._mover_reloj(ahora, "presencia %s" % ("true" if presencia else "false"))

    def registrar_nodo(self, cuerpo, ahora=None) -> None:
        """Un `casa/sala/nodo/estado`. Anota si el nodo está conectado.

        Al PASAR de desconectado a conectado (no en la primera noticia ni en un
        retenido que diga `true` sobre un `true`), el reloj se reinicia: durante el
        corte no llegaron los `presencia`, así que no se puede saber si hubo
        movimiento, y avisar justo al reconectar sería una alarma ciega.
        """
        conectado = _nodo_conectado(cuerpo)
        if conectado is None:
            registro.warning("mensaje de nodo/estado sin 'conectado' booleano; se ignora")
            return
        if ahora is None:
            ahora = int(time.time())
        with self._cerrojo:
            antes = self.nodo_conectado
            self.nodo_conectado = conectado
            reconecta = antes is False and conectado is True
        if reconecta:
            registro.info("nodo reconectado; el reloj de inactividad se reinicia")
            self._mover_reloj(ahora, "reconexion del nodo")

    def registrar_enlace(self, cuerpo, ahora=None) -> None:
        """El estado del enlace del sitio (`$SYS/.../state`).

        Solo un `0` explícito lo marca caído. Mientras esté caído no se avisa: si
        el sitio no tiene enlace, los `presencia` no llegan y la ausencia de
        mensajes no significa ausencia de movimiento. Al recuperarse, el reloj se
        reinicia, por el mismo motivo que al reconectar el nodo.
        """
        caido = _enlace_caido(cuerpo)
        if ahora is None:
            ahora = int(time.time())
        with self._cerrojo:
            antes = self.sitio_conectado
            self.sitio_conectado = not caido
            reconecta = antes is False and not caido
        if reconecta:
            registro.info("enlace del sitio recuperado; el reloj de inactividad "
                          "se reinicia")
            self._mover_reloj(ahora, "reconexion del enlace del sitio")

    def registrar_reconexion(self, ahora=None) -> None:
        """Se llama cuando el cliente MQTT recupera la conexión (no la primera).

        Es el gancho que pasa `orquestador.main` al cliente. Reinicia el reloj
        porque durante el corte del MQTT tampoco llegaron los `presencia`.
        """
        if ahora is None:
            ahora = int(time.time())
        self._mover_reloj(ahora, "reconexion de MQTT")

    # --- El ciclo -----------------------------------------------------------

    def verificar(self, publicar, ahora=None) -> bool:
        """Una vuelta del reloj. Devuelve si encoló una alerta.

        Lee el tiempo configurado de la base en CADA vuelta (ajuste en caliente).
        Comprueba, en este orden y por este motivo:

          1. ¿El episodio ya está abierto? Entonces ya se avisó y no se repite.
          2. ¿El nodo está conectado? Si no, no se puede saber si hay movimiento.
          3. ¿El enlace del sitio está caído con un `0` explícito? Igual.
          4. ¿Ya pasó el tiempo sin movimiento? Si no, nada.
        """
        if ahora is None:
            ahora = int(time.time())
        with self._cerrojo:
            if self.episodio_abierto:
                return False
            nodo = self.nodo_conectado
            sitio = self.sitio_conectado
            ultimo = self.ultimo_movimiento
        if nodo is not True:
            return False
        if sitio is False:
            return False
        minutos = self.base.tiempo_inactividad()
        if ahora - ultimo < minutos * 60:
            return False
        return self._alertar(publicar, minutos, ahora)

    def _alertar(self, publicar, minutos, ahora) -> bool:
        """Encola la alerta del episodio y la publica. Devuelve si la encoló.

        El id sale del RELOJ (del último movimiento), no del momento del aviso: es
        lo que hace que el mismo episodio tenga siempre el mismo id y que un
        reinicio, un reintento o un solape del ciclo no lo dupliquen. El episodio
        se marca abierto ANTES de tocar la base, para que la siguiente vuelta del
        ciclo no lo vuelva a intentar mientras esto corre.
        """
        id_alerta = "%s%d" % (PREFIJO_ID, self.ultimo_movimiento)
        carga = {"id": id_alerta, "minutos": minutos}
        texto = json.dumps(carga, separators=(",", ":"))
        with self._cerrojo:
            self.episodio_abierto = True
        if not self.base.registrar_id(id_alerta, "inactividad"):
            registro.info("la inactividad %s ya estaba registrada; no se repite",
                          id_alerta)
            return False
        if not self.base.insertar_alerta(id_alerta, "inactividad", texto):
            registro.info("la inactividad %s ya estaba en la cola", id_alerta)
            return False
        # La casa NO se entera en voz alta: la inactividad es silenciosa, y el eco
        # de la voz podría despertar o asustar a quien está en la casa sin moverse.
        # El panel la ve por MQTT y la familia por Telegram (la cola).
        if publicar is not None:
            try:
                publicar(config.TOPICO_ALERTA_INACTIVIDAD, carga, qos=1, retain=False)
            except Exception as error:  # noqa: BLE001
                registro.error("no se pudo publicar la alerta de inactividad en "
                               "MQTT: %s", error)
        # Solo se encola a los ACTIVOS (db.insertar_alerta hace el fan out a
        # familiares_activos), y este log tiene que contar lo mismo que encola: con
        # contar_familiares() salia el total, incluidos los dados de baja.
        destinatarios = self.base.contar_familiares_con_telegram()
        if destinatarios:
            registro.warning("ALERTA de inactividad %s: %d min sin movimiento; encolada "
                             "para %d familiar(es) activo(s)", id_alerta, minutos,
                             destinatarios)
        else:
            registro.warning("ALERTA de inactividad %s: %d min sin movimiento; encolada, "
                             "pero no hay ningun familiar activo: se queda esperando a "
                             "que se active uno", id_alerta, minutos)
        return True
