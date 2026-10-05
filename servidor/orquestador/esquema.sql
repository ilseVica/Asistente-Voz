-- El esquema de la base de datos del orquestador.
--
-- Dos tablas, y la separacion entre las dos es lo que hace que el sistema no
-- mande avisos repetidos.
--
-- `ids_procesados` es una memoria de los ids que ya se han procesado. Es la que
-- hace la deduplicacion: si un mensaje llega dos veces, el `INSERT OR IGNORE` no
-- inserta nada y se descarta el segundo. Sin esto, un mensaje repetido se
-- convierte en dos avisos a la familia.
--
-- `alertas` es la cola de trabajo: lo que falta por mandar a Telegram, con su
-- estado y su proximo intento.
--
-- Por que estan separadas y por que NO hay llave foranea entre ellas:
--
--   - Los ids se purgan a los 7 dias, porque son solo para evitar duplicados en
--     el corto plazo. Las alertas se guardan 30 dias, porque son el historial de
--     lo que paso en la casa.
--   - Si `alertas` tuviera una llave foranea a `ids_procesados`, al purgar los
--     ids a los 7 dias la fila de la alerta se borraria en cascada o la purga
--     fallaria. O sea: la alerta de hace diez dias se perderia, o la purga de
--     ids no terminaria.
--   - La relacion entre las dos no la necesita nadie. `ids_procesados` solo se
--     consulta con `SELECT 1 WHERE id = ?` para saber si algo ya paso, y
--     `alertas` se consulta por su propio `id`, que es la clave primaria.
--
-- Las cifras de purga (7 dias para los ids, 30 para las alertas) NO estan aqui,
-- porque este archivo es SQL puro. Viven en `config.py`, que es quien las lee.
-- Duplicarlas aqui haria que un cambio en un sitio no se aplicara en el otro, y
-- el fallo seria que la purga borrara filas de mas o de menos sin que nadie lo
-- notara.
--
-- Ninguna de las dos guarda el token de Telegram, ni contrasenas, ni nada del
-- `acl.conf`.


-- --- ids_procesados -------------------------------------------------------
--
-- Una fila por id visto. Es lo que evita el doble aviso.

CREATE TABLE IF NOT EXISTS ids_procesados (
    id           TEXT NOT NULL PRIMARY KEY,
    tipo         TEXT NOT NULL,
    recibido_en  INTEGER NOT NULL
);

-- Se consulta por `id`, que ya es la clave primaria. Este indice es para la
-- purga por antiguedad, que va a ser la consulta mas frecuente del proceso:
-- corre cada hora y toca la tabla entera.
CREATE INDEX IF NOT EXISTS idx_ids_recibido
    ON ids_procesados (recibido_en);


-- --- alertas --------------------------------------------------------------
--
-- Una fila por aviso a enviar. `id` es clave primaria y por eso sola ya impide
-- que un mismo aviso se inserte dos veces en la cola, aunque el
-- `INSERT OR IGNORE` de ids_procesados llegara a fallar.
--
-- `estado` toma estos valores:
--
--   pendiente  -- en la cola, esperando su proximo intento
--   enviada    -- Telegram la confirmo
--   resuelta   -- la familia pulso "Ya lo atendi" (P17)
--   descartada -- solo si la carga_json era ilegible. Un fallo de red NUNCA lleva
--                 aqui: una alerta que no se pudo mandar se reintenta para siempre
--
-- `purgable_en` se calcula al insertar (ahora + 30 dias), y no se calcula al
-- purgar. Asi, si algun dia el periodo de purga cambia, las filas que ya estan
-- en la tabla conservan la fecha con la que se insertaron.

CREATE TABLE IF NOT EXISTS alertas (
    id                 TEXT NOT NULL PRIMARY KEY,
    tipo               TEXT NOT NULL,
    carga_json         TEXT NOT NULL,
    estado             TEXT NOT NULL DEFAULT 'pendiente',
    intentos           INTEGER NOT NULL DEFAULT 0,
    proximo_intento_en INTEGER NOT NULL DEFAULT 0,
    enviado_en         INTEGER,
    resuelta_en        INTEGER,
    ultimo_error       TEXT,
    creado_en          INTEGER NOT NULL,
    purgable_en        INTEGER NOT NULL
);

-- El trabajador de la cola busca las pendientes cuyo proximo intento ya vencio.
-- Sin este indice, cada vuelta recorre todas las alertas de 30 dias.
CREATE INDEX IF NOT EXISTS idx_alertas_pendientes
    ON alertas (estado, proximo_intento_en);

-- Para el historial: que alertas de gas hubo esta semana.
CREATE INDEX IF NOT EXISTS idx_alertas_tipo
    ON alertas (tipo, creado_en);
