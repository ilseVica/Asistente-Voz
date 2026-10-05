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

# La versión del esquema, en `PRAGMA user_version`. Es lo que hace que una migración
# corra una vez y no en cada arranque.
#
# Antes no hacia falta: el único cambio de esquema era agregar `resuelta_en`, que se
# hacia con un `ALTER TABLE` idempotente ("si no existe la columna, agregala"). Eso
# bastaba mientras las migraciones fueran de una columna. Esta vez el cambio es de
# otra forma: hay una tabla nueva y hay que REPARTIR las filas que ya havia entre
# dos, y eso no se puede repetir sin duplicar. Un número de versión lo dice sin
# ambigüedad, y no necesita una tabla nueva ni una convención con la base.
VERSION_ESQUEMA = 1

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

        self.conexion.execute("PRAGMA user_version = %d" % VERSION_ESQUEMA)
        registro.info("base de datos migrada a la versión %d del esquema",
                      VERSION_ESQUEMA)

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

    def familiares_registrados(self) -> list:
        """Los familiares, del mas antiguo al mas reciente.

        El orden es el de alta, no el alfabetico: el primero en registrarse es el
        que mas tiempo lleva cuidando la casa, y en la cola de envios ese es el
        orden en que se avisa.
        """
        with _candado:
            return list(self.conexion.execute(
                "SELECT * FROM familiares ORDER BY registrado_en, chat_id"
            ))

    def contar_familiares(self) -> int:
        with _candado:
            return self.conexion.execute(
                "SELECT COUNT(*) AS total FROM familiares"
            ).fetchone()["total"]

    def dar_de_baja(self, chat_id: str) -> bool:
        """Quita un familiar y cancela lo que le quedaba por recibir.

        Cancela tambien sus envios pendientes, y no solo borra su fila de
        `familiares`. Si se borrara la fila y se dejaran los envios, el trabajador
        los seguiría mandando: el aviso SEGUIRIA saliendo a un chat que el operador
        acaba de dar de baja, y el único rastro sería un envio sin destino en una
        tabla sin nombre al lado. Dar de baja es quitar el aviso, no quitar el
        nombre de la lista.

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
            self.conexion.commit()
            return cursor.rowcount == 1

    # --- Cola de alertas ---------------------------------------------------

    def insertar_alerta(self, id_alerta: str, tipo: str, carga_json: str,
                        chat_ids=None) -> bool:
        """Mete una alerta en la cola, una vez por familiar.

        Devuelve `False` si ya estaba, y en ese caso no inserta nada de nada.

        Los `chat_ids` son los familiares registrados en el momento de encolar. Si
        se pasa `None`, se leen de la tabla, que es lo que hace el camino normal.

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
            chat_ids = [fila["chat_id"] for fila in self.familiares_registrados()]

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
                "ORDER BY e.proximo_intento_en LIMIT ?",
                (ahora, limite),
            ))

    def marcar_enviado(self, id_alerta: str, chat_id: str,
                       ahora: int = None) -> None:
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            self.conexion.execute(
                "UPDATE envios SET enviado_en = ?, ultimo_error = NULL "
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

    def marcar_resuelta(self, id_alerta: str, ahora: int = None) -> bool:
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
        """
        if ahora is None:
            ahora = int(time.time())
        with _candado:
            cursor = self.conexion.execute(
                "UPDATE alertas SET resuelto_en = ? "
                "WHERE id = ? AND resuelto_en IS NULL",
                (ahora, id_alerta),
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

    def obtener_envio(self, id_alerta: str, chat_id: str):
        with _candado:
            return self.conexion.execute(
                "SELECT * FROM envios WHERE alerta_id = ? AND chat_id = ?",
                (id_alerta, str(chat_id)),
            ).fetchone()

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
                "ORDER BY a.creado_en",
                (limite,),
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

    # --- Purgas ------------------------------------------------------------

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
            self.conexion.commit()
        return borrados_ids, borrados_alertas
