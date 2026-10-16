"""El ciclo de los recordatorios: cuándo toca, si había satélite, y qué se dijo.

El panel guarda el recordatorio en SQLite y el orquestador lo lee de ahí; el único
paso que sale por MQTT es el texto hablado (`casa/sala/satelite/decir`). Este
módulo es la parte del orquestador: mira la base cada 30 s, decide si un
recordatorio venció y lo publica para que lo diga el satélite.

Tres piezas, y las tres son funciones o clases pequeñas y probables:

  - `siguiente_vez`: el cálculo puro de cuándo toca, en hora de Mazatlán. No toca
    la base ni el reloj.
  - `Satelites`: qué satélites están conectados, en memoria. Hace falta porque el
    orquestador no lo sabía: solo reaccionaba a la transición para repetir el gas.
  - `procesar_vencidos` / `entregar_pendientes` / `expirar_pendientes`: el ciclo.

## Por qué un ciclo y no un planificador

`proximo_en` vive en SQLite y es la única verdad de cuándo toca. Un planificador de
terceros (APScheduler) guardaría los trabajos en memoria, y para sobrevivir a un
reinicio habría que darle un almacén persistente: exactamente una segunda copia de
`proximo_en` que se puede desincronizar. Revisar la base cada 30 s es más simple,
no depende de una librería y sobrevive a los reinicios sin nada especial.

## Cómo se garantiza no decir dos veces el mismo vencimiento

La tabla `recordatorio_entregas` tiene la clave primaria `(recordatorio_id,
toca_en)`, y el ciclo hace un `INSERT OR IGNORE` antes de publicar. Aunque el ciclo
se solape consigo mismo, o el proceso se reinicie justo entre insertar y publicar,
el segundo intento de insertar el mismo vencimiento no entra. `obtener_entrega` es
lo que corta el paso en la siguiente vuelta.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import textos

registro = logging.getLogger("recordatorios")

# La zona de la casa. Mazatlán no usa horario de verano desde 2022, así que el
# desfase es -7 todo el año; aun así el cálculo se hace con `zoneinfo` y no
# restando 7 horas a mano, para que un cambio futuro de reglas no deje la hora de
# los recordatorios corrida sin que nadie lo note.
ZONA = ZoneInfo("America/Mazatlan")


# =============================================================================
# El cálculo de la próxima vez
# =============================================================================

def _epoch_local(anio: int, mes: int, dia: int, hora_min: int, zona=ZONA) -> int:
    """El epoch UTC de una fecha/hora LOCAL de `zona`, con la hora en minutos."""
    return int(datetime(anio, mes, dia, hora_min // 60, hora_min % 60,
                        tzinfo=zona).timestamp())


def _dias(texto) -> frozenset:
    """Los días ISO de la columna `dias` ('1,3,5') como un conjunto."""
    if not texto:
        return frozenset()
    return frozenset(int(p) for p in str(texto).split(",") if p.strip())


def siguiente_vez(recordatorio, desde_epoch: int, zona=ZONA):
    """La próxima vez que toca un recordatorio, o `None` si ya no toca.

    Función pura: no toca la base ni el reloj, recibe `desde_epoch` y la zona. Eso
    es lo que la hace fácil de probar con fechas fijas, como las reglas puras del
    satélite.

    `desde_epoch` es el instante desde el que buscar. La búsqueda es ESTRICTA
    (siempre devuelve un instante POSTERIOR), y eso es lo que impide que un
    recordatorio se dispare dos veces seguidas por quedarse su `proximo_en` en el
    mismo minuto. Quien la llama pasa el mayor entre el vencimiento que acaba de
    procesar y AHORA, para que un proceso que estuvo caído no se ponga a decir los
    días que se perdió uno por uno: salta directo a la próxima vez futura.

    - `una_vez`: la fecha y la hora del recordatorio; `None` si ya pasaron.
    - `diario`: la misma hora, hoy si aún no pasó o mañana si ya pasó.
    - `semanal`: el próximo día marcado en `dias` (ISO, 1 = lunes), a la hora.
    """
    hora_min = int(recordatorio["hora_min"])
    repeticion = recordatorio["repeticion"]

    if repeticion == "una_vez":
        anio, mes, dia = (int(p) for p in str(recordatorio["fecha"]).split("-"))
        candidato = _epoch_local(anio, mes, dia, hora_min, zona)
        return candidato if candidato > desde_epoch else None

    local = datetime.fromtimestamp(desde_epoch, tz=zona)

    if repeticion == "diario":
        candidato = _epoch_local(local.year, local.month, local.day, hora_min, zona)
        if candidato <= desde_epoch:
            manana = local + timedelta(days=1)
            candidato = _epoch_local(manana.year, manana.month, manana.day,
                                     hora_min, zona)
        return candidato

    if repeticion == "semanal":
        dias = _dias(recordatorio["dias"])
        if not dias:
            return None
        for adelante in range(0, 8):
            objetivo = local + timedelta(days=adelante)
            if objetivo.isoweekday() not in dias:
                continue
            candidato = _epoch_local(objetivo.year, objetivo.month, objetivo.day,
                                     hora_min, zona)
            if candidato > desde_epoch:
                return candidato
        return None

    # Un `repeticion` que no es ninguno de los tres no debería existir: la API lo
    # valida. Si llega, no se puede calcular y se trata como «ya no toca».
    registro.warning("recordatorio con repeticion desconocida: %s", repeticion)
    return None


# =============================================================================
# El registro de satélites conectados
# =============================================================================

class Satelites:
    """Qué satélites están conectados, en memoria.

    Hace falta porque el orquestador no lo sabía. `alertas.al_estado_satelite` solo
    reaccionaba a la transición a conectado para repetir el gas, y descartaba los
    mensajes retenidos; ahora cada mensaje de estado, retenido o no, actualiza este
    registro. Un retenido con `conectado:true` que llega al suscribirse es lo que
    «siembra» el registro con quien ya estaba conectado antes de arrancar.

    Se escribe desde el hilo de red de paho (los `on_message`) y se lee desde el
    hilo del ciclo de recordatorios, así que lleva su propio cerrojo: un
    diccionario que un hilo recorre mientras otro lo modifica lanza
    «dictionary changed size during iteration».
    """

    def __init__(self):
        self._cerrojo = threading.Lock()
        self._estado = {}

    def actualizar(self, client_id: str, conectado: bool) -> None:
        with self._cerrojo:
            self._estado[client_id] = bool(conectado)

    def hay_conectado(self) -> bool:
        with self._cerrojo:
            return any(self._estado.values())

    def conectados(self) -> list:
        with self._cerrojo:
            return [cid for cid, valor in self._estado.items() if valor]


# =============================================================================
# La publicación
# =============================================================================

def publicar_recordatorio(publicar, texto: str, ahora: int, destino=None) -> bool:
    """Publica el recordatorio hablado. Devuelve si se publicó.

    Replica el patrón de `alertas.anunciar`: tipo `recordatorio`, `ts` de ahora, y
    `destino` solo si va dirigido a un satélite concreto. Sin `destino` lo dicen
    todos, que es el caso normal (había un satélite conectado a la hora).

    Nunca lanza: un fallo al publicar no puede tumbar el ciclo. Devuelve `False`
    para que quien llama sepa que el vencimiento NO quedó dicho y pueda
    reintentarlo en la próxima vuelta en vez de darlo por entregado.
    """
    if publicar is None:
        return False
    carga = {"texto": textos.recordatorio_hablado(texto), "tipo": "recordatorio",
             "ts": int(ahora)}
    if destino is not None:
        carga["destino"] = destino
    try:
        publicar(config.TOPICO_SATELITE_DECIR, carga, qos=1)
    except Exception as error:  # noqa: BLE001
        registro.error("no se pudo publicar el recordatorio: %s", error)
        return False
    registro.info("recordatorio publicado en satelite/decir%s",
                  " (destino %s)" % destino if destino else "")
    return True


# =============================================================================
# El ciclo
# =============================================================================

def _avanzar(base, fila, toca_en: int, ahora: int) -> None:
    """Pasa el recordatorio a su próxima vez, o lo cierra si ya no toca.

    El cálculo arranca en `max(toca_en, ahora)`, y no en `toca_en`, para que un
    ciclo que corre después de una caída larga no se ponga a emitir un recordatorio
    diario por cada día perdido: salta directo al próximo futuro. El vencimiento que
    se acaba de procesar queda igual en `recordatorio_entregas`, así que no se
    pierde el rastro de que ese día tocó.
    """
    proximo = siguiente_vez(fila, max(int(toca_en), ahora))
    if proximo is None:
        base.cerrar_recordatorio(fila["id"], ahora)
    else:
        base.avanzar_recordatorio(fila["id"], proximo)


def procesar_vencidos(base, publicar, satelites, ahora: int = None) -> int:
    """Una vuelta del ciclo. Devuelve cuántos vencimientos se procesaron.

    Por cada recordatorio activo cuyo `proximo_en` ya venció:
      - Si ya tiene una entrega para ese vencimiento, se salta (ya se dijo, o
        quedó pendiente y lo resolverá `entregar_pendientes`/`expirar_pendientes`).
      - Si hay algún satélite conectado, se publica SIN destino, se marca
        `entregado` y se avanza.
      - Si no hay ninguno, se marca `pendiente` y se avanza. El pendiente se dirá
        al conectarse un satélite dentro de la ventana, o se marcará `no_entregado`.
    """
    if ahora is None:
        ahora = int(time.time())
    expirar_pendientes(base, ahora)

    hay = satelites is not None and satelites.hay_conectado()
    procesados = 0
    for fila in base.recordatorios_vencidos(ahora):
        toca = fila["proximo_en"]
        if base.obtener_entrega(fila["id"], toca) is not None:
            continue
        if hay:
            if not publicar_recordatorio(publicar, fila["texto"], ahora):
                # No se pudo publicar: no se marca nada y se reintenta en la
                # próxima vuelta. Perder un recordatorio es peor que repetir una
                # vuelta.
                continue
            base.registrar_entrega(fila["id"], toca, "entregado", ahora, None)
        else:
            base.registrar_entrega(fila["id"], toca, "pendiente")
            registro.info("recordatorio %s vencio sin satelite conectado; queda "
                          "pendiente", fila["id"])
        _avanzar(base, fila, toca, ahora)
        procesados += 1
    return procesados


def entregar_pendientes(base, publicar, destino: str, ahora: int = None) -> int:
    """Dice a `destino` los recordatorios pendientes dentro de la ventana.

    Se llama cuando un satélite PASA a conectado. Los pendientes más viejos que la
    ventana no se dicen (los marca `no_entregado` la propia `expirar_pendientes`,
    que también corre aquí por si el ciclo no ha pasado todavía). El texto va con
    `destino` a ese satélite, para que no lo digan los demás.
    """
    if ahora is None:
        ahora = int(time.time())
    expirar_pendientes(base, ahora)
    limite = ahora - config.VENTANA_RECORDATORIO_PENDIENTE_S
    dichas = 0
    for fila in base.entregas_pendientes_recientes(limite):
        rec = base.obtener_recordatorio(fila["recordatorio_id"])
        if rec is None:
            continue
        if publicar_recordatorio(publicar, rec["texto"], ahora, destino=destino):
            base.marcar_entrega_entregado(fila["recordatorio_id"], fila["toca_en"],
                                          ahora, destino)
            dichas += 1
    if dichas:
        registro.info("satelite %s conectado: recordatorios pendientes que se le "
                      "dicen: %d", destino, dichas)
    return dichas


def expirar_pendientes(base, ahora: int = None) -> int:
    """Marca `no_entregado` los pendientes que ya pasaron la ventana.

    Corre en cada vuelta del ciclo y no solo al conectarse un satélite: si no,
    un pendiente cuyo satélite nunca llega se quedaría `pendiente` para siempre, y
    la pantalla lo mostraría como si fuera a decirse todavía.
    """
    if ahora is None:
        ahora = int(time.time())
    limite = ahora - config.VENTANA_RECORDATORIO_PENDIENTE_S
    vencidas = base.entregas_pendientes_anteriores(limite)
    for fila in vencidas:
        base.marcar_entrega_no_entregado(fila["recordatorio_id"], fila["toca_en"])
    if vencidas:
        registro.info("recordatorios marcados como no entregados por vencer la "
                      "ventana: %d", len(vencidas))
    return len(vencidas)
