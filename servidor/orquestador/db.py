"""Todo lo que toca la base de datos.

Una conexión, funciones simples y ninguna lógica de negocio. La deduplicación y el
cálculo del próximo intento están en `alertas.py`; aquí solo se ejecuta SQL.

Dos detalles de SQLite que importan y que no son evidentes:

**`check_same_thread=False` y un `Lock`.** El trabajador de la cola corre en un
hilo aparte del que recibe de MQTT. SQLite no permite que dos hilos usen la misma
conexión a la vez, y encima Python lanza la excepción si dos hilos la usan sin
decirle que es correcto. Por eso el `Lock`: las dos cosas.

**`PRAGMA journal_mode=WAL`.** El orquestador escribe mucho (una fila por cada id
que ve) mientras alguien está leyendo el historial. Con el modo normal, una
escritura bloquea a todos los lectores; con WAL, los lectores no se bloquean. No
es una optimización: sin esto, mirar el historial mientras llegan alertas
falla con "database is locked".

Sobre los `INSERT OR IGNORE`: son la base de la deduplicación, y funcionan por lo
que devuelven. Devuelven `cursor.rowcount`, que es 1 si insertaron y 0 si la fila
ya existía. Por eso el código mira `rowcount` y no "si no hay error".
"""

import logging
import os
import sqlite3
import threading
import time

import config

registro = logging.getLogger("db")

_candado = threading.Lock()

RUTA_ESQUEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "esquema.sql")


def leer_esquema() -> str:
    """Lee `esquema.sql` de disco.

    No es un modulo de Python, es SQL, y no se importa: se lee. La razón es que
    un archivo .sql se puede abrir con cualquier cliente y se lee igual, y en un
    proyecto donde una parte del esquema va a cambiar con migraciones, tener el
    esquema en un archivo de texto es mas util que tenerlo dentro de un .py.

    El archivo va junto al codigo en la imagen, y se lee solo al arrancar, asi
    que un fallo aqui es un fallo de la construccion de la imagen, no algo que
    aparezca a mitad de servicio.
    """
    with open(RUTA_ESQUEMA, encoding="utf-8") as archivo:
        return archivo.read()


class BaseDatos:
    def __init__(self, ruta: str = None):
        self.ruta = ruta or config.RUTA_DB
        carpeta = os.path.dirname(self.ruta)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        self.conexion = sqlite3.connect(
            self.ruta, check_same_thread=False, timeout=30.0
        )
        self.conexion.row_factory = sqlite3.Row
        with _candado:
            self.conexion.execute("PRAGMA journal_mode=WAL")
            self.conexion.execute("PRAGMA synchronous=NORMAL")
            self.conexion.execute("PRAGMA busy_timeout=30000")
            self.conexion.executescript(leer_esquema())
            self._asegurar_columnas()
            self.conexion.commit()

    def _asegurar_columnas(self) -> None:
        """Agrega las columnas que falten, sin borrar nada.

        `CREATE TABLE IF NOT EXISTS` no cambia una tabla que ya existe, asi que una
        base creada por una version anterior se quedaria sin las columnas nuevas.
        Como la base vive en un volumen persistente, "borrarla y empezar de cero"
        no es una opcion: perderia el historial de alertas y el registro de ids ya
        vistos.

        La migracion es de una sola columna y es idempotente: si ya existe, el
        `ALTER TABLE` no se intenta. Cuando el esquema cambie de verdad, esto
        tiene que crecer a una tabla de migraciones con su version.
        """
        columnas = {
            fila["name"] for fila in
            self.conexion.execute("PRAGMA table_info(alertas)").fetchall()
        }
        if "resuelta_en" not in columnas:
            registro.info("la tabla alertas no tenia resuelta_en; se agrega")
            self.conexion.execute(
                "ALTER TABLE alertas ADD COLUMN resuelta_en INTEGER"
            )

    def cerrar(self) -> None:
        with _candado:
            self.conexion.close()

    # --- Deduplicación -----------------------------------------------------

    def registrar_id(self, id_alerta: str, tipo: str) -> bool:
        """¿Es un id que no hemos visto antes?

        Devuelve `True` si es nuevo y lo inserta. Devuelve `False` si ya existía,
        y en ese caso no inserta nada.

        El `INSERT OR IGNORE` es atómico: no hay ventana entre "comprobar" e
        "insertar". Por eso se usa una sola sentencia en lugar de un `SELECT`
        seguido de un `INSERT`, que dejaría que dos hilos con el mismo mensaje
        insertaran los dos.
        """
        with _candado:
            cursor = self.conexion.execute(
                "INSERT OR IGNORE INTO ids_procesados (id, tipo, recibido_en) "
                "VALUES (?, ?, ?)",
                (id_alerta, tipo, int(time.time())),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    # --- Cola de alertas ---------------------------------------------------

    def insertar_alerta(self, id_alerta: str, tipo: str, carga_json: str) -> bool:
        """Mete una alerta en la cola. Devuelve `False` si ya estaba.

        El `id` es clave primaria en `alertas`, así que aunque el `INSERT OR
        IGNORE` de `ids_procesados` hubiera fallado, esta sentencia tampoco
        duplicaría la fila.
        """
        ahora = int(time.time())
        purgable = ahora + config.DIAS_PURGA_ALERTAS * 86400
        with _candado:
            cursor = self.conexion.execute(
                "INSERT OR IGNORE INTO alertas "
                "(id, tipo, carga_json, estado, intentos, proximo_intento_en, "
                " creado_en, purgable_en) "
                "VALUES (?, ?, ?, 'pendiente', 0, ?, ?, ?)",
                (id_alerta, tipo, carga_json, ahora, ahora, purgable),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def tomar_pendientes(self, ahora: int = None, limite: int = 10) -> list:
        """Las alertas que tocan intento ahora.

        Filtra por `estado = 'pendiente'` y por `proximo_intento_en`. Sin el
        segundo filtro, una alerta que falló hace un segundo volvería a intentarse
        en cada vuelta del bucle, y con eso se golpearía la API de Telegram.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            return list(
                self.conexion.execute(
                    "SELECT * FROM alertas WHERE estado = 'pendiente' "
                    "AND proximo_intento_en <= ? "
                    "ORDER BY proximo_intento_en LIMIT ?",
                    (ahora, limite),
                )
            )

    def marcar_enviada(self, id_alerta: str, ahora: int = None) -> None:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE alertas SET estado = 'enviada', enviado_en = ?, "
                "ultimo_error = NULL WHERE id = ?",
                (ahora, id_alerta),
            )
            self.conexion.commit()

    def marcar_fallida(self, id_alerta: str, intentos: int, proximo: int,
                       error: str) -> None:
        """Anota un fallo de envío y dice cuándo volver a intentarlo.

        La fila se queda en `pendiente` SIEMPRE. No hay ningún camino por el que un
        fallo de red lleve una alerta a `descartada`, y eso es deliberado: la
        versión anterior gastaba cinco intentos y marcaba `descartada`, con lo que
        un corte de Internet de unos minutos convertía un aviso de gas en algo que
        no se mandaba nunca y no volvía a intentarse. La alerta se reintenta para
        siempre, con la espera creciente de `config.REINTENTO_SEGUNDOS`.

        Lo único que se acota es el `intentos`, para el aviso horario de las alertas
        que llevan más de un día sin poder salir, y el error, que se guarda recortado
        para que un mensaje enorme no llene la base.
        """
        with _candado:
            self.conexion.execute(
                "UPDATE alertas SET estado = 'pendiente', intentos = ?, "
                "proximo_intento_en = ?, ultimo_error = ? WHERE id = ?",
                (intentos, proximo, error[:500], id_alerta),
            )
            self.conexion.commit()

    def marcar_descartada(self, id_alerta: str, intentos: int, motivo: str) -> None:
        """Descarta una alerta que NO se puede mandar nunca.

        La reserva de `descartada` es una sola: una fila cuya `carga_json` no se
        puede leer. Reintentar esa alerta cada cinco minutos para siempre no
        arregla nada y va llenando el log. Un fallo de red nunca llega aquí.
        """
        with _candado:
            self.conexion.execute(
                "UPDATE alertas SET estado = 'descartada', intentos = ?, "
                "ultimo_error = ? WHERE id = ?",
                (intentos, motivo[:500], id_alerta),
            )
            self.conexion.commit()

    def marcar_resuelta(self, id_alerta: str, ahora: int = None) -> bool:
        """Marca la alerta como atendida por la familia (P17).

        Se llama desde el botón "Ya lo atendí". Devuelve `True` si la alerta
        existía y no estaba ya resuelta, para que el bot sepa si tiene que
        contestar algo o si el botón ya no hacía falta.

        Importante: esta actualización es la que impide que la cola vuelva a
        mandar la alerta. `marcar_enviada` es lo que evita el segundo envío de la
        misma alerta; esto es lo que cierra el círculo, porque una alerta enviada
        sigue en estado `enviada` y sin resolver, y sin esto no habría forma de
        saber que alguien la atendió.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE alertas SET estado = 'resuelta', resuelta_en = ? "
                "WHERE id = ? AND estado != 'resuelta'",
                (ahora, id_alerta),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def obtener_alerta(self, id_alerta: str):
        with _candado:
            return self.conexion.execute(
                "SELECT * FROM alertas WHERE id = ?", (id_alerta,)
            ).fetchone()

    def hay_ayuda_reciente(self, ahora: int = None) -> bool:
        """¿Hubo una alerta de ayuda en los últimos `ANTIRREBOTE_AYUDA_S`?

        Es el segundo filtro del antirrebote de ayuda (P05). El primero está en el
        ESP32 y no cubre el caso de la voz, en el que la alerta la publica el
        servidor. Sin este filtro, «Alexa, necesito ayuda» dicho dos veces en 30 s
        llegaría al familiar como dos mensajes, y la prueba P05 pide una sola vez.

        Solo mira alertas `enviadas` o `resueltas`, no las `descartadas` por otros
        motivos, y mira `creado_en` y no el intento de envío: la ventana que
        importa es la del disparo, que es lo que el ESP32 también cuenta.
        """
        if ahora is None:
            ahora = int(time.time())
        limite = ahora - config.ANTIRREBOTE_AYUDA_S
        with _candado:
            cursor = self.conexion.execute(
                "SELECT 1 FROM alertas WHERE tipo = 'ayuda' "
                "AND creado_en >= ? LIMIT 1",
                (limite,),
            )
            return cursor.fetchone() is not None

    def listar_pendientes_antigas(self, antiguedad: int, ahora: int = None) -> list:
        """Alertas pendientes que llevan más de `antiguedad` segundos sin salir.

        La consulta la hace el trabajador de la cola una vez por hora, para poder
        decir por log que hay una alerta de gas atascada. Sin ella, una cola que no
        puede mandar (token caducado, chat borrado, API caída) no da ninguna señal
        de que exista, y eso es exactamente el fallo que no puede pasar inadvertido.
        """
        if ahora is None:
            ahora = int(time.time())
        limite = ahora - antiguedad
        with _candado:
            return list(
                self.conexion.execute(
                    "SELECT * FROM alertas WHERE estado = 'pendiente' "
                    "AND creado_en <= ? ORDER BY creado_en",
                    (limite,),
                )
            )

    def contar_por_estado(self) -> dict:
        with _candado:
            filas = self.conexion.execute(
                "SELECT estado, COUNT(*) AS total FROM alertas GROUP BY estado"
            )
        return {fila["estado"]: fila["total"] for fila in filas}

    def listar_recientes(self, limite: int = 20, tipo: str = None) -> list:
        """El historial, para el comando `/historial` del bot y para las pruebas."""
        with _candado:
            if tipo:
                return list(
                    self.conexion.execute(
                        "SELECT * FROM alertas WHERE tipo = ? "
                        "ORDER BY creado_en DESC LIMIT ?",
                        (tipo, limite),
                    )
                )
            return list(
                self.conexion.execute(
                    "SELECT * FROM alertas ORDER BY creado_en DESC LIMIT ?",
                    (limite,),
                )
            )

    # --- Purgas ------------------------------------------------------------

    def purgar(self, ahora: int = None) -> tuple:
        """Borra lo viejo. Devuelve cuántos borró de cada tabla.

        Los ids se van a los 7 días y las alertas a los 30, cada una con su propia
        fecha en su propia fila. Por eso `purgable_en` está en `alertas` y el
        filtro de `ids_procesados` usa `recibido_en + 7 días`.

        No hay llave foránea entre las dos tablas, así que borrar los ids no
        arrastra ninguna alerta: es justo lo que hace que la purga se pueda hacer
        en dos `DELETE` independientes. Las alertas se borran por su `purgable_en`
        y punto, estén en el estado que estén.
        """
        if ahora is None:
            ahora = int(time.time())
        con_ids = ahora - config.DIAS_PURGA_IDS * 86400
        with _candado:
            cursor_ids = self.conexion.execute(
                "DELETE FROM ids_procesados WHERE recibido_en < ?", (con_ids,)
            )
            borrados_ids = cursor_ids.rowcount
            cursor_alertas = self.conexion.execute(
                "DELETE FROM alertas WHERE purgable_en < ?", (ahora,)
            )
            borrados_alertas = cursor_alertas.rowcount
            self.conexion.commit()
        return borrados_ids, borrados_alertas
