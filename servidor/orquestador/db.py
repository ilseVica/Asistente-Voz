"""Todo lo que toca la base de datos.

Una conexión, funciones simples y ninguna lógica de negocio. La deduplicación y el
cálculo del próximo intento están en `alertas.py`; aquí solo se ejecuta SQL.

## Una alerta, N envios

La diferencia importante con la versión anterior es que aquí hay dos tablas de
trabajo y no una. `alertas` es lo que pasó; `envios` es a quién hay que mandárselo,
una fila por familiar, con **sus propios** intentos y su **propio** próximo intento.

Eso no es una florituras para cuando haya varios familiares: es lo que evita que un
teléfono sin cobertura retrase un aviso de gas para los demás. Con los reintentos
en la tabla de alertas, el móvil apagado haría esperar a cada alerta nueva hasta que
alguien lo encendiera, y el familiar que sí tenía cobertura se enteraría más tarde.

Por eso el estado de un aviso no está guardado en ninguna columna: sale de sus
filas de `envios` (`db.CASE_ESTADO`). Dos sitios donde guardar la verdad son dos
sitios donde puede estar equivocados.

## `chat_id` no sale entero al log

Un `chat_id` de Telegram es el número de una persona, y `docker logs` se lee con
cualquiera que tenga acceso al servidor. `enmascarar_chat` deja los ultimos cuatro
dígitos, que es lo justo para distinguir a un familiar de otro en una casa con dos,
y el entero sale solo por la consola del operador (`python -m registro familiares`).

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

# Marca de un envio que fallo de forma permanente (chat no encontrado, bot bloqueado,
# etc.): no se reintenta. Es un valor centinela en `proximo_intento_en`, porque esa
# columna es `NOT NULL` y no se cambia el esquema. `tomar_envios_pendientes` y el
# aviso horario lo excluyen, asi que el envio no vuelve a intentarse ni avisa cada
# hora como si estuviera atascado.
ENVIO_FALLIDO_PERMANENTE = 2147483647

# La versión del esquema, en `PRAGMA user_version`. Es lo que hace que una migración
# corra una vez y no en cada arranque.
#
# Antes no hacia falta: el único cambio de esquema era agregar `resuelta_en`, que se
# hacia con un `ALTER TABLE` idempotente ("si no existe la columna, agregala"). Eso
# bastaba mientras las migraciones fueran de una columna. Esta vez el cambio es de
# otra forma: hay una tabla nueva y hay que REPARTIR las filas que ya habia entre
# dos, y eso no se puede repetir sin duplicar. Un número de versión lo dice sin
# ambigüedad, y no necesita una tabla nueva ni una convención con la base.
#
# La version 2 (panel web) no reparte nada: son dos tablas nuevas que crea
# `esquema.sql` con `CREATE TABLE IF NOT EXISTS`, asi que en una base que ya estaba
# en la 1 se crean igual y no hay ni datos que mover. Aun asi sube la version, para
# que quede escrito cuando paso y para que el log de arranque diga la verdad.
#
# La version 3 agrega `mensaje_id` y `editado_en` a `envios`, que es con lo que el
# aviso de Telegram se reescribe cuando la alerta se atiende desde el panel. Como
# son dos columnas nuevas en una tabla vieja, si hace falta el `ALTER TABLE` idem-
# potente de siempre: en una base que ya esta en la 2, `esquema.sql` no toca nada
# (`CREATE TABLE IF NOT EXISTS` sobre una tabla que ya existe) y las columnas solo
# pueden salir de ahi.
#
# La version 4 agrega `activo` a `familiares`: un registro se puede DESACTIVAR sin
# borrarlo. La columna tiene `DEFAULT 1` para que los familiares que ya estan en la
# tabla sigan recibiendo alertas sin tocar sus filas, y es otra columna nueva en
# una tabla vieja, asi que vuelve a ser el `ALTER TABLE` idempotente. Sin ella, un
# chat ficticio como la «Prueba Publica» de P20 haria que el fan out le creara un
# envio por cada alerta y el trabajador lo reintentaria para siempre contra algo
# que no existe.
#
# La version 5 (recordatorios) son dos tablas nuevas y dos indices, igual que la 2
# (panel web): las crea `esquema.sql` con `CREATE TABLE IF NOT EXISTS` / `CREATE
# INDEX IF NOT EXISTS`, que ya se ejecuto entero antes de llegar a `_migrar`, asi
# que en una base vieja se crean igual y no hay datos que mover. No tiene funcion
# propia por el mismo motivo que la 1->2, que esta explicado mas abajo.
#
# La version 6 (inactividad) es UNA tabla nueva, `configuracion`, otra vez del tipo
# «la crea `esquema.sql` antes de migrar, no hay datos que mover»: en una base que
# ya estaba en la 5 se crea vacia, y las dos claves que use el servicio se insertan
# cuando hagan falta (el valor por defecto NO se escribe al migrar, lo devuelve
# `db.tiempo_inactividad()` cuando la clave no esta). Tampoco tiene funcion propia.
#
# La version 8 agrega `resuelto_por` a `alertas`: el nombre del familiar (NUNCA un
# `chat_id`) que atendio la alerta, para que la linea de tiempo del panel pueda
# decir «Atendida por Ana» en vez de solo «Atendida». Las filas que ya existen
# quedan con `NULL`, que es la verdad historica: no se sabe quien atendio algo de
# antes, y rellenarlo con un nombre inventado seria peor que dejarlo vacio. Otra
# columna nueva en una tabla vieja, asi que otra vez el `ALTER TABLE` idempotente
# de la 2->3 y de la 4.
VERSION_ESQUEMA = 9

# La marca que deja la migración en `alertas.estado` para decir «esta fila ya se
# repartio», antes de borrar la columna entera. Un valor y no un `NULL`, porque en
# el esquema de un solo destino `estado` es `NOT NULL`; el comentario de la
# migración explica por que importa.
ESTADO_MIGRADO = "repartido-v1"

# El estado de una alerta, tal como se ve en `/alertas`. No se guarda: se calcula.
#
# El orden de los `WHEN` importa. `descartada_en` va primero porque una alerta
# descartada puede tener `resuelto_en` puesto (alguien pulsa un boton de un aviso
# viejo que se había descartado por un `carga_json` ilegible), y el resultado que
# interesa es "no se pudo mandar". `resuelto_en` va segundo porque es lo que el
# familiar quiere ver. Y el ultimo `ELSE` (sin envios) es `pendiente`: un aviso que
# no ha salido de la cola no está enviada, por mucha intencion que tenga.
#
# `a` es el alias de la tabla de alertas, y `e` el de envios. Las dos consultas que
# lo usan tienen que pondernos esos alias, de ahí que sea una constante y no un
# trozo de SQL repetido en tres sitios.
CASE_ESTADO = """
    CASE
        WHEN a.descartada_en IS NOT NULL THEN 'descartada'
        WHEN a.resuelto_en IS NOT NULL THEN 'resuelta'
        WHEN EXISTS (SELECT 1 FROM envios e
                     WHERE e.alerta_id = a.id AND e.enviado_en IS NULL)
            THEN 'pendiente'
        -- Se ofrecio a la lista y no queda nada sin entregar. normally habria un
        -- envio entregado; el `OR` de la ultima línea es para el caso de que no
        -- haya ninguno, que sale de la migración de una base del diseno de un solo
        -- destino sin `familia.json`: el aviso LLEGO (lo dice el estado que tenia),
        -- pero no se sabe a quien, asi que no hay fila en `envios`.
        --
        -- Sin ese `OR`, esas alertas salen «pendiente» en `/alertas` para siempre:
        -- no hay envio pendiente que las saque de ahí, y no se van a mandar nunca
        -- porque `fanout_hecho` esta a 1. Un aviso de gas marcado como pendiente
        -- sin fecha de fin no es un estado, es un aviso que no se apaga, y lo
        -- único que sugiere es que hay gas en casa ahora.
        WHEN EXISTS (SELECT 1 FROM envios e WHERE e.alerta_id = a.id)
             OR a.fanout_hecho = 1 THEN 'enviada'
        ELSE 'pendiente'
    END
"""


def enmascarar_chat(chat_id) -> str:
    """El `chat_id` que puede salir en un log: los ultimos cuatro digitos.

    Con dos familiares, `...4970` y `...1234` se distinguen sin publicar el numero
    entero. Con uno, el log sirve igual: dice que se registro y cuando.

    Si el id es corto (un `1` de prueba, o un id inventado en un test), se devuelve
    entero: enmascarar cuatro digitos de un id de un digito no oculta nada y deja
    el log menos legible.
    """
    texto = str(chat_id or "")
    if len(texto) <= 4:
        return texto
    return "..." + texto[-4:]


def leer_esquema() -> str:
    """Lee `esquema.sql` de disco.

    No es un modulo de Python, es SQL, y no se importa: se lee. La razón es que
    un archivo .sql se puede abrir con cualquier cliente y se lee igual, y en un
    proyecto donde una parte del esquema va a cambiar con migraciones, tener el
    esquema en un archivo de texto es mas útil que tenerlo dentro de un .py.

    El archivo va junto al código en la imagen, y se lee solo al arrancar, asi
    que un fallo aquí es un fallo de la construccion de la imagen, no algo que
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
            self._migrar()
            self.conexion.commit()

    def _migrar(self) -> None:
        """Lleva la base al esquema actual. Es idempotente.

        La versión 0 es la anterior a los familiares múltiples: `alertas` tenia
        `estado`, `intentos`, `proximo_intento_en` y `enviado_en`, y una sola fila
        por aviso, porque solo habia un destino. La versión 1 parte esos envios en
        la tabla `envios`, una fila por familiar, y deja el estado derivado.

        **La migración tiene que repartir, no copia.** Una base ya en marcha tiene
        avisos que se mandaron y avisos que quedaron en la cola. Si al avisar que
        se mando se hiciera una fila en `envios` para todos los familiares
        registrados en vez de para el que lo recibio, un familiar que se registra
        mañana se encontraría con que le reenvian el historico entero. Y si no se
        crearía esa fila, el aviso quedaría en `pendiente` para siempre y el
        trabajador de la cola lo mandaria otra vez. Las dos cosas están mal y se
        evitan haciendo laconversion una vez, con el familiar que hubiera en ese
        momento.

        Que se reparta y no se copie es tambien lo que hace segura la corrida: si el
        proceso se muere a mitad, `user_version` sigue en 0 y la migración vuelve a
        empezar desde una tabla que, si ya se llego a vaciar `estado`, se detecta
        como ya migrada. Por eso `estado` se vacía ANTES de llenar `envios`, y no al
        revés.
        """
        version = self.conexion.execute("PRAGMA user_version").fetchone()[0]
        if version >= VERSION_ESQUEMA:
            return

        if version == 0:
            self._migrar_a_familiares_multiples()

        # De la 1 a la 2 (panel web) no hay nada que hacer aqui, y es a proposito:
        # `panel_enlaces` y `panel_tokens` las crea `esquema.sql` con su `CREATE
        # TABLE IF NOT EXISTS`, que ya se ejecuto entero ANTES de llegar aqui, tanto
        # en una base nueva como en una que ya estaba en la 1. Por eso esta version
        # no tiene funcion propia: si le pusieramos una, acabaria haciendo en codigo
        # un `CREATE TABLE` que el archivo de esquema ya garantiza, y habria dos
        # sitios donde el panel se puede desincronizar del esquema.
        #
        # Lo que si tiene que quedar escrito es la version, para que el proximo
        # arranque no vuelva a pasar por aqui.

        # De la 2 a la 3: `envios` gana `mensaje_id` y `editado_en`. A diferencia
        # de la tabla del panel, estas columnas van DENTRO de una tabla que ya
        # existe, asi que `esquema.sql` no las puede crear en una base vieja y hace
        # falta el `ALTER TABLE` idempotente. En una base nueva no se entra aqui
        # (el `CREATE TABLE` del fichero ya trajo las columnas) y en una vieja el
        # `PRAGMA table_info` decide, con lo que la funcion se puede llamar las
        # veces que haga falta sin duplicar nada.
        if version < 3:
            self._migrar_envios_mensajes()

        # De la 3 a la 4: `familiares` gana `activo`. Igual que en la 2->3, la
        # columna va DENTRO de una tabla que ya existe, asi que en una base que ya
        # esta en la 3 el fichero de esquema no la puede crear y hace falta el
        # `ALTER TABLE` idempotente. En una base nueva el `CREATE TABLE` del fichero
        # ya trajo la columna, y en una vieja el `PRAGMA table_info` decide, con lo
        # que la funcion se puede llamar las veces que haga falta sin duplicar nada.
        if version < 4:
            self._migrar_familiares_activo()

        # De la 4 a la 5 (recordatorios) no hay nada que hacer aqui, y es a
        # proposito: `recordatorios` y `recordatorio_entregas` las crea
        # `esquema.sql` con su `CREATE TABLE IF NOT EXISTS`, que ya se ejecuto
        # entero ANTES de llegar aqui, tanto en una base nueva como en una que ya
        # estaba en la 4. Igual que la 1->2 (panel web): si le pusieramos una
        # funcion, acabaria haciendo en codigo un `CREATE TABLE` que el archivo de
        # esquema ya garantiza. Lo que si tiene que quedar escrito es la version.

        # De la 5 a la 6 (inactividad) tampoco: `configuracion` la crea
        # `esquema.sql` con su `CREATE TABLE IF NOT EXISTS`, por el mismo motivo
        # que las dos anteriores. En una base que ya estaba en la 5 nace vacia, y
        # eso es lo correcto: `db.tiempo_inactividad()` devuelve el valor de
        # fabrica de `config.py` mientras no haya clave, y `ultimo_movimiento`
        # tampoco existe, asi que el reloj arranca en la hora de arranque.

        # De la 6 a la 7 (códigos de acceso nuevos): nueva tabla `codigos_acceso`.
        # Se crea con IF NOT EXISTS en esquema.sql y aquí nos aseguramos de que
        # quede creada también en bases antiguas. No toca panel_enlaces/panel_tokens.
        if version < 7:
            self.conexion.execute("""
                CREATE TABLE IF NOT EXISTS codigos_acceso (
                    codigo_hash  TEXT NOT NULL PRIMARY KEY,
                    chat_id      TEXT NOT NULL,
                    creado_en    INTEGER NOT NULL,
                    expira_en    INTEGER NOT NULL,
                    intentos     INTEGER NOT NULL DEFAULT 0,
                    usado_en     INTEGER
                )
            """)
            self.conexion.execute("""
                CREATE INDEX IF NOT EXISTS idx_codigos_acceso_expira
                    ON codigos_acceso (expira_en)
            """)
            self.conexion.execute("""
                CREATE INDEX IF NOT EXISTS idx_codigos_acceso_chat
                    ON codigos_acceso (chat_id)
            """)

        # De la 7 a la 8: `alertas` gana `resuelto_por`, el nombre de quien
        # atendio. Igual que en la 2->3 y la 4, la columna va DENTRO de una tabla
        # que ya existe, asi que hace falta el `ALTER TABLE` idempotente con
        # `PRAGMA table_info`: en una base nueva `esquema.sql` ya la trajo, y en
        # una vieja esta funcion la agrega una sola vez. Las filas anteriores
        # quedan `NULL` a proposito (no se sabe quien atendio lo de antes).
        if version < 8:
            self._migrar_alertas_resuelto_por()

        # El indice de las alertas que esperan a un familiar se crea AQUI y no en
        # `esquema.sql`, y el motivo es el orden: `esquema.sql` se ejecuta entero
        # ANTES de migrar, y sobre una base ya existente su `CREATE TABLE IF NOT
        # EXISTS` no hace nada, asi que `fanout_hecho` todavia no existe cuando se
        # llega a las sentencia de indice. Un `CREATE INDEX ... (fanout_hecho)` en
        # el fichero de esquema aborta la apertura de la base con «no such column»
        # justo en las instalaciones que hay que migrar, que son las unicas que
        # importan. Aqui la columna ya existe, tanto en una base nueva como en una
        # migrada, y el `IF NOT EXISTS` lo deja hacer las dos veces sin quejarse.
        self.conexion.execute(
            "CREATE INDEX IF NOT EXISTS idx_alertas_esperando "
            "ON alertas (fanout_hecho) "
            "WHERE resuelto_en IS NULL AND descartada_en IS NULL"
        )

        # De la 8 a la 9 (chat de dos vias): tablas `conversacion` y
        # `conversacion_envios`, mas backfill de las alertas de los ultimos 30 dias
        # como entradas tipo alerta (para que el panel muestre lo mismo que ya esta
        # en Telegram). Las tablas las crea esquema.sql con IF NOT EXISTS; aqui se
        # aseguran en bases antiguas y se hace el backfill una sola vez.
        if version < 9:
            self.conexion.execute("""
                CREATE TABLE IF NOT EXISTS conversacion (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    creado_en       INTEGER NOT NULL,
                    tipo            TEXT NOT NULL CHECK(tipo IN ('mensaje','respuesta','alerta')),
                    autor           TEXT,
                    origen          TEXT CHECK(origen IS NULL OR origen IN ('panel','telegram')),
                    autor_chat_id   TEXT,
                    texto           TEXT,
                    estado          TEXT CHECK(estado IS NULL OR estado IN ('enviado','se_dijo','no_se_pudo_decir')),
                    estado_en       INTEGER,
                    alerta_id       TEXT,
                    notificado_en   INTEGER,
                    purgable_en     INTEGER NOT NULL
                )
            """)
            self.conexion.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversacion_creado
                    ON conversacion (creado_en)
            """)
            self.conexion.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversacion_alerta
                    ON conversacion (alerta_id)
            """)
            self.conexion.execute("""
                CREATE INDEX IF NOT EXISTS idx_conversacion_estado
                    ON conversacion (estado, creado_en)
            """)
            self.conexion.execute("""
                CREATE TABLE IF NOT EXISTS conversacion_envios (
                    conversacion_id INTEGER NOT NULL,
                    chat_id         TEXT NOT NULL,
                    mensaje_id      INTEGER,
                    editado_en      INTEGER,
                    creado_en       INTEGER NOT NULL,
                    PRIMARY KEY (conversacion_id, chat_id)
                )
            """)
            self.conexion.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_envios_pendientes
                    ON conversacion_envios (editado_en, mensaje_id)
            """)
            # Backfill: una entrada tipo alerta por cada alerta de los ultimos 30 dias.
            ahora = int(time.time())
            limite = ahora - 30 * 86400
            for fila in self.conexion.execute(
                    "SELECT id, creado_en, purgable_en FROM alertas "
                    "WHERE creado_en >= ?", (limite,)):
                existe = self.conexion.execute(
                    "SELECT 1 FROM conversacion WHERE alerta_id = ?",
                    (fila["id"],)).fetchone()
                if existe is None:
                    self.conexion.execute(
                        "INSERT INTO conversacion (creado_en, tipo, alerta_id, "
                        "purgable_en) VALUES (?, 'alerta', ?, ?)",
                        (fila["creado_en"], fila["id"],
                         fila["purgable_en"] if fila["purgable_en"] else
                         fila["creado_en"] + 30 * 86400),
                    )

        self.conexion.execute("PRAGMA user_version = %d" % VERSION_ESQUEMA)
        registro.info("base de datos migrada a la versión %d del esquema",
                      VERSION_ESQUEMA)

    def _migrar_envios_mensajes(self) -> None:
        """La migración de la versión 2 a la 3: las dos columnas de la reescritura.

        `mensaje_id` y `editado_en` en `envios`. No hay datos que mover: las dos
        columnas nacen vacias, que es donde tienen que empezar, porque ninguna fila
        de una base vieja tiene todavia un mensaje reescrito.

        El `PRAGMA table_info` es lo que hace que sea segura. En una base creada en
        limpio, `esquema.sql` ya puso las columnas antes de llegar aqui (su
        `CREATE TABLE` es el nuevo), y un segundo `ALTER TABLE` sobre columnas que
        existen fallaria con «duplicate column name» y dejaria la base a medias.
        """
        columnas = {fila["name"] for fila in
                    self.conexion.execute("PRAGMA table_info(envios)").fetchall()}
        for nombre in ("mensaje_id", "editado_en"):
            if nombre not in columnas:
                self.conexion.execute(
                    "ALTER TABLE envios ADD COLUMN %s INTEGER" % nombre
                )

    def _migrar_familiares_activo(self) -> None:
        """La migración de la versión 3 a la 4: `activo` en `familiares`.

        No hay datos que mover: la columna nace con `DEFAULT 1` (todos los
        familiares existentes quedan activos, que es el comportamiento de antes),
        y una fila inactiva solo puede salir del comando `inactivar`.

        El `PRAGMA table_info` es lo que la hace idempotente, igual que en la
        migración de la 2 a la 3: en una base creada en limpio, `esquema.sql` ya
        puso la columna al ejecutarse entero antes de llegar aqui, y un segundo
        `ALTER TABLE` sobre una columna que ya existe fallaria con «duplicate
        column name» y dejaria la base a medias.
        """
        columnas = {fila["name"] for fila in
                    self.conexion.execute("PRAGMA table_info(familiares)").fetchall()}
        if "activo" not in columnas:
            self.conexion.execute(
                "ALTER TABLE familiares ADD COLUMN activo INTEGER NOT NULL DEFAULT 1"
            )

    def _migrar_alertas_resuelto_por(self) -> None:
        """La migración de la versión 7 a la 8: `resuelto_por` en `alertas`.

        El nombre de quien atendio, para la linea de tiempo del panel («Atendida
        por Ana, 07:32»). Sin `DEFAULT` y con las filas existentes en `NULL`: lo
        de antes nadie lo guardo, y ponerle un nombre a algo que nadie registro
        seria inventar historia.

        El `PRAGMA table_info` la hace idempotente, igual que en las migraciones
        de la 2 a la 3 y de la 4: en una base creada en limpio, `esquema.sql` ya
        puso la columna al ejecutarse entero antes de llegar aqui.
        """
        columnas = {fila["name"] for fila in
                    self.conexion.execute("PRAGMA table_info(alertas)").fetchall()}
        if "resuelto_por" not in columnas:
            self.conexion.execute(
                "ALTER TABLE alertas ADD COLUMN resuelto_por TEXT"
            )

    def _migrar_a_familiares_multiples(self) -> None:
        """La migración de la versión 0 a la 1.

        La columna `estado` decide que hacer con cada fila:

          enviada     -> un envio marcado como enviado para el único familiar que
                         hubiera, con la hora que tuviera `enviado_en`.
          resuelta    -> lo mismo, y ademas `resuelto_en` en la alerta.
          descartada  -> `descartada_en` en la alerta, y sin envios: reenviar un
                         `carga_json` ilegible no lo arregla nunca.
          pendiente   -> un envio PENDIENTE para el único familiar que hubiera, con
                         los intentos y el próximo intento que ya tenia. Asi sigue
                         reintentando igual que antes, con la misma escalera.

        El caso "no había ningun familiar" tiene dos partes que no pueden
        escribirse igual, y confundirlas es un fallo que sale meses despues:

        **Una alerta que se quedo PENDIENTE** si se guarda sin envios y con
        `fanout_hecho = 0`, para que salga cuando alguien se registre. Es lo que ya
        prometia la versión anterior ("si alguien pone el token dentro de una hora,
        las alertas que se habian acumulado salen").

        **Una alerta `enviada` o `resuelta` sin familiar conocido** se guarda
        tambien sin envios, pero con `fanout_hecho = 1`. Llego a alguien en su dia; de
        eso dan fe el estado y la hora que trae la fila. Pero el esquema viejo
        no guardaba a quien, y no hay forma de reconstruirlo. Marcar esas filas como
        esperando un destinatario haría que el familiar que se registre manana
        recibiera el historico de la casa como si fueran alertas de ahora. Un replay
        del historico a un movil suena casi como una alerta real, y a las tres de
        la manana nadie mira el id antes de arrancar.

        Las columnas viejas (`estado`, `intentos`, `proximo_intento_en`,
        `enviado_en`) se borran al final. Se podrían dejar: sobran, pero una columna
        que ningun código lee y que dice "el estado" es una forma de que el proximo
        que lea esta tabla se fíe de ella. `DROP COLUMN` necesita SQLite 3.35 o
        superior, y la imagen del servicio va con una mucho mas nueva.
        """
        columnas = {
            fila["name"] for fila in
            self.conexion.execute("PRAGMA table_info(alertas)").fetchall()
        }
        if "estado" not in columnas:
            # Una base creada directamente con el esquema nuevo: no hay nada que
            # repartir. Pasa con las pruebas y con una instalación en limpio.
            return

        for nombre in ("resuelto_en", "descartada_en", "fanout_hecho"):
            if nombre not in columnas:
                if nombre == "fanout_hecho":
                    self.conexion.execute(
                        "ALTER TABLE alertas ADD COLUMN fanout_hecho INTEGER "
                        "NOT NULL DEFAULT 0"
                    )
                else:
                    self.conexion.execute(
                        "ALTER TABLE alertas ADD COLUMN %s INTEGER" % nombre
                    )

        # Si hay un `chat_id` de un familiar de la versión anterior, se copia a
        # `familiares` para que sus envios migren a nombre de alguien. Sin esta
        # lectura, los avisos de la base historica se quedarían sin destino y
        # alguien que se registrara recibiria el historico de la casa.
        chat_previo = self._chat_registrado_legacy()

        # La versión anterior guardaba la hora de resolucion en `resuelta_en` (en
        # femenino, concertando con «alerta») y esta la llama `resuelto_en`. Las dos
        # columnas están en el mismo sitio y significan lo mismo, y leer la que no
        # es no da error: da NULL, que se parece mucho a una verdad.
        #
        # El fallo concreto es que las nueve alertas resueltas de la instalación
        # real saldrían con la hora en que LLEGARON en lugar de la hora en que
        # alguien pulsó «Ya lo atendí». `/alertas` seguiría contando nueve
        # resueltas, asi que nadie vería nada raro: la fecha equivocada solo se ve
        # cuando alguien mira el historial preguntándose cuánto tardó en atender, que
        # es justo la pregunta que un historial de avisos tiene que responder.
        columna_resolucion = ("resuelta_en" if "resuelta_en" in columnas
                              else "resuelto_en")

        ahora = int(time.time())
        filas = self.conexion.execute(
            "SELECT id, tipo, estado, intentos, proximo_intento_en, enviado_en, "
            "COALESCE(%s, resuelto_en) AS resuelto_en, creado_en FROM alertas"
            % columna_resolucion
        ).fetchall()

        # Primero se marca TODA la tabla como ya repartida, antes de tocar una sola
        # fila. Es lo que hace segura la corrida: si el proceso se muere aquí, la
        # versión sigue en 0 pero no queda ninguna fila pendiente de repartir, y al
        # volver a arrancar esta función las ve a todas marcadas y no hace nada.
        # Es el único orden en que un corte a mitad no deja la base a medias.
        #
        # La marca es un VALOR, no un NULL, y no es casualidad: en el esquema de un
        # solo destino `estado`, `intentos` y `proximo_intento_en` son `NOT NULL`.
        # Un `SET estado = NULL` para decir «ya repartido» falla con «NOT NULL
        # constraint failed» en la primera fila, y el servicio no arranca nunca con
        # una base antigua. Hay que marcar con algo que quepa.
        self.conexion.execute(
            "UPDATE alertas SET estado = ? WHERE estado IS NOT NULL",
            (ESTADO_MIGRADO,),
        )

        for fila in filas:
            estado = fila["estado"] or "pendiente"
            if estado == ESTADO_MIGRADO:
                # Ya estaba repartida en una corrida anterior que se murio a mitad.
                # Los `INSERT OR IGNORE` de abajo ya la dejaron bien, y volver a
                # tratarla como pendiente le reiniciaria la escalera de reintentos.
                continue

            # Las marcas de la ALERTA se copian antes de mirar si hay familiar. Son
            # dos cosas distintas: «alguien pulso el boton» y «llego a alguien». Que
            # la segunda no se pueda reconstruir no puede borrarte la primera, y si
            # se guardan detras del `if chat_previo`, una base sin `familia.json`
            # deja las alertas resueltas como pendientes: `/alertas` dira que no se
            # ha atendido nada de todo lo que se atendio, que es un historial falso
            # en la direccion que mas duele (parece que nadie hizo nada).
            if estado == "resuelta":
                self.conexion.execute(
                    "UPDATE alertas SET resuelto_en = ? WHERE id = ?",
                    (fila["resuelto_en"] or fila["creado_en"], fila["id"]),
                )

            if estado == "descartada":
                self.conexion.execute(
                    "UPDATE alertas SET descartada_en = ?, fanout_hecho = 1 "
                    "WHERE id = ?",
                    (fila["creado_en"], fila["id"]),
                )
                continue

            if chat_previo is None:
                # Sin familiar registrado, el aviso no tiene a quien mandarse. Lo
                # único que decide si sigue esperando es si estaba PENDIENTE: un
                # aviso pendiente sale cuando se registre alguien, y uno que ya
                # había salido (enviada/resuelta) no se vuelve a ofrecer a nadie.
                self.conexion.execute(
                    "UPDATE alertas SET fanout_hecho = ? WHERE id = ?",
                    (0 if estado == "pendiente" else 1, fila["id"]),
                )
                continue

            # Con familiar conocido, la alerta se ofrecio a la lista en su dia:
            # se le da su envio y no vuelve a entrar en el fan out.
            self.conexion.execute(
                "UPDATE alertas SET fanout_hecho = 1 WHERE id = ?", (fila["id"],)
            )

            if estado == "pendiente":
                # Un aviso que se quedo en la cola sigue en la cola, con la misma
                # escalera de reintentos que tenia. Sus columnas viejas se copian
                # tal cual: reiniciar el contador de intentos haría que una alerta
                # que llevaba tres fallos volviera a insistir cada 15 segundos.
                self.conexion.execute(
                    "INSERT OR IGNORE INTO envios (alerta_id, chat_id, intentos, "
                    "proximo_intento_en, enviado_en, ultimo_error, creado_en) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                    (fila["id"], chat_previo, fila["intentos"] or 0,
                     fila["proximo_intento_en"] or 0, ahora),
                )
                continue

            # `enviada` y `resuelta`: en las dos el aviso LLEGO a alguien, asi que
            # se crea el envio como enviado. La marca de `resuelta` ya esta copiada
            # arriba, antes de mirar si hay familiar.
            self.conexion.execute(
                "INSERT OR IGNORE INTO envios (alerta_id, chat_id, intentos, "
                "proximo_intento_en, enviado_en, ultimo_error, creado_en) "
                "VALUES (?, ?, ?, 0, ?, NULL, ?)",
                (fila["id"], chat_previo, fila["intentos"] or 0,
                 fila["enviado_en"] or fila["creado_en"], ahora),
            )

        if chat_previo is not None:
            self.conexion.execute(
                "INSERT OR IGNORE INTO familiares (chat_id, nombre, registrado_en) "
                "VALUES (?, ?, ?)",
                (chat_previo, "", ahora),
            )

        # Los indices que usaban las columnas viejas se borran ANTES que las
        # columnas, y no es un detalle de orden: SQLite no deja quitar una columna
        # que un indice menciona, y falla con «error in index after drop column: no
        # such column: proximo_intento_en». Se para la base justo al final de una
        # migración que ya ha escrito todos los envios, que es el peor sitio para
        # que el servicio no arranque.
        #
        # El indice de la versión anterior era `idx_alertas_pendientes
        # (estado, proximo_intento_en)`. Los nombres se eligen consultando
        # `sqlite_master` y no con una lista fija: cualquier indice que mencione una
        # columna vieja da el mismo error, y adivinar los nombres es pedir que se
        # rompa en la instalación que todavia no se ha visto.
        #
        # `idx_alertas_tipo (tipo, creado_en)` se queda: son columnas que no se
        # borran, y `esquema.sql` lo crea en cada arranque.
        columnas_borradas = ("estado", "intentos", "proximo_intento_en",
                              "enviado_en", "resuelta_en")
        for indice in self.conexion.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = 'alertas' AND sql IS NOT NULL"
        ).fetchall():
            sql = indice["sql"] or ""
            if any(nombre in sql for nombre in columnas_borradas):
                registro.info("se borra el indice %s, que usaba columnas que ya no "
                              "existen", indice["name"])
                self.conexion.execute("DROP INDEX IF EXISTS %s" % indice["name"])

        for nombre in ("enviado_en", "proximo_intento_en", "intentos", "estado",
                       "resuelta_en"):
            # `resuelta_en` solo existe si venía de la versión anterior, y el
            # `DROP` de una columna que no esta ahi es un error. Se comprueba en vez
            # de asumir: borrar las cuatro viejas es lo que hace falta siempre, y
            # esta quinta solo a veces.
            if nombre in columnas:
                self.conexion.execute(
                    "ALTER TABLE alertas DROP COLUMN %s" % nombre
                )

        registro.info(
            "la cola paso de una fila por aviso a una fila por aviso y familiar; "
            "%d avisos conservados", len(filas),
        )

    def _chat_registrado_legacy(self):
        """El `chat_id` del `familia.json` viejo, si lo hay.

        Solo lo usa la migración. Vive aquí y no en `registro.py` porque es una
        lectura de la base: la migración tiene que pasar dentro de la misma
        transaccion que el resto, o un corte entre leer el archivo y escribir la
        tabla deja la base a medias sin que nada lo detecte.

        Se busca junto a LA BASE QUE SE ESTA MIGRANDO (`self.ruta`), no junto a
        `config.RUTA_DB`. Las dos son el mismo sitio en el servidor, y por eso esta
        distinción parece una mania hasta que se migra una base que no es la de
        `config` (una copia de una prueba, la de otro servicio): mirando la ruta de
        `config` la migración leeria el `familia.json` de la instalación de al lado,
        no encontraría ninguno, y los avisos del familiar de verdad se quedarian
        huerfanos sin que nada diga por que.
        """
        import registro as registro_modulo

        return registro_modulo.leer_familia_legacy(
            os.path.dirname(self.ruta)
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

    # --- Familiares ---------------------------------------------------------

    def registrar_familiar(self, chat_id: str, nombre: str = "") -> bool:
        """Da de alta un familiar. Devuelve si era nuevo.

        `False` significa que ese chat ya estaba, y no es un error: es el caso
        normal de que `/registro` llegue dos veces. No se actualiza el nombre ni la
        fecha en ese caso, para que `registrado_en` siga siendo cuando se dio de
        alta de verdad, y no cuando se escribio `/registro` por ultima vez.
        """
        with _candado:
            cursor = self.conexion.execute(
                "INSERT OR IGNORE INTO familiares (chat_id, nombre, registrado_en) "
                "VALUES (?, ?, ?)",
                (str(chat_id), nombre or "", int(time.time())),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def familiar_registrado(self, chat_id: str) -> bool:
        """¿Este chat recibe las alertas?

        Es la única definicion de "registrado" del sistema. La usan los tres sitios
        que decisiones: el silencio de `/registro`, si `/alertas` contesta, y si el
        boton "Ya lo atendi" se atiende. Que los tres pregunten lo MISMO es lo que
        evita el caso raro de un chat que puede ver el historial pero no resolver,
        o al revés.
        """
        with _candado:
            return self.conexion.execute(
                "SELECT 1 FROM familiares WHERE chat_id = ?", (str(chat_id),)
            ).fetchone() is not None

    def familiar_activo(self, chat_id: str) -> bool:
        """¿Este chat esta registrado Y activo?

        Es la puerta del codigo de acceso del panel: un familiar desactivado sigue
        en la base (y en la lista del operador) pero no puede pedir codigo. Es una
        pregunta distinta de `familiar_registrado` a proposito: el historial de
        alertas lo ve un desactivado, la entrada al panel no.
        """
        with _candado:
            return self.conexion.execute(
                "SELECT 1 FROM familiares WHERE chat_id = ? AND activo = 1",
                (str(chat_id),)
            ).fetchone() is not None

    def familiares_registrados(self) -> list:
        """Los familiares, del mas antiguo al mas reciente.

        El orden es el de alta, no el alfabetico: el primero en registrarse es el
        que mas tiempo lleva cuidando la casa, y en la cola de envios ese es el
        orden en que se avisa.

        La lista INCLUYE TAMBIEN LOS INACTIVOS: es la del operador, la que
        muestra que un chat esta registrado pero desactivado. Para saber a quien
        avisar esta `familiares_activos`; confundir las dos devolveria alertas a
        alguien desactivado.
        """
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM familiares ORDER BY registrado_en, chat_id"
            ))

    def familiares_activos(self) -> list:
        """Los familiares que reciben alertas AHORA (`activo = 1`).

        Es la lista de DESTINO del fan out (`insertar_alerta`). La diferencia con
        `familiares_registrados` es intencional y separada por el nombre: esa
        devuelve todo (es lo que ve el operador), esta solo los que hay que avisar.
        Un `WHERE activo = 1` en el fan out es la diferencia entre un chat que
        "esta apuntado" y un chat que "esta apuntado pero no le llega nada".
        """
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM familiares WHERE activo = 1 "
                "ORDER BY registrado_en, chat_id"
            ))

    def contar_familiares(self) -> int:
        """Cuantos chats hay en la tabla, ACTIVOS O NO.

        El número de "personas registradas" que ven el panel y el bot. Si contara
        solo los activos, una desactivacion "desapareceria" a alguien sin avisar;
        el estado se ve en la lista, no en el total.
        """
        with _candado:
            return self.conexion.execute(
                "SELECT COUNT(*) AS total FROM familiares"
            ).fetchone()["total"]

    def contar_familiares_activos(self) -> int:
        """Cuenta solo los familiares activos.

        Es lo que muestra el panel: cuanta gente puede usar el panel ahora mismo.
        Un familiar desactivado sigue en la base y en la lista del operador, pero
        no cuenta para este numero.
        """
        with _candado:
            return self.conexion.execute(
                "SELECT COUNT(*) AS total FROM familiares WHERE activo = 1"
            ).fetchone()["total"]

    def marcar_familiar_inactivo(self, chat_id: str) -> bool:
        """Desactiva un familiar SIN borrarlo. Devuelve si cambio algo.

        La fila se queda, que es de lo que se trata: desactivar es para casos como
        la «Prueba Publica» de P20, un chat que se apunto al sistema y no debe
        recibir nada pero que conviene conservar como evidencia. `activo = 0`;
        ademas cancela sus ENVIOS PENDIENTES, igual que hace `dar_de_baja`, porque
        si se dejaran el trabajador de la cola seguiria reintentandolos para siempre
        contra un chat que ya no debe recibir avisos.

        Se devuelven los avisos cancelados al operador.
        """
        chat = str(chat_id)
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE familiares SET activo = 0 WHERE chat_id = ? AND activo = 1",
                (chat,),
            )
            cancelados = self.conexion.execute(
                "DELETE FROM envios WHERE chat_id = ? AND enviado_en IS NULL",
                (chat,),
            ).rowcount
            self.conexion.commit()
            if cursor.rowcount == 1 and cancelados:
                registro.info("al inactivar al familiar %s se cancelaron %d "
                              "aviso(s) sin mandar",
                              enmascarar_chat(chat), cancelados)
            return cursor.rowcount == 1

    def marcar_familiar_activo(self, chat_id: str) -> bool:
        """Reactiva un familiar desactivado. Devuelve si cambio algo.

        Solo le vuelve a llegar lo NUEVO: lo que se encolo mientras estaba
        inactivo no se le reenvia (esa es la regla de `insertar_alerta`, que solo
        reparte entre los que estan ACTIVOS en el momento de encolar).
        """
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE familiares SET activo = 1 WHERE chat_id = ? AND activo = 0",
                (str(chat_id),),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def nombre_de(self, chat_id: str) -> str:
        """El nombre de un familiar, o una cadena vacía.

        La fila de `familiares` guarda el nombre, y el token del panel NO: el token es
        una credencial y no lleva un nombre dentro. Así que el saludo de la cabecera del
        panel («Hola, Ana») sale de aquí y no del token, y cambiar el nombre en la base
        se ve sin tener que volver a hacer `/panel`.

        Cadena vacía y no `None` para que quien lo pinte no tenga que tratar el caso
        «no hay nombre» por separado del de «el nombre está en blanco».
        """
        with _candado:
            fila = self.conexion.execute(
                "SELECT nombre FROM familiares WHERE chat_id = ?", (str(chat_id),)
            ).fetchone()
        return (fila["nombre"] or "") if fila is not None else ""

    def dar_de_baja(self, chat_id: str) -> bool:
        """Quita un familiar y cancela lo que le quedaba por recibir.

        Cancela tambien sus envios pendientes, y no solo borra su fila de
        `familiares`. Si se borrara la fila y se dejaran los envios, el trabajador
        los seguiría mandando: el aviso SEGUIRIA saliendo a un chat que el operador
        acaba de dar de baja, y el único rastro sería un envio sin destino en una
        tabla sin nombre al lado. Dar de baja es quitar el aviso, no quitar el
        nombre de la lista.

        Borra tambien sus tokens y sus enlaces del panel. Sin esto, dar de baja a
        alguien lo dejaria dentro del panel: su token en el navegador seguiria
        valiendo, porque la comprobacion de sesion tendria que mirar `familiares`
        para enterarse. Se comprueba, si, pero es una comprobacion de mas en cada
        peticion para tapar un olvido que aqui se resuelve de una vez.

        Se devuelven los envios cancelados para que el operador vea cuantos
        avisos se quedaron sin mandar, que es el número que le dice si la baja
        era de alguien que llevaba dias sin enterarse de nada.
        """
        chat = str(chat_id)
        with _candado:
            cursor = self.conexion.execute(
                "DELETE FROM familiares WHERE chat_id = ?", (chat,)
            )
            cancelados = self.conexion.execute(
                "DELETE FROM envios WHERE chat_id = ? AND enviado_en IS NULL",
                (chat,),
            ).rowcount
            sesiones = self.conexion.execute(
                "DELETE FROM panel_tokens WHERE chat_id = ?", (chat,)
            ).rowcount
            enlaces = self.conexion.execute(
                "DELETE FROM panel_enlaces WHERE chat_id = ?", (chat,)
            ).rowcount
            self.conexion.commit()
            if sesiones or enlaces:
                registro.info("al dar de baja al familiar %s se han borrado %d "
                              "sesion(es) y %d enlace(s) del panel",
                              enmascarar_chat(chat), sesiones, enlaces)
            return cursor.rowcount == 1

    # --- Panel web: enlaces de un solo uso y sesiones ------------------------

    def crear_enlace_panel(self, codigo_hash: str, chat_id: str, expira_en: int,
                           ahora: int = None) -> None:
        """Guarda el hash de un codigo de acceso del panel.

        `codigo_hash` viene ya hasheado por quien lo genera (`panel/acceso.py`).
        Aqui solo se guarda, porque hashear es cosa del que conoce el formato del
        codigo y no de la base de datos.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "INSERT OR REPLACE INTO panel_enlaces "
                "(codigo_hash, chat_id, creado_en, expira_en, intentos, usado_en) "
                "VALUES (?, ?, ?, ?, 0, NULL)",
                (codigo_hash, str(chat_id), ahora, int(expira_en)),
            )
            self.conexion.commit()

    def tomar_enlace_panel(self, codigo_hash: str, ahora: int = None) -> dict:
        """Canjea un codigo de un solo uso, y devuelve la fila si sirve.

        Devuelve `None` si el codigo no existe, ya se uso, caduco, o se paso del
        limite de intentos. El `SELECT` de comprobacion es solo informativo: quien
        decide si el canje se llevo a cabo es el `rowcount` del `UPDATE` de abajo.

        **El canje tiene que ser atomico, y por eso decide el `UPDATE`.** Comprobar
        con un `SELECT` y despues marcar con un `UPDATE` deja un hueco entre las dos
        cosas: dos peticiones a la vez, las dos leen `usado_en IS NULL` y las dos
        crean un token. Como el token dura 30 dias, eso no es un doble aviso que se
        ve y se corrige: son dos sesiones abiertas para el mismo enlace.

        Por eso el `UPDATE` lleva la condicion en su propio `WHERE` y el `rowcount`
        es la respuesta a «me lo quedo yo».

        Sobre `intentos`, para que no se lea mas de lo que es: un codigo equivocado
        NO aparece en la tabla (solo se guarda su hash), asi que no cuenta intento, y
        un codigo correcto se canjea a la primera y se consume. En la practica el
        limite de 5 casi no se alcanza nunca, y no hay que venderlo como una defensa
        contra adivinar: esa defensa es que 16 caracteres de un alfabeto de 31 son
        unos 8x10^23 combinaciones. `intentos` esta para que un cambio futuro en el
        alfabeto, o un endpoint que admita mas de un intento sobre un codigo
        conocido, no se quede sin techo.
        """
        import registro as registro_modulo

        if ahora is None:
            ahora = int(time.time())
        with _candado:
            fila = self.conexion.execute(
                "SELECT * FROM panel_enlaces WHERE codigo_hash = ?", (codigo_hash,)
            ).fetchone()
            # El codigo que no existe no cuenta intento: no hay nada que
            # adivinar, y un `SELECT` sobre un hash aleatorio es solo ruido.
            if fila is None:
                return None
            if fila["usado_en"] is not None:
                return None
            if fila["expira_en"] <= ahora:
                return None
            if fila["intentos"] >= registro_modulo.MAX_INTENTOS_CODIGO:
                return None

            self.conexion.execute(
                "UPDATE panel_enlaces SET intentos = intentos + 1 "
                "WHERE codigo_hash = ?",
                (codigo_hash,),
            )
            cursor = self.conexion.execute(
                "UPDATE panel_enlaces SET usado_en = ? "
                "WHERE codigo_hash = ? AND usado_en IS NULL",
                (ahora, codigo_hash),
            )
            self.conexion.commit()
            if cursor.rowcount != 1:
                return None
            return dict(fila)

    def crear_codigo_acceso(self, codigo_hash: str, chat_id: str, expira_en: int,
                            ahora: int = None) -> None:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "INSERT OR REPLACE INTO codigos_acceso "
                "(codigo_hash, chat_id, creado_en, expira_en, intentos, usado_en) "
                "VALUES (?, ?, ?, ?, 0, NULL)",
                (codigo_hash, str(chat_id), ahora, int(expira_en)),
            )
            self.conexion.commit()

    def invalidar_codigos_acceso(self, chat_id: str, ahora: int = None) -> int:
        """Marca como usados los codigos sin gastar de un familiar.

        Al pedir `/panel` otra vez, el codigo anterior de ESE familiar deja de
        servir: es la regla «un solo codigo vivo por familiar». Se marca con
        `usado_en` y no se borra, para que el codigo ya emitido quede en la tabla
        como evidencia y no se pueda reutilizar. Devuelve cuantos invalido, que es
        lo que la prueba puede contar.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE codigos_acceso SET usado_en = ? "
                "WHERE chat_id = ? AND usado_en IS NULL",
                (ahora, str(chat_id)),
            )
            self.conexion.commit()
            return cursor.rowcount

    def tomar_codigo_acceso(self, codigo_hash: str, ahora: int = None) -> dict:
        import hmac

        import registro as registro_modulo

        if ahora is None:
            ahora = int(time.time())
        with _candado:
            fila = self.conexion.execute(
                "SELECT * FROM codigos_acceso WHERE codigo_hash = ?", (codigo_hash,)
            ).fetchone()
            if fila is None:
                return None
            # La busqueda es por el hash como clave primaria, pero la ultima
            # comprobacion se hace con `compare_digest` para no depender de que la
            # colacion de SQLite no cambie: comparar valores en claro con `==` filtra
            # informacion por el tiempo, y aqui no cuesta nada hacerlo bien.
            if not hmac.compare_digest(str(fila["codigo_hash"]), str(codigo_hash)):
                return None
            if fila["usado_en"] is not None:
                return None
            if fila["expira_en"] <= ahora:
                return None
            if fila["intentos"] >= registro_modulo.MAX_INTENTOS_CODIGO:
                return None

            self.conexion.execute(
                "UPDATE codigos_acceso SET intentos = intentos + 1 "
                "WHERE codigo_hash = ?",
                (codigo_hash,),
            )
            cursor = self.conexion.execute(
                "UPDATE codigos_acceso SET usado_en = ? "
                "WHERE codigo_hash = ? AND usado_en IS NULL",
                (ahora, codigo_hash),
            )
            self.conexion.commit()
            if cursor.rowcount != 1:
                return None
            return dict(fila)


    def crear_token_panel(self, token_hash: str, chat_id: str, caduca_en: int,
                          ahora: int = None) -> None:
        """Guarda una sesión nueva del panel a partir de su hash."""
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "INSERT INTO panel_tokens "
                "(token_hash, chat_id, creado_en, caduca_en, ultimo_uso_en, "
                " revocado_en) VALUES (?, ?, ?, ?, NULL, NULL)",
                (token_hash, str(chat_id), ahora, int(caduca_en)),
            )
            self.conexion.commit()

    def sesion_panel(self, token_hash: str, ahora: int = None):
        """La fila del token si la sesión vale, o `None`.

        Valen las cuatro cosas a la vez: que el hash exista, que el familiar siga
        registrado, que no este revocada y que no haya caducado.

        La de `familiares` es la que evita que un token sobreviva a una baja: al
        borrar al familiar, `dar_de_baja` limpia sus tokens, pero si ese borrado se
        queda sin hacer, esta comprobacion sigue cierrando la puerta.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            fila = self.conexion.execute(
                "SELECT t.* FROM panel_tokens t "
                "WHERE t.token_hash = ? "
                "  AND t.revocado_en IS NULL "
                "  AND t.caduca_en > ? "
                "  AND EXISTS (SELECT 1 FROM familiares f "
                "              WHERE f.chat_id = t.chat_id)",
                (token_hash, ahora),
            ).fetchone()
            return dict(fila) if fila is not None else None

    def marcar_token_usado(self, token_hash: str, ahora: int = None) -> None:
        """Anota el ultimo uso de una sesión.

        Se llama una vez por sesion y no en cada peticion. El dato va para que el
        operador vea «llego a usarlo el martes», no para decidir nada: decidir
        caducidad es cosa del `caduca_en` de la fila, que no se renueva.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE panel_tokens SET ultimo_uso_en = ? WHERE token_hash = ?",
                (ahora, token_hash),
            )
            self.conexion.commit()

    def revocar_token_panel(self, token_hash: str, ahora: int = None) -> bool:
        """Cierra una sesión («salir»). `True` si estaba viva.

        Se marca `revocado_en` y no se borra la fila, para que `salir` no sea
        idempotente por accidente: la segunda vez que se llama ya no hay sesion que
        cerrar y quien llama lo puede decir. La p96 se lleva despues la fila.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE panel_tokens SET revocado_en = ? "
                "WHERE token_hash = ? AND revocado_en IS NULL",
                (ahora, token_hash),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def sesiones_panel_de(self, chat_id: str) -> list:
        """Las sesiones vivas de un familiar. Para el operador, no para la API."""
        with _candado:
            return list(self.conexion.execute(
                "SELECT token_hash, creado_en, caduca_en, ultimo_uso_en "
                "FROM panel_tokens WHERE chat_id = ? AND revocado_en IS NULL "
                "ORDER BY creado_en",
                (str(chat_id),),
            ))

    # --- Cola de alertas ---------------------------------------------------

    def insertar_alerta(self, id_alerta: str, tipo: str, carga_json: str,
                        chat_ids=None) -> bool:
        """Mete una alerta en la cola, una vez por familiar.

        Devuelve `False` si ya estaba, y en ese caso no inserta nada de nada.

        Los `chat_ids` son los familiares ACTIVOS en el momento de encolar (si se
        pasa `None`, se leen de la tabla con `familiares_activos`). Que el fan out
        consulte a los activos es lo que hace cumplir la desactivacion: un chat
        con `activo = 0` no recibe NADA nuevo, y el que se reactiva no recibe lo
        que se le acumulo mientras estaba fuera, porque en el momento de cada
        alerta no estaba activo.

        Si no hay ningun familiar, la alerta se guarda SIN envios y con
        `fanout_hecho = 0`: no es una alerta perdida, queda a la espera de que se
        registre alguien y entonces se le hace fan out (`fan_out_sin_destino`). Es
        el comportamiento que ya tenia la versión de un solo destino, donde la cola
        esperaba a que hubiera un token y un chat.

        Todo en una transaccion: o la alerta y sus N envios están, o no esta
        ninguno. Un corte entre el `INSERT` de la alerta y el de los envios
        dejaria un aviso sin nadie a quien mandarlo, que es el fallo que mas duele.
        """
        ahora = int(time.time())
        purgable = ahora + config.DIAS_PURGA_ALERTAS * 86400
        if chat_ids is None:
            chat_ids = [fila["chat_id"] for fila in self.familiares_activos()]

        with _candado:
            cursor = self.conexion.execute(
                "INSERT OR IGNORE INTO alertas "
                "(id, tipo, carga_json, creado_en, purgable_en, fanout_hecho) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (id_alerta, tipo, carga_json, ahora, purgable,
                 1 if chat_ids else 0),
            )
            if cursor.rowcount != 1:
                self.conexion.commit()
                return False
            for chat in chat_ids:
                self.conexion.execute(
                    "INSERT OR IGNORE INTO envios (alerta_id, chat_id, intentos, "
                    "proximo_intento_en, enviado_en, ultimo_error, creado_en) "
                    "VALUES (?, ?, 0, ?, NULL, NULL, ?)",
                    (id_alerta, str(chat), ahora, ahora),
                )
            self.conexion.commit()
            return True

    def fan_out_sin_destino(self, chat_id: str) -> int:
        """Manda a un familiar recien registrado los avisos que no salieron a nadie.

        Son los avisos que se acumularon mientras no había ningun familiar: los que
        tienen `fanout_hecho = 0`, que es lo mismo que decir «nunca se ofrecieron a
        nadie». El filtro va sobre el distintivo y no sobre «no tener filas en
        `envios`» a proposito, por el caso que no se ve leyendo la tabla: una
        instalación que viene del diseno de un solo destino puede tener alertas
        `enviada` que llegaron a alguien y que no se pueden asociar a nadie porque
        no queda el `chat_id` de entonces. Tambien están sin envios, y mandarselas
        al familiar que se registra hoy sería reenviarle el historico de la casa
        como si fueran avisos de ahora.

        A la vez que se encolan, se marcan como ofrecidas (`fanout_hecho = 1`): si
        dos familiares se registran a la vez, cada aviso sale una vez, no dos.

        Se llama al final de `cmd_registro`, y no al arrancar, porque entre el
        arranque y el registro puede entrar una alerta mas: encolar el fan out en
        un único sitio, justo despues de insertar al familiar, es donde no puede
        quedar una alerta sin destinatario por un corte.
        """
        ahora = int(time.time())
        with _candado:
            sin_destino = [
                fila["id"] for fila in self.conexion.execute(
                    "SELECT id FROM alertas WHERE fanout_hecho = 0 "
                    "AND resuelto_en IS NULL AND descartada_en IS NULL"
                ).fetchall()
            ]
            for id_alerta in sin_destino:
                self.conexion.execute(
                    "INSERT OR IGNORE INTO envios (alerta_id, chat_id, intentos, "
                    "proximo_intento_en, enviado_en, ultimo_error, creado_en) "
                    "VALUES (?, ?, 0, ?, NULL, NULL, ?)",
                    (id_alerta, str(chat_id), ahora, ahora),
                )
            self.conexion.execute(
                "UPDATE alertas SET fanout_hecho = 1 WHERE fanout_hecho = 0 "
                "AND resuelto_en IS NULL AND descartada_en IS NULL"
            )
            self.conexion.commit()
            return len(sin_destino)

    def tomar_envios_pendientes(self, ahora: int = None, limite: int = 10) -> list:
        """Los envios que tocan intento ahora.

        Filtra por `enviado_en IS NULL` y por `proximo_intento_en`. Sin el segundo
        filtro, un envio que fallo hace un segundo volvería a intentarse en cada
        vuelta del bucle, y con eso se golpearía la API de Telegram.

        `limite` es el número de ENVIOS, no de alertas: una alerta con dos
        familiares tiene dos filas y ocupa dos vueltas del bucle. Es lo correcto,
        porque cada envio tiene su propio reloj y mandarlos en un lote no evita
        nada.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            return list(self.conexion.execute(
                "SELECT e.*, a.tipo AS tipo, a.carga_json AS carga_json "
                "FROM envios e JOIN alertas a ON a.id = e.alerta_id "
                "WHERE e.enviado_en IS NULL AND e.proximo_intento_en <= ? "
                "  AND e.proximo_intento_en <> ? "
                "ORDER BY e.proximo_intento_en LIMIT ?",
                (ahora, 2147483647, limite),
            ))

    def marcar_enviado(self, id_alerta: str, chat_id: str,
                       ahora: int = None, mensaje_id: int = None) -> None:
        """Marca un envio como hecho y guarda el `message_id` con el que salio.

        `mensaje_id` es lo que permite reescribir ese mensaje despues si la alerta
        se atiende desde el panel. Se guarda con `COALESCE` y no a pelo: si un
        reintento devolviera un `None` (un remitente de prueba, o una libreria que
        no confirmara el mensaje), lo que hay se conserva en vez de perderse.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE envios SET enviado_en = ?, ultimo_error = NULL, "
                "mensaje_id = COALESCE(?, mensaje_id) "
                "WHERE alerta_id = ? AND chat_id = ?",
                (ahora, mensaje_id, id_alerta, str(chat_id)),
            )
            self.conexion.commit()

    def envios_a_editar(self, limite: int = 10) -> list:
        """Los avisos ya enviados cuya alerta esta resuelta y aun no se reescribieron.

        Es la cola de trabajo de `cola._editar_resueltas`: lo que falto hacerle al
        aviso cuando la alerta se atendio desde el panel (o desde cualquier sitio
        que no fuera el boton de Telegram de ese chat).

        Sale solo lo que tiene `mensaje_id`: sin mensaje no hay nada que editar, y
        meterlo aqui seria un intento de edicion fallido en cada vuelta para toda la
        vida. `editado_en IS NULL` es lo que hace que cada aviso se reescriba una
        sola vez, y tambien es lo que aparta los avisos que el propio boton del bot
        ya habia reescrito.
        """
        with _candado:
            return list(self.conexion.execute(
                "SELECT e.alerta_id, e.chat_id, e.mensaje_id, a.tipo, "
                "       a.resuelto_en "
                "FROM envios e JOIN alertas a ON a.id = e.alerta_id "
                "WHERE a.resuelto_en IS NOT NULL AND e.mensaje_id IS NOT NULL "
                "  AND e.editado_en IS NULL "
                "ORDER BY a.resuelto_en LIMIT ?",
                (limite,),
            ))

    def marcar_editado(self, id_alerta: str, chat_id: str,
                       ahora: int = None) -> None:
        """Anota que el aviso de esta fila ya no hace falta reescribirlo.

        Se llama tanto si la edicion salio como si no. La razon de marcar tambien
        el fallo es que el unico fallo posible real es que Telegram no acepte editar
        ese mensaje (aviso de mas de 48 horas, chat borrado), y eso no se arregla
        reintentando: un reintento cada cinco segundos para siempre, con su linea de
        ERROR encima, no arregla nada y tapa el resto del log.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE envios SET editado_en = ? "
                "WHERE alerta_id = ? AND chat_id = ?",
                (ahora, id_alerta, str(chat_id)),
            )
            self.conexion.commit()

    def marcar_envio_fallido(self, id_alerta: str, chat_id: str, intentos: int,
                             proximo: int, error: str) -> None:
        """Anota un fallo de UN envio y dice cuándo volver a intentarlo.

        La fila se queda con `enviado_en` en NULL. No hay ningún camino por el que
        un fallo de red lleve un aviso a descartarse, y eso es deliberado: la
        versión anterior gastaba cinco intentos y marcaba `descartada`, con lo que
        un corte de Internet de unos minutos convertía un aviso de gas en algo que
        no se mandaba nunca y no volvía a intentarse. El envio se reintenta para
        siempre, con la espera creciente de `config.REINTENTO_SEGUNDOS`.

        Y a diferencia de la versión de un solo destino, este fallo NO toca a los
        envios de los otros familiares: cada uno tiene su fila y su reloj.

        Lo único que se acota es el `intentos`, para el aviso horario de los envios
        que llevan más de un día sin poder salir, y el error, que se guarda recortado
        para que un mensaje enorme no llene la base.
        """
        with _candado:
            self.conexion.execute(
                "UPDATE envios SET intentos = ?, proximo_intento_en = ?, "
                "ultimo_error = ? WHERE alerta_id = ? AND chat_id = ?",
                (intentos, proximo, error[:500], id_alerta, str(chat_id)),
            )
            self.conexion.commit()

    def marcar_alerta_descartada(self, id_alerta: str, motivo: str) -> None:
        """Descarta una alerta que NO se puede mandar nunca.

        La reserva de `descartada` es una sola: una fila cuya `carga_json` no se
        puede leer. Reintentar esa alerta cada cinco minutos para siempre no
        arregla nada y va llenando el log. Un fallo de red nunca llega aquí.

        Se marca en `alertas` y se borran sus envios: si no, el trabajador los
        volveria a encontrar pendientes y a intentar mandarlos, y cada intento
        fallaría por lo mismo.
        """
        with _candado:
            self.conexion.execute(
                "UPDATE alertas SET descartada_en = ? WHERE id = ?",
                (int(time.time()), id_alerta),
            )
            self.conexion.execute(
                "DELETE FROM envios WHERE alerta_id = ?", (id_alerta,)
            )
            self.conexion.commit()

    def marcar_resuelta(self, id_alerta: str, ahora: int = None,
                        quien: str = None) -> bool:
        """Marca la alerta como atendida por la familia (P17).

        Se llama desde el botón "Ya lo atendí". Devuelve `True` si la alerta
        existía y no estaba ya resuelta, para que el bot sepa si tiene que
        contestar algo o si el botón ya no hacía falta.

        Resolver un aviso cancela ademas los envios que quedaban pendientes de los
        demas familiares. Un aviso de gas que ya se atendio en un movil no debe
        llegar veinte minutos despues a otro movil que llevaba sin cobertura: es
        un aviso viejo, y en un aviso viejo lo molesto es que suene. Quien lo tenga
        pendiente se queda con que la alerta sale en `/alertas` como resuelta.

        Importante: esto es lo que impide que la cola vuelva a mandar la alerta. Un
        envio ya enviado tiene `enviado_en` y no se toca; lo que se cancela es lo
        que nunca salió.

        `quien` es el NOMBRE del familiar («Ana»), que es lo que queda en
        `resuelto_por` para la linea de tiempo del panel. Nunca un `chat_id`: un
        número de chat en una pantalla de familia no significa nada para nadie. El
        nombre lo arma quien llama (`alertas.resolver`), con `db.nombre_de`.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE alertas SET resuelto_en = ?, resuelto_por = ? "
                "WHERE id = ? AND resuelto_en IS NULL",
                (ahora, quien, id_alerta),
            )
            self.conexion.execute(
                "DELETE FROM envios WHERE alerta_id = ? AND enviado_en IS NULL",
                (id_alerta,),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def obtener_alerta(self, id_alerta: str):
        with _candado:
            return self.conexion.execute(
                "SELECT * FROM alertas WHERE id = ?", (id_alerta,)
            ).fetchone()

    def gas_sin_atender_desde(self, desde: int) -> list:
        """Alertas de gas sin atender ni descartar creadas desde `desde`.

        Las usa la repeticion al conectarse un satelite (Fase 2C): un satelite
        que entra con un gas abierto lo dice, aunque no estuviera cuando llego.
        """
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM alertas WHERE tipo = 'gas' AND resuelto_en IS NULL "
                "AND descartada_en IS NULL AND creado_en >= ? ORDER BY creado_en",
                (desde,),
            ))

    def obtener_envio(self, id_alerta: str, chat_id: str):
        with _candado:
            return self.conexion.execute(
                "SELECT * FROM envios WHERE alerta_id = ? AND chat_id = ?",
                (id_alerta, str(chat_id)),
            ).fetchone()

    def envios_de_alerta(self, id_alerta: str) -> list:
        """Los avisos de una alerta, con el NOMBRE del destino y sin `chat_id`.

        Es lo que la linea de tiempo del panel («Qué pasó») enseña como eventos
        reales: si un envio tiene `enviado_en`, salió; si no, sigue esperando.
        El `chat_id` NO sale de aqui a proposito: un numero de chat en una
        pantalla de familia no significa nada para nadie, y para el nombre ya
        esta `familiares.nombre`.

        El `LEFT JOIN` y no un `INNER`: si la fila de un familiar se borró (o
        llega un envio con un chat que ya no está), el evento se enseña con el
        nombre en blanco y no desaparece, porque el envio sí ocurrió.
        """
        with _candado:
            return list(self.conexion.execute(
                "SELECT e.creado_en, e.enviado_en, "
                "COALESCE(f.nombre, '') AS nombre "
                "FROM envios e LEFT JOIN familiares f ON f.chat_id = e.chat_id "
                "WHERE e.alerta_id = ? ORDER BY e.creado_en, e.chat_id",
                (id_alerta,),
            ))

    def envios_sin_mandar_de(self, chat_id: str) -> int:
        """Cuantos avisos le quedan por llegar a este familiar."""
        with _candado:
            return self.conexion.execute(
                "SELECT COUNT(*) AS total FROM envios "
                "WHERE chat_id = ? AND enviado_en IS NULL", (str(chat_id),)
            ).fetchone()["total"]

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
                "SELECT 1 FROM alertas a WHERE a.tipo = 'ayuda' "
                "AND a.creado_en >= ? "
                "AND a.descartada_en IS NULL AND EXISTS ("
                "  SELECT 1 FROM envios e WHERE e.alerta_id = a.id "
                "  AND e.enviado_en IS NOT NULL) LIMIT 1",
                (limite,),
            )
            return cursor.fetchone() is not None

    def listar_envios_pendientes_antigas(self, antiguedad: int,
                                         ahora: int = None) -> list:
        """Envios que llevan más de `antiguedad` segundos sin salir.

        La consulta la hace el trabajador de la cola una vez por hora, para poder
        decir por log que hay un aviso de gas atascado. Sin ella, una cola que no
        puede mandar (token caducado, chat borrado, API caída) no da ninguna señal
        de que exista, y eso es exactamente el fallo que no puede pasar inadvertido.

        Devuelve una fila por envio, con el tipo de la alerta. Con dos familiares, un
        aviso atascado en uno de los mobiles sale en el log tantas veces como
        familiares afectados, y con `chat_id` para saber a quien: sin eso, "la alerta
        X lleva 30 h pendiente" no dice si el problema es de Telegram o de un
        telefono.
        """
        if ahora is None:
            ahora = int(time.time())
        limite = ahora - antiguedad
        with _candado:
            return list(self.conexion.execute(
                "SELECT e.*, a.tipo AS tipo, a.creado_en AS alerta_creado_en "
                "FROM envios e JOIN alertas a ON a.id = e.alerta_id "
                "WHERE e.enviado_en IS NULL AND a.creado_en <= ? "
                "  AND e.proximo_intento_en <> ? "
                "ORDER BY a.creado_en",
                (limite, 2147483647),
            ))

    def contar_por_estado(self) -> dict:
        with _candado:
            filas = self.conexion.execute(
                "SELECT %s AS estado, COUNT(*) AS total "
                "FROM alertas a GROUP BY estado" % CASE_ESTADO
            )
        return {fila["estado"]: fila["total"] for fila in filas}

    def listar_recientes(self, limite: int = 20, tipo: str = None) -> list:
        """El historial, para el comando `/alertas` del bot y para las pruebas.

        El estado que devuelve cada fila es el DERIVADO, el mismo que ve `/alertas`,
        y no una columna: por eso el `SELECT` trae el `CASE`. Devuelve ademas cuantos
        envios salieron y cuantos no, para que se vea que una alerta esta enviada a
        uno de dos sin tener que mirar otra tabla.
        """
        with _candado:
            enviados = ("(SELECT COUNT(*) FROM envios e "
                        "WHERE e.alerta_id = a.id AND e.enviado_en IS NOT NULL) "
                        "AS enviados")
            pendientes = ("(SELECT COUNT(*) FROM envios e "
                          "WHERE e.alerta_id = a.id AND e.enviado_en IS NULL) "
                          "AS sin_mandar")
            base = ("SELECT a.*, %s AS estado, %s, %s FROM alertas a"
                    % (CASE_ESTADO, enviados, pendientes))
            if tipo:
                return list(self.conexion.execute(
                    base + " WHERE a.tipo = ? ORDER BY a.creado_en DESC LIMIT ?",
                    (tipo, limite),
                ))
            return list(self.conexion.execute(
                base + " ORDER BY a.creado_en DESC LIMIT ?", (limite,),
            ))

    # --- Recordatorios -----------------------------------------------------

    def crear_recordatorio(self, id_rec: str, texto: str, hora_min: int,
                           repeticion: str, fecha, dias, creado_por: str,
                           proximo_en: int, ahora: int = None) -> None:
        """Guarda un recordatorio nuevo. `proximo_en` lo calcula quien llama.

        La hora se guarda como entero (minutos desde medianoche) y el cálculo de
        la próxima vez vive en `recordatorios.siguiente_vez`; aquí solo se escribe,
        igual que con el resto de las tablas: SQL puro, sin lógica de negocio.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "INSERT INTO recordatorios (id, texto, hora_min, repeticion, "
                "fecha, dias, activo, cancelado_en, hecho_en, creado_por, "
                "creado_en, proximo_en, purgable_en) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, NULL, NULL, ?, ?, ?, ?)",
                (id_rec, texto, int(hora_min), repeticion, fecha, dias,
                 str(creado_por), ahora, int(proximo_en),
                 ahora + config.DIAS_PURGA_RECORDATORIOS * 86400),
            )
            self.conexion.commit()

    def obtener_recordatorio(self, id_rec: str):
        with _candado:
            return self.conexion.execute(
                "SELECT * FROM recordatorios WHERE id = ?", (id_rec,)
            ).fetchone()

    def listar_recordatorios(self, incluir_inactivos: bool = False) -> list:
        """Los recordatorios, con el estado de su última entrega.

        El panel necesita, por cada fila, la próxima vez y UNA línea de la última
        entrega; las dos subconsultas de `recordatorio_entregas` lo traen en la
        misma lectura, para no hacer una consulta por recordatorio. El orden pone
        primero los activos y, dentro de ellos, el que toca antes.
        """
        consulta = (
            "SELECT r.*, "
            "(SELECT e.estado FROM recordatorio_entregas e "
            " WHERE e.recordatorio_id = r.id "
            " ORDER BY e.toca_en DESC LIMIT 1) AS ultimo_estado, "
            "(SELECT e.toca_en FROM recordatorio_entregas e "
            " WHERE e.recordatorio_id = r.id "
            " ORDER BY e.toca_en DESC LIMIT 1) AS ultimo_toca_en, "
            "(SELECT e.entregado_en FROM recordatorio_entregas e "
            " WHERE e.recordatorio_id = r.id "
            " ORDER BY e.toca_en DESC LIMIT 1) AS ultimo_entregado_en "
            "FROM recordatorios r"
        )
        with _candado:
            if incluir_inactivos:
                return list(self.conexion.execute(
                    consulta + " ORDER BY r.activo DESC, r.proximo_en"))
            return list(self.conexion.execute(
                consulta + " WHERE r.activo = 1 ORDER BY r.proximo_en"))

    def recordatorios_vencidos(self, ahora: int = None) -> list:
        """Los activos cuyo `proximo_en` ya venció. Es la consulta del ciclo."""
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM recordatorios "
                "WHERE activo = 1 AND proximo_en <= ? ORDER BY proximo_en",
                (int(ahora),),
            ))

    def cancelar_recordatorio(self, id_rec: str, ahora: int = None) -> bool:
        """Cancela el recordatorio entero. Devuelve si cambió algo.

        Idempotente: cancelar uno ya cancelado devuelve `False` en vez de tocar la
        fila, que es lo que deja al panel contestar «ya estaba cancelado» sin
        tener que mirarlo antes. `purgable_en` se corre a ahora + 90 días: los 90
        días de purga cuentan desde que dejó de estar activo, no desde que se creó.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE recordatorios SET activo = 0, cancelado_en = ?, "
                "purgable_en = ? WHERE id = ? AND activo = 1",
                (ahora, ahora + config.DIAS_PURGA_RECORDATORIOS * 86400, id_rec),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def cerrar_recordatorio(self, id_rec: str, ahora: int = None) -> None:
        """Desactiva un recordatorio que ya no vuelve a tocar (un `una_vez`).

        No es lo mismo que cancelar: `hecho_en` lo distingue de una cancelación a
        ojos del operador. No borra la fila: queda como historial hasta la purga.
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE recordatorios SET activo = 0, hecho_en = ?, "
                "purgable_en = ? WHERE id = ?",
                (ahora, ahora + config.DIAS_PURGA_RECORDATORIOS * 86400, id_rec),
            )
            self.conexion.commit()

    def avanzar_recordatorio(self, id_rec: str, proximo_en: int) -> None:
        with _candado:
            self.conexion.execute(
                "UPDATE recordatorios SET proximo_en = ? WHERE id = ?",
                (int(proximo_en), id_rec),
            )
            self.conexion.commit()

    def registrar_entrega(self, id_rec: str, toca_en: int, estado: str,
                          entregado_en: int = None, destino: str = None) -> bool:
        """Anota una entrega. Devuelve si la insertó (no si ya existía).

        El `INSERT OR IGNORE` sobre `(recordatorio_id, toca_en)` es lo que hace
        imposible decir dos veces el mismo vencimiento: si la fila ya está, no
        entra y devuelve `False`. Quien llama lo usa para no volver a publicar.
        """
        with _candado:
            cursor = self.conexion.execute(
                "INSERT OR IGNORE INTO recordatorio_entregas "
                "(recordatorio_id, toca_en, estado, entregado_en, destino) "
                "VALUES (?, ?, ?, ?, ?)",
                (id_rec, int(toca_en), estado, entregado_en, destino),
            )
            self.conexion.commit()
            return cursor.rowcount == 1

    def obtener_entrega(self, id_rec: str, toca_en: int):
        with _candado:
            return self.conexion.execute(
                "SELECT * FROM recordatorio_entregas "
                "WHERE recordatorio_id = ? AND toca_en = ?",
                (id_rec, int(toca_en)),
            ).fetchone()

    def marcar_entrega_entregado(self, id_rec: str, toca_en: int, ahora: int,
                                 destino: str = None) -> None:
        """Un pendiente que sí se dijo al conectarse un satélite."""
        with _candado:
            self.conexion.execute(
                "UPDATE recordatorio_entregas SET estado = 'entregado', "
                "entregado_en = ?, destino = ? "
                "WHERE recordatorio_id = ? AND toca_en = ?",
                (int(ahora), destino, id_rec, int(toca_en)),
            )
            self.conexion.commit()

    def marcar_entrega_no_entregado(self, id_rec: str, toca_en: int) -> None:
        with _candado:
            self.conexion.execute(
                "UPDATE recordatorio_entregas SET estado = 'no_entregado' "
                "WHERE recordatorio_id = ? AND toca_en = ?",
                (id_rec, int(toca_en)),
            )
            self.conexion.commit()

    def entregas_pendientes_recientes(self, desde: int) -> list:
        """Pendientes cuyo vencimiento es `desde` o posterior (dentro de ventana)."""
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM recordatorio_entregas "
                "WHERE estado = 'pendiente' AND toca_en >= ? ORDER BY toca_en",
                (int(desde),),
            ))

    def entregas_pendientes_anteriores(self, limite: int) -> list:
        """Pendientes cuyo vencimiento es anterior a `limite` (ya no se dirán)."""
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM recordatorio_entregas "
                "WHERE estado = 'pendiente' AND toca_en < ? ORDER BY toca_en",
                (int(limite),),
            ))

    # --- Configuración en caliente (Fase 4) --------------------------------

    def _config_leer(self, clave: str):
        """El valor crudo de una clave, o `None` si no está."""
        with _candado:
            fila = self.conexion.execute(
                "SELECT valor FROM configuracion WHERE clave = ?", (clave,)
            ).fetchone()
        return None if fila is None else fila["valor"]

    def _config_escribir(self, clave: str, valor: str) -> None:
        """Guarda una clave. `INSERT OR REPLACE` para que sea alta o cambio."""
        with _candado:
            self.conexion.execute(
                "INSERT OR REPLACE INTO configuracion (clave, valor) VALUES (?, ?)",
                (clave, valor),
            )
            self.conexion.commit()

    def tiempo_inactividad(self) -> int:
        """Los minutos sin movimiento antes de avisar, ya validados.

        Es la única puerta de lectura de este ajuste, y por eso valida aquí y no
        en quien llama: el valor lo escribe el PANEL en la misma base, así que
        puede ser cualquier cosa (una versión vieja, una edición a mano, un
        `NULL`). Un valor que no sea un entero dentro del rango se sustituye por
        el de fábrica y se avisa en el log; lo que NO se hace nunca es aceptar un
        número que dejaría el reloj disparando alertas sin sentido (un 0, un
        negativo, un texto).
        """
        crudo = self._config_leer("tiempo_inactividad_min")
        if crudo is None:
            return config.TIEMPO_INACTIVIDAD_MIN_DEFECTO
        try:
            minutos = int(crudo)
        except (TypeError, ValueError):
            registro.warning("tiempo_inactividad_min=%r en la base no es un "
                             "entero; se usa el de fabrica (%d)",
                             crudo, config.TIEMPO_INACTIVIDAD_MIN_DEFECTO)
            return config.TIEMPO_INACTIVIDAD_MIN_DEFECTO
        if not (config.TIEMPO_INACTIVIDAD_MIN_MINIMO
                <= minutos <= config.TIEMPO_INACTIVIDAD_MIN_MAXIMO):
            registro.warning("tiempo_inactividad_min=%r fuera del rango %d..%d; "
                             "se usa el de fabrica (%d)", crudo,
                             config.TIEMPO_INACTIVIDAD_MIN_MINIMO,
                             config.TIEMPO_INACTIVIDAD_MIN_MAXIMO,
                             config.TIEMPO_INACTIVIDAD_MIN_DEFECTO)
            return config.TIEMPO_INACTIVIDAD_MIN_DEFECTO
        return minutos

    def guardar_tiempo_inactividad(self, minutos: int) -> int:
        """Guarda el ajuste. Devuelve el valor que quedó, ya validado.

        La validación es la MISMA que en la lectura (mismo rango, mismas
        constantes), y aun así se repite: la API del panel valida antes de
        llamar, pero esta función es la última puerta antes de la base y no se
        fía de que el llamador haya hecho su trabajo. Un valor fuera de rango no
        se guarda: lanza `ValueError`, y la API lo traduce a un 400.
        """
        minutos = int(minutos)
        if not (config.TIEMPO_INACTIVIDAD_MIN_MINIMO
                <= minutos <= config.TIEMPO_INACTIVIDAD_MIN_MAXIMO):
            raise ValueError(
                "tiempo_inactividad_min fuera del rango %d..%d: %d"
                % (config.TIEMPO_INACTIVIDAD_MIN_MINIMO,
                   config.TIEMPO_INACTIVIDAD_MIN_MAXIMO, minutos)
            )
        self._config_escribir("tiempo_inactividad_min", str(minutos))
        return minutos

    def ultimo_movimiento(self):
        """El epoch del último movimiento, o `None` si nunca se ha guardado."""
        crudo = self._config_leer("ultimo_movimiento")
        if crudo is None:
            return None
        try:
            return int(crudo)
        except (TypeError, ValueError):
            registro.warning("ultimo_movimiento=%r en la base no es un entero; "
                             "se ignora", crudo)
            return None

    def fijar_ultimo_movimiento(self, cuando: int) -> None:
        """Guarda el reloj del movimiento. Lo llama `inactividad.py`."""
        self._config_escribir("ultimo_movimiento", str(int(cuando)))

    # --- Purgas ------------------------------------------------------------

    # --- Conversacion (chat de dos vias) -----------------------------------

    def crear_mensaje_conversacion(self, autor: str, autor_chat_id: str,
                                   origen: str, texto: str,
                                   ahora: int = None) -> int:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "INSERT INTO conversacion (creado_en, tipo, autor, origen, "
                "autor_chat_id, texto, estado, estado_en, purgable_en) "
                "VALUES (?, 'mensaje', ?, ?, ?, ?, 'enviado', ?, ?)",
                (ahora, autor, origen, str(autor_chat_id), texto, ahora,
                 ahora + 30 * 86400),
            )
            self.conexion.commit()
            return cursor.lastrowid

    def crear_respuesta_conversacion(self, texto: str, ahora: int = None) -> int:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "INSERT INTO conversacion (creado_en, tipo, autor, texto, "
                "purgable_en) VALUES (?, 'respuesta', 'La sala', ?, ?)",
                (ahora, texto, ahora + 30 * 86400),
            )
            self.conexion.commit()
            return cursor.lastrowid

    def crear_alerta_conversacion(self, alerta_id: str, creado_en: int,
                                  purgable_en: int = None) -> None:
        if purgable_en is None:
            purgable_en = creado_en + 30 * 86400
        with _candado:
            existe = self.conexion.execute(
                "SELECT 1 FROM conversacion WHERE alerta_id = ?", (alerta_id,)
            ).fetchone()
            if existe is None:
                self.conexion.execute(
                    "INSERT INTO conversacion (creado_en, tipo, alerta_id, "
                    "purgable_en) VALUES (?, 'alerta', ?, ?)",
                    (creado_en, alerta_id, purgable_en),
                )
                self.conexion.commit()

    def actualizar_estado_conversacion(self, id_conv: int, estado: str,
                                      ahora: int = None) -> None:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE conversacion SET estado = ?, estado_en = ? WHERE id = ?",
                (estado, ahora, id_conv),
            )
            self.conexion.commit()

    def obtener_conversacion(self, id_conv: int):
        with _candado:
            return self.conexion.execute(
                "SELECT * FROM conversacion WHERE id = ?", (id_conv,)
            ).fetchone()

    def listar_conversacion(self, limite: int = 50, antes_id: int = None) -> list:
        with _candado:
            if antes_id is None:
                filas = self.conexion.execute(
                    "SELECT * FROM conversacion ORDER BY id DESC LIMIT ?",
                    (limite + 1,),
                ).fetchall()
            else:
                filas = self.conexion.execute(
                    "SELECT * FROM conversacion WHERE id < ? ORDER BY id DESC LIMIT ?",
                    (antes_id, limite + 1),
                ).fetchall()
        hay_mas = len(filas) > limite
        filas = filas[:limite]
        filas = list(reversed(filas))
        return filas, hay_mas

    def hay_mas_conversacion(self, antes_id: int) -> bool:
        with _candado:
            fila = self.conexion.execute(
                "SELECT 1 FROM conversacion WHERE id < ? LIMIT 1",
                (antes_id,),
            ).fetchone()
        return fila is not None

    def ultimo_mensaje_de(self, chat_id: str):
        with _candado:
            return self.conexion.execute(
                "SELECT creado_en FROM conversacion "
                "WHERE autor_chat_id = ? AND tipo = 'mensaje' "
                "ORDER BY creado_en DESC LIMIT 1",
                (str(chat_id),),
            ).fetchone()

    def conversacion_desde(self, desde_id: int, limite: int = 50) -> list:
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM conversacion WHERE id > ? ORDER BY id LIMIT ?",
                (desde_id, limite),
            ))

    def mensajes_pendientes_conversacion(self) -> list:
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM conversacion WHERE estado = 'enviado' "
                "ORDER BY creado_en"
            ))

    def mensajes_enviados_sin_notificar(self, limite: int = 10) -> list:
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM conversacion WHERE tipo IN ('mensaje','respuesta') "
                "AND notificado_en IS NULL ORDER BY creado_en LIMIT ?",
                (limite,),
            ))

    def marcar_conversacion_notificada(self, id_conv: int, ahora: int = None) -> None:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE conversacion SET notificado_en = ? WHERE id = ?",
                (ahora, id_conv),
            )
            self.conexion.commit()

    def crear_envio_conversacion(self, id_conv: int, chat_id: str,
                                 mensaje_id: int = None) -> None:
        ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "INSERT OR IGNORE INTO conversacion_envios "
                "(conversacion_id, chat_id, mensaje_id, creado_en) "
                "VALUES (?, ?, ?, ?)",
                (id_conv, str(chat_id), mensaje_id, ahora),
            )
            self.conexion.commit()

    def envios_conversacion_a_editar(self, id_conv: int) -> list:
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM conversacion_envios WHERE conversacion_id = ? "
                "AND mensaje_id IS NOT NULL AND editado_en IS NULL",
                (id_conv,),
            ))

    def marcar_envio_conversacion_editado(self, id_conv: int, chat_id: str,
                                          ahora: int = None) -> None:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE conversacion_envios SET editado_en = ? "
                "WHERE conversacion_id = ? AND chat_id = ?",
                (ahora, id_conv, str(chat_id)),
            )
            self.conexion.commit()

    def purgar_conversacion(self, ahora: int) -> None:
        with _candado:
            self.conexion.execute(
                "DELETE FROM conversacion_envios WHERE conversacion_id IN "
                "(SELECT id FROM conversacion WHERE purgable_en < ?)", (ahora,))
            self.conexion.execute(
                "DELETE FROM conversacion WHERE purgable_en < ?", (ahora,))

    def purgar(self, ahora: int = None) -> tuple:
        """Borra lo viejo. Devuelve cuántos borró de cada tabla.

        Los ids se van a los 7 días, las alertas y sus envios a los 30, cada una con
        su propia fecha en su propia fila. Por eso `purgable_en` está en `alertas` y
        el filtro de `ids_procesados` usa `recibido_en + 7 días`.

        No hay llave foránea entre las dos, así que borrar los ids no arrastra
        ninguna alerta: es justo lo que hace que la purga se pueda hacer en dos
        `DELETE` independientes. Los envios se borran con un `DELETE` que mira el
        `purgable_en` de su alerta, y despues se borran las alertas: en ese orden no
        puede quedar un envio huérfano ni aunque el proceso muera entre medias.

        Los envios ya enviados tambien se purgan, y no solo los pendientes: son la
        parte mas grande de la tabla y guardan lo mismo que la alerta que ya esta en
        el historial.

        Las dos tablas del panel se purgan aqui tambien, y no lo hace el servicio del
        panel: la purga corre cada hora en el orquestador, y meterse en la base desde
        dos procesos para la misma tarea son dos relojes que se pisan. Los plazos son
        cortos porque son cosas que ya no sirven para nada:

          - Los enlaces de un solo uso, un dia despues de usados o caducados. No
            hace falta esperar 30 dias para algo que, como mucho, vale 10 minutos:
            el unico caso en que un enlace caducado sirve de algo es que alguien
            este mirando el historico del navegador dentro de un mes, y a ese
            familiar le va a llegar un `/panel` nuevo.
          - Los tokens, 30 dias despues de caducar o de revocarse. Aqui si se
            espera el plazo entero, porque un token caducado es la prueba de que
            esa sesion existio, y el operador lo necesita para saber si una baja
            funciono.

        Los recordatorios inactivos (cancelados o ya dichos) se purgan a los 90
        dias, con sus entregas. El plazo es mas largo que el de las alertas porque
        un recordatorio es «que tenia que hacer la persona que vive en la casa», y
        la familia quiere reconstruir mas tiempo que el historial de gas.
        """
        if ahora is None:
            ahora = int(time.time())
        con_ids = ahora - config.DIAS_PURGA_IDS * 86400
        with _candado:
            cursor_ids = self.conexion.execute(
                "DELETE FROM ids_procesados WHERE recibido_en < ?", (con_ids,)
            )
            borrados_ids = cursor_ids.rowcount
            cursor_envios = self.conexion.execute(
                "DELETE FROM envios WHERE alerta_id IN "
                "(SELECT id FROM alertas WHERE purgable_en < ?)", (ahora,)
            )
            borrados_envios = cursor_envios.rowcount
            cursor_alertas = self.conexion.execute(
                "DELETE FROM alertas WHERE purgable_en < ?", (ahora,)
            )
            borrados_alertas = cursor_alertas.rowcount
            cursor_enlaces = self.conexion.execute(
                "DELETE FROM panel_enlaces WHERE "
                "  (usado_en IS NOT NULL AND usado_en < ?) OR "
                "  (usado_en IS NULL AND expira_en < ?)",
                (ahora - config.DIAS_PURGA_PANEL_ENLACES * 86400,
                 ahora - config.DIAS_PURGA_PANEL_ENLACES * 86400),
            )
            borrados_enlaces = cursor_enlaces.rowcount
            cursor_tokens = self.conexion.execute(
                "DELETE FROM panel_tokens WHERE "
                "  caduca_en < ? OR (revocado_en IS NOT NULL AND revocado_en < ?)",
                (ahora - config.DIAS_PURGA_PANEL_TOKENS * 86400,
                 ahora - config.DIAS_PURGA_PANEL_TOKENS * 86400),
            )
            borrados_tokens = cursor_tokens.rowcount
            # Los recordatorios, en el mismo orden que alertas/envios: primero las
            # entregas que cuelgan del recordatorio, y después el recordatorio. Solo
            # se van los INACTIVOS (cancelados o ya dichos) cuyo `purgable_en` pasó;
            # un recordatorio diario activo no se toca por viejo que sea.
            cursor_rec_entregas = self.conexion.execute(
                "DELETE FROM recordatorio_entregas WHERE recordatorio_id IN "
                "(SELECT id FROM recordatorios WHERE activo = 0 AND purgable_en < ?)",
                (ahora,),
            )
            borrados_rec_entregas = cursor_rec_entregas.rowcount
            cursor_recordatorios = self.conexion.execute(
                "DELETE FROM recordatorios WHERE activo = 0 AND purgable_en < ?",
                (ahora,),
            )
            borrados_recordatorios = cursor_recordatorios.rowcount
            # Conversacion: 30 dias, igual que las alertas.
            self.conexion.execute(
                "DELETE FROM conversacion_envios WHERE conversacion_id IN "
                "(SELECT id FROM conversacion WHERE purgable_en < ?)", (ahora,))
            self.conexion.execute(
                "DELETE FROM conversacion WHERE purgable_en < ?", (ahora,))
            self.conexion.commit()
        if borrados_enlaces or borrados_tokens:
            registro.info("purgados del panel: %d enlace(s) y %d sesion(es)",
                          borrados_enlaces, borrados_tokens)
        if borrados_recordatorios:
            registro.info("purgados %d recordatorio(s) inactivo(s) y %d entrega(s)",
                          borrados_recordatorios, borrados_rec_entregas)
        return borrados_ids, borrados_alertas
