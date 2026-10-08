-- El esquema de la base de datos del orquestador.
--
-- Tres tablas, y la separación entre ellas es lo que hace que el sistema no mande
-- avisos repetidos, y lo que hace que un familiar que falla no frene a los demas.
--
-- `ids_procesados` es una memoria de los ids que ya se han procesado. Es la que
-- hace la deduplicacion: si un mensaje llega dos veces, el `INSERT OR IGNORE` no
-- inserta nada y se descarta el segundo. Sin esto, un mensaje repetido se
-- convierte en dos avisos a la familia.
--
-- `alertas` es lo que pasó: qué avisos hay, cuándo y de qué tipo.
--
-- `envios` es la cola de trabajo: a QUIÉN hay que mandar cada aviso, con su estado
-- y su próximo intento. Un aviso son N filas de `envios`, una por familiar
-- registrado en el momento de encolar el aviso.
--
-- `familiares` es la lista de chats que reciben las alertas. Vive en SQLite y no
-- en un archivo, porque es un conjunto y no un valor: un archivo con un solo
-- destino era el diseño de "un familiar", y en cuanto hay dos hay que migrarlo.
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
--   - La relacion entre esas dos no la necesita nadie. `ids_procesados` solo se
--     consulta con `SELECT 1 WHERE id = ?` para saber si algo ya paso, y
--     `alertas` se consulta por su propio `id`, que es la clave primaria.
--
-- Entre `alertas` y `envios` tampoco hay llave foranea, y por el mismo motivo: la
-- purga borra primero los envios de las alertas vencidas y despues las alertas, en
-- dos `DELETE` independientes. Con una cascada, una purga a medias podria dejar
-- alertas sin la fila que dice si se mandaron.
--
-- Las cifras de purga (7 dias para los ids, 30 para las alertas) NO estan aqui,
-- porque este archivo es SQL puro. Viven en `config.py`, que es quien las lee.
-- Duplicarlas aqui haria que un cambio en un sitio no se aplicara en el otro, y
-- el fallo seria que la purga borrara filas de mas o de menos sin que nadie lo
-- notara.
--
-- Ninguna de las tres guarda el token de Telegram, ni contrasenas, ni nada del
-- `acl.conf`. El `chat_id` de un familiar si es dato personal, asi que sale a la
-- consola del operador y al log enmascarado (ver `db.enmascarar_chat`).


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


-- --- familiares -----------------------------------------------------------
--
-- Un chat que recibe las alertas. La clave primaria es el `chat_id` de Telegram,
-- y por eso no puede haber dos filas del mismo familiar: registrarse dos veces es
-- un `INSERT OR IGNORE`, no un envio duplicado.
--
-- El nombre es lo que la persona escribio detras del codigo, y es opcional. No se
-- usa para decidir nada: es para que el operador sepa a quien va a dar de baja.
--
-- No hay columna `activo` ni borrado logico. "Esta en la tabla" significa "recibe
-- alertas", y una fila que significa "no" solo invites a bugs: un `SELECT` que se
-- olvide del `WHERE activo = 1` mandaria avisos a alguien dado de baja. Dar de
-- baja borra la fila y cancela sus envios pendientes (ver `db.dar_de_baja`).

CREATE TABLE IF NOT EXISTS familiares (
    chat_id        TEXT NOT NULL PRIMARY KEY,
    nombre         TEXT NOT NULL DEFAULT '',
    registrado_en  INTEGER NOT NULL
);


-- --- alertas --------------------------------------------------------------
--
-- Una fila por aviso, no por envio. El `id` es clave primaria y por eso sola ya
-- impide que un mismo aviso se inserte dos veces, aunque el `INSERT OR IGNORE` de
-- ids_procesados llegara a fallar.
--
-- `alertas` NO tiene columna de estado, y eso es lo unico raro de este archivo. El
-- estado de un aviso no es una cosa que se guarde: sale de sus filas de `envios` y
-- de las dos marcas de aqui. Un aviso esta:
--
--   descartada -- `descartada_en` no es nulo. Solo si la `carga_json` era ilegible:
--                 reintentar un texto que no se puede leer no lo arregla nunca.
--                 Un fallo de red NUNCA lleva aqui.
--   resuelta   -- `resuelto_en` no es nulo: alguien pulso "Ya lo atendi" (P17).
--   pendiente  -- tiene al menos un envio sin mandar, o no tiene ninguno todavia
--                 (alerta esperando a que haya un familiar registrado).
--   enviada    -- todos sus envios salieron y nadie ha pulsado el boton.
--
-- Guardar el estado en una columna seria un segundo sitio donde la verdad puede
-- estar equivocada: habria que actualizar el estado de la alerta Y el del envio en
-- el mismo commit, y un fallo a mitad dejaria la familia con un aviso que el log
-- dice enviado y que no existe. Con la verdad repartida en `envios`, que es donde
-- esta el envio, no hay nada que mantener sincronizado.
--
-- Un aviso sin ningun envio es `pendiente` y no `enviada`: nadie lo ha recibido.
-- Cuando se registra un familiar nuevo se le fan out a los avisos que no tienen
-- ningun envio (ver `db.fan_out_sin_destino`), que son los que se acumulaban
-- mientras no habia a quien mandarlos.
--
-- `purgable_en` se calcula al insertar (ahora + 30 dias), y no se calcula al
-- purgar. Asi, si algun dia el periodo de purga cambia, las filas que ya estan
-- en la tabla conservan la fecha con la que se insertaron.

CREATE TABLE IF NOT EXISTS alertas (
    id             TEXT NOT NULL PRIMARY KEY,
    tipo           TEXT NOT NULL,
    carga_json     TEXT NOT NULL,
    resuelto_en    INTEGER,
    descartada_en  INTEGER,
    creado_en      INTEGER NOT NULL,
    purgable_en    INTEGER NOT NULL,
    -- Si esta alerta ya se ofrecio a la lista de familiares. 1 = si, 0 = se
    -- guardo esperando a que hubiera alguien a quien mandarsela.
    --
    -- Existe por un caso que no se ve leyendo el resto de la tabla: la
    -- instalacion que viene del diseno de "un solo destino" puede tener alertas
    -- `enviada` o `resuelta` y, a la vez, no tener NINGUN familiar con el que
    -- asociarlas (si `familia.json` no estaba, o si ya se habia borrado). Esas
    -- alertas llegaron a alguien en su dia, pero el esquema viejo no guardaba a
    -- quien. Sin esta columna, "no tiene envios" las hace parecer esperando
    -- destinatario, y el familiar que se registre mañana recibe el historico de
    -- la casa como si fuera una alerta de ahora. La migracion las marca con 1:
    -- se offerieron a un destino que ya no se puede nombrar, y no se vuelven a
    -- ofrecer. Solo las alertas nuevas con 0 esperan a un familiar.
    fanout_hecho   INTEGER NOT NULL DEFAULT 0
);

-- Para el historial: que alertas de gas hubo esta semana.
CREATE INDEX IF NOT EXISTS idx_alertas_tipo
    ON alertas (tipo, creado_en);

-- OJO: el indice de las alertas que esperan a un familiar NO esta aqui, y es a
-- proposito. Se crea al final de `db._migrar()`, y no como un `CREATE INDEX` mas de
-- este archivo, por el orden en que se ejecutan las cosas:
--
--   1. `BaseDatos` ejecuta este fichero entero.
--   2. Despues llama a `_migrar()`, que es la que anade `fanout_hecho` a una tabla
--      `alertas` que todavia tiene las columnas del diseno de un solo destino.
--
-- Un `CREATE INDEX` sobre `fanout_hecho` en el paso 1 falla en el paso 2 con
-- «no such column», porque en una base ya existente el `CREATE TABLE IF NOT EXISTS`
-- de arriba no hace nada (la tabla esta, con las columnas viejas) y la columna
-- nueva todavia no existe. Es decir: el esquema que se ejecuta primero es el
-- NUEVO, aplicado sobre una tabla VIEJA, y SQLite no perdona esa mezcla.
--
-- Por eso este indice vive en `_migrar()`, que es donde la columna pasa a existir.


-- --- envios ---------------------------------------------------------------
--
-- Una fila por (aviso, familiar). Esta tabla ES la cola, y el trabajador de la cola
-- lee solo de aqui.
--
-- La clave primaria compuesta es lo que hace que un aviso llegue una vez a cada
-- familiar y no dos: el `INSERT OR IGNORE` al encolar ignora al familiar que ya
-- tenia esa fila.
--
-- Cada fila lleva SU PROPIO `intentos` y SU PROPIO `proximo_intento_en`, y esa es
-- la razon de que esta tabla exista en vez de que los reintentos vivieran en
-- `alertas`. Un familiar con el movil sin cobertura falla el envio; el otro, con el
-- movil cargado, lo recibe. Si los reintentos fueran del aviso, el que falla
-- estorbaria al que funciona: cada nueva alta esperaria a que el primero tuviera
-- cobertura, y una alerta de gas se retrasaria por el movil de una persona.
--
-- `enviado_en` es lo que marca el envio como hecho. Mientras sea nulo, la fila se
-- reintenta, y da igual cuantos intentos lleve: un fallo de red no descarta nunca
-- un aviso de gas (ver `cola.TrabajadorTelegram`).
--
-- `mensaje_id` y `editado_en` son los dos campos con los que el aviso de Telegram
-- se reescribe cuando la alerta se atiende DESDE EL PANEL. El que pulsa el boton
-- del bot edita su propio mensaje alla mismo (tiene el objeto `Message` en la mano
-- y el token), pero el panel es otro proceso: resuelve en la base y no puede tocar
-- Telegram. Lo que queda aqui es lo que le permite al orquestador hacerlo despues.
-- Sin `mensaje_id` no habria manera de saber que mensaje reescribir (con dos
-- familiares son dos mensajes distintos en dos chats distintos), y sin
-- `editado_en` la tarea de reescritura volveria a editar el mismo mensaje en cada
-- vuelta.

CREATE TABLE IF NOT EXISTS envios (
    alerta_id          TEXT NOT NULL,
    chat_id            TEXT NOT NULL,
    intentos           INTEGER NOT NULL DEFAULT 0,
    proximo_intento_en INTEGER NOT NULL DEFAULT 0,
    enviado_en         INTEGER,
    ultimo_error       TEXT,
    creado_en          INTEGER NOT NULL,
    -- El `message_id` de Telegram del aviso, en su chat. Nulo mientras el envio
    -- no haya salido: solo hay mensaje que editar cuando lo hay.
    mensaje_id         INTEGER,
    -- Cuando se reescribio como ATENDIDO. Nulo = sigue con el aviso y los
    -- botones, y es la condicion que mira `db.envios_a_editar`.
    editado_en         INTEGER,
    PRIMARY KEY (alerta_id, chat_id)
);

-- El trabajador busca envios sin mandar cuyo proximo intento ya vencio, una vez
-- cada `PAUSA_TRABAJADOR_S`. Sin este indice recorre todos los envios de 30 dias
-- en cada vuelta.
CREATE INDEX IF NOT EXISTS idx_envios_pendientes
    ON envios (enviado_en, proximo_intento_en);

-- Para dar de baja a alguien y ver cuantos envios sueltos tiene.
CREATE INDEX IF NOT EXISTS idx_envios_chat
    ON envios (chat_id, enviado_en);


-- --- panel_enlaces ---------------------------------------------------------
--
-- Los codigos de un solo uso que el bot manda en `/panel`. Una fila por codigo
-- emitido, y son de UN SOLO USO: el que lo canjea por un token se queda sin
-- Poder volver a usarlo, y el codigo se marca con `usado_en`.
--
-- Se guarda el HASH del codigo, no el codigo. Un `SELECT` sobre esta tabla, o
-- una copia del volumen, no entrega nada con lo que entrar al panel. El codigo
-- viaja entero por Telegram y por la URL, asi que en algun momento existe en
-- claro, pero en la base no.
--
-- El alfabeto y la longitud son los de `registro.py` (16 caracteres sin 0/O ni
-- 1/l/I), reutilizados a proposito: un codigo que se teclea o se dicta no puede
-- confundirse entre un cero y una o. Un enlace con el codigo pegado al final
-- son 16 caracteres mas el dominio, y sigue siendo corto.
--
-- `intentos` lleva el limite de 5, y va EN LA FILA y no en memoria porque el que
-- genera el codigo es el bot y el que lo cuenta es la API: son dos procesos, y un
-- contador en memoria de uno de los dos no lo veria el otro.
--
-- Para que no se lea mas de lo que es: un codigo equivocado no esta en la tabla
-- (solo se guarda su hash), asi que no cuenta intento, y uno correcto se canjea a
-- la primera. La defensa contra adivinar no es esta columna, son los 16 caracteres
-- de un alfabeto de 31. `intentos` es un techo por si algun dia el codigo deja de
-- ser de un solo intento.
--
-- NO hay llave foranea con `panel_tokens` ni con `familiares`: esta tabla esta
-- para borrar, no para impedir nada. Si un familiar se da de baja, sus tokens se
-- borran y sus enlaces dejan de servir porque el canje comprueba `familiares`.

CREATE TABLE IF NOT EXISTS panel_enlaces (
    codigo_hash  TEXT NOT NULL PRIMARY KEY,
    chat_id      TEXT NOT NULL,
    creado_en    INTEGER NOT NULL,
    expira_en    INTEGER NOT NULL,
    intentos     INTEGER NOT NULL DEFAULT 0,
    usado_en     INTEGER
);

-- Para la purga: lo que ya esta usado o caducado y se puede ir.
CREATE INDEX IF NOT EXISTS idx_panel_enlaces_expira
    ON panel_enlaces (expira_en);


-- --- panel_tokens ----------------------------------------------------------
--
-- Las sesiones del panel web. Un token por dispositivo (mas bien, por navegador y
-- origen), y el token es lo que va en `Authorization: Bearer`.
--
-- El token NO se guarda en claro, solo su sha256. A diferencia del codigo, aqui
-- no hay ninguna excusa para guardarlo: el token no lo teclea nadie y no viaja en
-- ninguna URL que el usuario pueda leer, y son 32 bytes de `secrets`, asi que un
-- hash no se puede invertir.
--
-- No hay columna `nombre`: sale de `familiares` con un `SELECT`. Copiarlo aqui
-- seria un segundo sitio donde puede estar equivocado, y no se usa para decidir
-- nada.
--
-- `chat_id` es lo que permite cerrar el acceso de un familiar concreto sin tocar
-- nada mas: `db.dar_de_baja()` borra sus filas de aqui, y a partir de ahi su
-- token da 401 aunque el navegador lo siga teniendo guardado.
--
-- `caduca_en` son 30 dias, el mismo plazo que el historial de alertas. No se
-- renueva solo por usar el panel: se renueva haciendo `/panel` de nuevo. Un token
-- que se renueva solo es un token que nunca se va.

CREATE TABLE IF NOT EXISTS panel_tokens (
    token_hash    TEXT NOT NULL PRIMARY KEY,
    chat_id       TEXT NOT NULL,
    creado_en     INTEGER NOT NULL,
    caduca_en     INTEGER NOT NULL,
    ultimo_uso_en INTEGER,
    revocado_en   INTEGER
);

-- Para la purga y para dar de baja a alguien: quien tiene una sesion abierta.
CREATE INDEX IF NOT EXISTS idx_panel_tokens_chat
    ON panel_tokens (chat_id);

CREATE INDEX IF NOT EXISTS idx_panel_tokens_caduca
    ON panel_tokens (caduca_en);
