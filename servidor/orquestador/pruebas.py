"""Las pruebas del orquestador, sin MQTT y sin Telegram.

    python3 pruebas.py

Todo lo que se comprueba aquí es una DECISIÓN del código, no un efecto de la red:
se falsean Telegram, el tiempo y el reloj de los ficheros, y lo que se mira es lo
que el sistema decide. Un `docker compose exec` con la red caída no distingue un
fallo del diseño de un fallo de la conexión, y para cambiar de diseño hace falta
algo que se pueda repetir en dos segundos.

Lo que se cubre, en orden:

  1. La validación por tópico, y que `tipo` sea un campo extra más.
  2. Que la lista de familiares esté en SQLite y que cada alta se SUME.
  3. El fan out: una alerta, un envío por familiar.
  4. Que los reintentos sean independientes (el fallo de uno no toca al otro).
  5. Que un fallo de red NUNCA convierta un aviso en algo que no se manda.
  6. El backfill: lo que se acumuló sin nadie sale, el histórico no se reenvía.
  7. La migración del diseño de un solo destino, con y sin `familia.json`.
  8. `/registro`: silencio solo para su propio chat, alta sin quitar a nadie.
  9. `/alertas`: contesta a cualquier familiar registrado y a nadie más.
 10. El botón «Ya lo atendí»: cualquier familiar, y editando en SU chat.
 11. El CLI de familiares: listar y dar de baja.
 12. Que ningún `chat_id` salga entero al log.

Las pruebas que necesitan una base de datos usan una temporal y la borran, así que
no tocan `/datos` ni el `codigos.json` del servidor. Para las que necesitan una base
con el esquema VIEJO se construye una a mano en otra temporal: la migración solo se
puede probar migrating algo.
"""

import asyncio
import contextlib
import io
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

# El token es de mentira y lo dice: si alguna vez se colara un token de verdad en
# este archivo, el rastreo de secretos lo vería y saltaría la cosa equivocada.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TOKEN-DE-PRUEBAS-NO-REAL")

datos = tempfile.mkdtemp(prefix="pruebas-orquestador-")
os.environ["ORQUESTADOR_DB"] = os.path.join(datos, "pruebas.sqlite3")

import alertas  # noqa: E402
import bot as bot_modulo  # noqa: E402
import cola as cola_modulo  # noqa: E402
import config  # noqa: E402
import db as db_modulo  # noqa: E402
import registro as registro_modulo  # noqa: E402

FALLOS = []
COMPROBACIONES = [0]


def prueba(nombre, condicion, detalle=""):
    COMPROBACIONES[0] += 1
    if condicion:
        print("PASA  " + nombre)
    else:
        print("FALLA " + nombre + ("  <- " + str(detalle) if detalle else ""))
        FALLOS.append(nombre)


def titulo(texto):
    print("\n" + texto)
    print("-" * len(texto))


def base_nueva(prefijo="limpia-"):
    """Una `BaseDatos` en una base temporal propia, ya cerrada."""
    carpeta = tempfile.mkdtemp(prefix=prefijo)
    ruta = os.path.join(carpeta, "o.sqlite3")
    base = db_modulo.BaseDatos(ruta)
    return base, ruta


# =============================================================================
# 1. Validacion por topico, y `tipo` como un campo extra mas
# =============================================================================

titulo("1. Validacion por topico")

carga, id_, tipo, err = alertas.validar(
    config.TOPICO_ALERTA_GAS, '{"id":"000000-b0001-0001","gas_adc":2100}')
prueba("gas valido se acepta", err is None and tipo == "gas"
       and id_ == "000000-b0001-0001", err)

carga, id_, tipo, err = alertas.validar(
    config.TOPICO_ALERTA_AYUDA, '{"id":"000000-b0002-0001","origen":"alexa"}')
prueba("ayuda valida se acepta", err is None and tipo == "ayuda", err)

_, _, _, err = alertas.validar(config.TOPICO_ALERTA_GAS,
                               '{"id":"x1","tipo":"ayuda","origen":"alexa"}')
prueba("gas SIN gas_adc se rechaza (el tipo no lo suple)", err is not None, err)

# El `tipo` se captura a proposito. Si se descarta con `_` y se comprueba despues,
# lo que se mira es el de la validación ANTERIOR, que era «ayuda»: la prueba falla
# por un `_` de mas y parece que el campo se esta usando como verdad.
_, id_tipo, tipo_tipo, err = alertas.validar(
    config.TOPICO_ALERTA_GAS, '{"id":"x2","gas_adc":2100,"tipo":"ayuda"}')
prueba("gas con tipo que NO cuadra se ACEPTA, y manda el topico",
       err is None and tipo_tipo == "gas", (err, tipo_tipo))

_, _, _, err = alertas.validar(config.TOPICO_ALERTA_GAS, '{"id":"x3","gas_adc":5000}')
prueba("gas_adc fuera de rango se rechaza", err is not None, err)

_, _, _, err = alertas.validar(config.TOPICO_ALERTA_GAS, "no es json")
prueba("cuerpo que no es JSON se rechaza", err is not None, err)

_, _, _, err = alertas.validar("casa/sala/alerta/otro", '{"id":"x4"}')
prueba("topico que no es de los dos se rechaza", err is not None, err)

# Que `tipo` avise es la otra mitad del asunto: si se acepta en silencio, el que
# publico el mensaje no se entera de que el orquestador lo esta ignorando.
registro_modulo.registro = logging.getLogger("alertas")
log_capturado = []


class _Captura(logging.Handler):
    def emit(self, registro_emitido):
        log_capturado.append(registro_emitido)


manipulador = _Captura()
manipulador.setLevel(logging.WARNING)
alertas.registro.addHandler(manipulador)
alertas.registro.setLevel(logging.WARNING)

log_capturado.clear()
alertas.validar(config.TOPICO_ALERTA_GAS,
                '{"id":"000000-b0003-0001","gas_adc":2100,"tipo":"ayuda"}')
avisos_tipo = [r for r in log_capturado if "tipo" in r.getMessage()]
prueba("un `tipo` que no cuadra deja un WARNING en el log", len(avisos_tipo) == 1,
       [r.getMessage() for r in log_capturado])

log_capturado.clear()
alertas.validar(config.TOPICO_ALERTA_GAS,
                '{"id":"000000-b0003-0002","gas_adc":2100,"inventado":1}')
avisos_extra = [r for r in log_capturado if "inventado" in r.getMessage()]
prueba("cualquier otro campo sobrante tambien avisa", len(avisos_extra) == 1,
       [r.getMessage() for r in log_capturado])

# Y el aviso tiene que decir QUE se ignora, no solo que hay un problema: un
# warning que suena a error hace que alguien vaya a mirar un aviso que funciono.
log_capturado.clear()
alertas.validar(config.TOPICO_ALERTA_GAS,
                '{"id":"000000-b0003-0003","gas_adc":2100,"tipo":"ayuda"}')
texto_aviso = log_capturado[0].getMessage() if log_capturado else ""
prueba("el warning de `tipo` dice que se acepta y se ignora",
       "acepta" in texto_aviso and "ignoran" in texto_aviso, texto_aviso)


# =============================================================================
# 2. La lista de familiares en SQLite, y cada alta se SUMA
# =============================================================================

titulo("2. La lista de familiares")

base, ruta = base_nueva("familiares-")
prueba("una base nueva arranca con cero familiares", base.contar_familiares() == 0)

alta_a = base.registrar_familiar("111", nombre="Marta")
prueba("el primer familiar se da de alta", alta_a and base.contar_familiares() == 1)
prueba("y se escribe su chat en la tabla",
       base.familiar_registrado("111") and not base.familiar_registrado("222"))

alta_b = base.registrar_familiar("222", nombre="Javi")
prueba("el segundo familiar se da de alta SUMADO, no sustituyendo",
       alta_b and base.contar_familiares() == 2)
prueba("el primero sigue en la lista", base.familiar_registrado("111"))
prueba("y el segundo tambien", base.familiar_registrado("222"))

repetido = base.registrar_familiar("111", nombre="Marta")
prueba("reinscribir el mismo familiar no lo duplica",
       repetido is False and base.contar_familiares() == 2, base.contar_familiares())

nombres = {f["chat_id"]: f["nombre"] for f in base.familiares_registrados()}
prueba("los nombres se guardan con su chat", nombres == {"111": "Marta", "222": "Javi"},
       nombres)

# El `chat_id` se guarda como TEXTO y se consulta como texto. Telegram da enteros,
# JSON y el shell dan cadenas, y una comparacion `111 == '111'` es False: si el
# tipo no es el mismo, el familiar esta en la base y `/alertas` no le contesta.
prueba("un familiar buscado como texto se encuentra",
       base.familiar_registrado("111"))
prueba("y no se confunde un chat con el prefijo de otro",
       not base.familiar_registrado("11"), base.familiar_registrado("11"))

# La lista se guarda en SQLite, no en un archivo. Es lo que se comprobo antes
# mirando que no exista `familia.json`; aquí se comprueba que la tabla es la que
# manda y que no hay ningun archivo con un único chat_id al lado.
prueba("la lista vive en la tabla, no en un archivo",
       not os.path.exists(os.path.join(os.path.dirname(ruta), "familia.json")))

base.cerrar()


# =============================================================================
# 3. El fan out: una alerta, un envio por familiar
# =============================================================================

titulo("3. Fan out")

base, _ = base_nueva("fanout-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")

nuevo = base.insertar_alerta("000000-b0004-0001", "gas",
                             '{"id":"000000-b0004-0001","gas_adc":2100}')
prueba("la alerta se encola", nuevo)

envios = base.tomar_envios_pendientes(limite=10)
destinos = sorted(f["chat_id"] for f in envios)
prueba("hay UN envio por familiar, con la misma alerta en los dos",
       destinos == ["111", "222"], destinos)
prueba("los dos envios son de la misma alerta",
       {f["alerta_id"] for f in envios} == {"000000-b0004-0001"})

# Un tercero que entra DESPUES recibe las alertas nuevas, no las de antes. Es lo
# que evita que el nuevo sea el que recibe el historico de la casa.
base.registrar_familiar("333", nombre="Ana")
atrasados = base.fan_out_sin_destino("333")
prueba("un familiar que entra despues NO recibe el historico", atrasados == 0,
       atrasados)
prueba("y sigue sin envios que mandar",
       base.obtener_envio("000000-b0004-0001", "333") is None)

nuevo = base.insertar_alerta("000000-b0004-0002", "ayuda",
                             '{"id":"000000-b0004-0002","origen":"alexa"}')


def destinos_de(id_alerta):
    """Los chats con un envio PENDIENTE de una alerta concreta.

    Se filtra por alerta a proposito: `tomar_envios_pendientes` devuelve la cola
    entera, y al medir «cuantos hay» sin filtrar se cuentan tambien los envios de
    las alertas anteriores, que es como se pasa de «van a los tres» a una lista de
    cinco sin que el código esté mal.
    """
    return sorted(f["chat_id"] for f in base.tomar_envios_pendientes(limite=50)
                  if f["alerta_id"] == id_alerta)


prueba("una alerta nueva si va a los TRES",
       nuevo and destinos_de("000000-b0004-0002") == ["111", "222", "333"],
       destinos_de("000000-b0004-0002"))

# Y un solo `id` no genera una fila por familiar y otra por reintento: el
# `INSERT OR IGNORE` con la clave (alerta_id, chat_id) es lo que lo garantiza.
prueba("volver a encolar el mismo id no crea envios de mas",
       base.insertar_alerta("000000-b0004-0002", "ayuda",
                            '{"id":"000000-b0004-0002","origen":"alexa"}') is False)
prueba("siguen siendo tres envios para esa alerta, no seis",
       destinos_de("000000-b0004-0002") == ["111", "222", "333"],
       destinos_de("000000-b0004-0002"))

base.cerrar()


# =============================================================================
# 4. Reintentos independientes: el fallo de uno no toca al otro
# =============================================================================

titulo("4. Reintentos por familiar")

base, _ = base_nueva("reintentos-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")
base.insertar_alerta("000000-b0005-0001", "gas",
                     '{"id":"000000-b0005-0001","gas_adc":2100}')

# El de 111 falla tres veces seguidas. El de 222, nada.
for intentos in (1, 2, 3):
    base.marcar_envio_fallido("000000-b0005-0001", "111", intentos,
                              int(time.time()) + 60 * intentos, "sin cobertura")

fila_111 = base.obtener_envio("000000-b0005-0001", "111")
fila_222 = base.obtener_envio("000000-b0005-0001", "222")
prueba("el envio de 111 lleva sus tres intentos", fila_111["intentos"] == 3,
       fila_111["intentos"])
prueba("y su proximo intento esta mas lejos que el de 222",
       fila_111["proximo_intento_en"] > fila_222["proximo_intento_en"])
prueba("el de 222 no se ha enterado de nada", fila_222["intentos"] == 0,
       fila_222["intentos"])
prueba("y sigue sin marcar como enviado", fila_222["enviado_en"] is None)
prueba("los dos siguen vivos: un fallo no descarta la alerta",
       base.contar_por_estado().get("pendiente") == 1
       and base.contar_por_estado().get("descartada", 0) == 0,
       base.contar_por_estado())

# La escalera de esperas es la misma para todos, pero cada envio la lleva su cuenta.
base.marcar_enviado("000000-b0005-0001", "222")
prueba("222 queda como enviado", base.obtener_envio("000000-b0005-0001", "222")
       ["enviado_en"] is not None)
prueba("111 sigue pendiente, sin que 222 lo haya arrastrado",
       base.obtener_envio("000000-b0005-0001", "111")["enviado_en"] is None)

# Y con la alerta medio repartida, el estado de la alerta sigue siendo PENDIENTE.
# No es un descuido del desglose: llamar «enviada» a un aviso que una de las dos
# personas no tiene recibido es la forma de que /alertas diga que todo esta bien
# mientras un movil sin cobertura se queda sin saber nada. La honestidad va en dos
# sitios: el estado dice que falta, y el desglose dice a quien.
recientes = base.listar_recientes(limite=1)
fila = recientes[0]
prueba("con una copia sin entregar, la alerta NO se dice enviada",
       fila["estado"] == "pendiente", fila["estado"])
prueba("y el desglose dice 1 de 2",
       (fila["enviados"], fila["sin_mandar"]) == (1, 1),
       (fila["enviados"], fila["sin_mandar"]))

# Un fallo de red, por muchos intentos que se acumulen, NO descarta. Es la razon
# de que `marcar_envio_fallido` no toque la alerta.
for intentos in range(4, 40):
    base.marcar_envio_fallido("000000-b0005-0001", "111", intentos,
                              int(time.time()) + 60 * intentos, "sin cobertura")
prueba("tras 39 intentos, la alerta NO esta descartada",
       base.contar_por_estado().get("descartada", 0) == 0,
       base.contar_por_estado())
prueba("y el envio de 111 sigue en la cola para otro dia",
       base.obtener_envio("000000-b0005-0001", "111")["enviado_en"] is None)

# El único camino a descartar es una carga ilegible, y no lo decide la red.
base.insertar_alerta("000000-b0005-0002", "gas", "esto no es json")
base.marcar_alerta_descartada("000000-b0005-0002", "carga_json ilegible")
prueba("una carga ilegible SI se descarta",
       base.contar_por_estado().get("descartada") == 1,
       base.contar_por_estado())

base.cerrar()


# =============================================================================
# 5. La cola manda a cada familiar por su cuenta
# =============================================================================

titulo("5. La cola con dos familiares")


class RemitenteFalso:
    """Un `Remitente` que falla solo para un chat, sin tocar la red."""

    def __init__(self, que_falla=""):
        self.que_falla = que_falla
        self.enviados = []
        self.token = "x"
        self.disponible = True

    def enviar(self, texto, teclado=None, chat_id=None):
        if str(chat_id) == self.que_falla:
            # Lo que lanza Telegram cuando el bot esta bloqueado o el chat no es el
            # suyo. No importa la clase exacta: la cola captura(Exception) porque un
            # fallo de red puede ser cualquier cosa, y esto prueba justo eso.
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.enviados.append((str(chat_id), texto))
        return len(texto)

    def editar(self, mensaje, texto, teclado=None):
        return True

    def editar_futuro(self, mensaje, texto, teclado=None, chat_id=None):
        # Lo que devuelve la real es un future, porque el manejador hace
        # `await remitente.editar_futuro(...)`. Un stub que devuelva `True` hace
        # fallar ese `await` y el fallo se cuela como si fuera del bot.
        futuro = asyncio.get_event_loop().create_future()
        futuro.set_result(None)
        return futuro


base, _ = base_nueva("cola-")
# Un `chat_id` de Telegram de verdad: nueve o diez digitos. Con «222» el enmascarado
# no hace nada (ver la seccion 12) y la prueba de privacidad no probaria nada.
CHAT_A = "551234567"   # el que falla
CHAT_B = "551234568"   # el que puede
base.registrar_familiar(CHAT_A, nombre="Marta")
base.registrar_familiar(CHAT_B, nombre="Javi")
base.insertar_alerta("000000-b0006-0001", "gas",
                     '{"id":"000000-b0006-0001","gas_adc":2100}')

# CHAT_B es el que falla, y falla siempre. CHAT_A tiene que salir igual.
remitente = RemitenteFalso(que_falla=CHAT_B)
trabajador = cola_modulo.TrabajadorTelegram(base, remitente)
for _ in range(6):
    trabajador._intentar_uno()

destinos = [c for c, _ in remitente.enviados]
prueba("el familiar que falla NO se lleva el aviso de los demas, y el que puede lo "
       "recibe", CHAT_A in destinos, destinos)
prueba("el aviso se reintenta para el que falla, sin rendirse",
       base.obtener_envio("000000-b0006-0001", CHAT_B)["intentos"] >= 1,
       base.obtener_envio("000000-b0006-0001", CHAT_B)["intentos"])
prueba("el que lo recibio queda como enviado y solo una vez",
       base.obtener_envio("000000-b0006-0001", CHAT_A)["enviado_en"] is not None
       and destinos.count(CHAT_A) == 1, destinos)

# Y el log no puede escribir el chat entero. El manejador se pone aquí y no antes
# porque `cola` usa su propio logger, que no es el de `alertas`: sin engancharlo, el
# `log_capturado` sale vacio y la comprobacion de abajo pasa sin comprobar nada.
#
# Antes hay que volver a poner el envio de CHAT_B «tocando ahora»: los seis
# intentos anteriores lo dejaron agendado con la espera creciente, y un envio
# futuristicamente agendado no se toca. Sin esto, el `_intentar_uno()` de abajo no
# hace nada, no hay log, y la prueba de privacidad pasa porque no había nada que
# mirar.
registro_cola = cola_modulo.registro
registro_cola.addHandler(manipulador)
registro_cola.setLevel(logging.WARNING)

base.marcar_envio_fallido("000000-b0006-0001", CHAT_B, 1, int(time.time()) - 1,
                          "sin cobertura")
log_capturado.clear()
remitente2 = RemitenteFalso(que_falla=CHAT_B)
cola_modulo.TrabajadorTelegram(base, remitente2)._intentar_uno()
texto_log = "\n".join(r.getMessage() for r in log_capturado)
prueba("el log del fallo enmascara el chat_id",
       texto_log and CHAT_B not in texto_log
       and db_modulo.enmascarar_chat(CHAT_B) in texto_log, texto_log[:300])

base.cerrar()


# =============================================================================
# 6. El backfill: lo que se acumulo sale; el historico, no
# =============================================================================

titulo("6. Backfill")

base, _ = base_nueva("backfill-")
# Sin familiares: la alerta se guarda esperando, no se pierde.
base.insertar_alerta("000000-b0007-0001", "gas",
                     '{"id":"000000-b0007-0001","gas_adc":2100}')
prueba("una alerta sin familiar queda pendiente, no perdida",
       base.contar_por_estado().get("pendiente") == 1, base.contar_por_estado())
prueba("y no hay ningun envio todavia",
       base.obtener_envio("000000-b0007-0001", "111") is None)

salieron = base.fan_out_sin_destino("111")
prueba("cuando se registra uno, sale lo que estaba esperando", salieron == 1, salieron)
prueba("con un envio para el", base.obtener_envio("000000-b0007-0001", "111") is not None)
prueba("y la alerta deja de estar esperando", base.fan_out_sin_destino("222") == 0)

# La parte que sale cara si no se piensa: una alerta historica que LLEGO a alguien
# en su dia y de la que no se puede saber a quien. Sin el distintivo `fanout_hecho`,
# "no tiene envios" la hace indistinguishable de una que esta esperando, y el
# familiar de manana recibe el historico de la casa como aviso fresco.
base2, _ = base_nueva("backfill-historico-")
base2.conexion.execute(
    "INSERT INTO alertas (id, tipo, carga_json, creado_en, purgable_en, fanout_hecho) "
    "VALUES ('000000-b0007-0002','gas','{\"id\":\"000000-b0007-0002\","
    "\"gas_adc\":2100}', 1, 9999999999, 1)")
base2.conexion.commit()
prueba("una alerta historica sin destino conocido NO se reenvia al nuevo",
       base2.fan_out_sin_destino("111") == 0)
prueba("ni le crea un envio",
       base2.obtener_envio("000000-b0007-0002", "111") is None)
base2.cerrar()


# =============================================================================
# 7. La migración del diseno de un solo destino
# =============================================================================

titulo("7. Migracion desde el esquema viejo")


def base_vieja(ruta, chat_previo=None):
    """La base del diseno de un solo destino: `alertas` con `estado` y sin `envios`.

    Las columnas se copian de las de la instalación real a proposito, incluidas las
    que están en femurino: la versión anterior llamaba `resuelta_en` a la hora de
    resolucion (concertando con «alerta») y a `recibido_en` la de `ids_procesados`.
    Ponerlas bien aquí es lo que hace que la prueba pueda encontrar una migración que
    lea una columna que en la base de verdad no existe.
    """
    conexion = sqlite3.connect(ruta)
    conexion.execute("CREATE TABLE IF NOT EXISTS ids_procesados "
                     "(id TEXT PRIMARY KEY, tipo TEXT, recibido_en INTEGER)")
    conexion.execute(
        "CREATE TABLE alertas ("
        "id TEXT PRIMARY KEY, tipo TEXT NOT NULL, carga_json TEXT NOT NULL, "
        "estado TEXT NOT NULL, intentos INTEGER NOT NULL DEFAULT 0, "
        "proximo_intento_en INTEGER NOT NULL DEFAULT 0, "
        "enviado_en INTEGER, ultimo_error TEXT, "
        "creado_en INTEGER NOT NULL, purgable_en INTEGER NOT NULL DEFAULT 9999999999, "
        "resuelta_en INTEGER)")
    conexion.commit()
    # El indice que traia la versión anterior. Va aquí a proposito: SQLite no deja
    # borrar una columna que un indice menciona, asi que una migración que solo se
    # prueba contra una base sin indices pasa y falla en la instalación de verdad,
    # en el ultimo paso, con la base ya repartida. Los indices se limpian en el
    # esquema, no en la migración.
    conexion.execute("CREATE INDEX IF NOT EXISTS idx_alertas_pendientes "
                     "ON alertas (estado, proximo_intento_en)")
    conexion.execute("CREATE INDEX IF NOT EXISTS idx_alertas_tipo "
                     "ON alertas (tipo, creado_en)")
    conexion.commit()
    conexion.close()
    if chat_previo is not None:
        with open(os.path.join(os.path.dirname(ruta), "familia.json"), "w") as f:
            json.dump({"chat_id": chat_previo, "registrado_en": 1}, f)
    return ruta


def fila_vieja(conexion, id_alerta, tipo, carga, estado, extra_sql="",
               extra=(), resuelta_en=None):
    """Una fila en el esquema viejo.

    `extra_sql` van SIN la coma final, porque lo que viene despues depende de si se
    han pedido columnas extra: el `INSERT` se arma con `enviado_en` y luego, si hay
    `extra`, con las suyas. Poner la coma aquí produce `..., ?, ?, ) VALUES (...)`,
    que es un error de sintaxis de SQLite que sale en la línea de la prueba y hace
    pensar que la migración esta mal.
    """
    columnas = ("id, tipo, carga_json, estado, creado_en, enviado_en, resuelta_en")
    valores = (id_alerta, tipo, carga, estado, 1000,
               1500 if estado in ("enviada", "resuelta") else None, resuelta_en)
    if extra_sql:
        columnas += ", " + extra_sql
        valores += tuple(extra)
    conexion.execute(
        "INSERT INTO alertas (%s) VALUES (%s)"
        % (columnas, ",".join("?" * len(valores))), valores)
    conexion.commit()


# Con `familia.json`: el familiar se copia y cada aviso conserva su estado.
carpeta = tempfile.mkdtemp(prefix="migra-con-")
ruta_vieja = base_vieja(os.path.join(carpeta, "o.sqlite3"), chat_previo="777")
cx = sqlite3.connect(ruta_vieja)
fila_vieja(cx, "000000-b0008-0001", "gas",
           '{"id":"000000-b0008-0001","gas_adc":2100}', "pendiente",
           "intentos, proximo_intento_en", (3, 1700000000))
fila_vieja(cx, "000000-b0008-0002", "gas",
           '{"id":"000000-b0008-0002","gas_adc":2300}', "enviada")
fila_vieja(cx, "000000-b0008-0003", "gas",
           '{"id":"000000-b0008-0003","gas_adc":2400}', "resuelta",
           resuelta_en=1900)
fila_vieja(cx, "000000-b0008-0004", "gas", "carga rota", "descartada")
cx.close()

base = db_modulo.BaseDatos(ruta_vieja)
prueba("el familiar de familia.json pasa a la tabla",
       base.familiar_registrado("777"), base.familiares_registrados())
prueba("y es el unico", base.contar_familiares() == 1, base.contar_familiares())

envio_pendiente = base.obtener_envio("000000-b0008-0001", "777")
prueba("el aviso pendiente sigue en la cola para el mismo familiar",
       envio_pendiente is not None and envio_pendiente["enviado_en"] is None)
prueba("y conserva los intentos que tenia, para no reiniciar la escalera",
       envio_pendiente["intentos"] == 3, envio_pendiente["intentos"])
prueba("y su proximo intento",
       envio_pendiente["proximo_intento_en"] == 1700000000,
       envio_pendiente["proximo_intento_en"])

envio_enviada = base.obtener_envio("000000-b0008-0002", "777")
prueba("el aviso enviado queda enviado, con su hora", envio_enviada is not None
       and envio_enviada["enviado_en"] == 1500, envio_enviada)
envio_resuelta = base.obtener_envio("000000-b0008-0003", "777")
prueba("el aviso resuelto sale como enviado (llego) y con la marca de resuelto",
       envio_resuelta["enviado_en"] == 1500
       and base.obtener_alerta("000000-b0008-0003")["resuelto_en"] is not None)
# Y la marca de resolucion es la de cuando se pulso el boton, no la de cuando llego
# el aviso. La columna se llamaba `resuelta_en` en la versión anterior, en femenino
# concertando con «alerta»; leer `resuelto_en` (la nueva) sale NULL sin dar error, y
# entonces el `COALESCE` de emergencia pone la hora de llegada. El historial
# seguiría contando las nueve resueltas igual de bien, y la fecha estaría mal.
prueba("y conserva la hora en que se resolvio, no la de llegada",
       base.obtener_alerta("000000-b0008-0003")["resuelto_en"] == 1900,
       base.obtener_alerta("000000-b0008-0003")["resuelto_en"])
prueba("la columna vieja `resuelta_en` ya no esta, para que no haya dos verdades",
       "resuelta_en" not in {f["name"] for f in
                             base.conexion.execute("PRAGMA table_info(alertas)")},
       [f["name"] for f in base.conexion.execute("PRAGMA table_info(alertas)")])
# El indice de la cola vieja no puede sobrevivir a sus columnas, y borrarlo es parte
# de la migración: sin esto el servicio no arranca nunca con una base antigua.
indices = {f["name"] for f in base.conexion.execute(
    "SELECT name FROM sqlite_master WHERE type = 'index'")}
prueba("el indice de la cola vieja se ha ido con sus columnas",
       "idx_alertas_pendientes" not in indices, sorted(indices))
prueba("el indice del historial se queda, porque sus columnas siguen",
       "idx_alertas_tipo" in indices, sorted(indices))
prueba("y el indice del fan out esta creado, para no recorrer la tabla entera",
       "idx_alertas_esperando" in indices, sorted(indices))

conteo = base.contar_por_estado()
prueba("el aviso descartado NO se manda a nadie, ni ahora ni luego",
       conteo.get("descartada") == 1 and conteo.get("enviada") == 1
       and conteo.get("pendiente") == 1, conteo)
prueba("y no tiene envio creado",
       base.obtener_envio("000000-b0008-0004", "777") is None)

# El archivo se aparta, no se borra, y el segundo arranque no rompe nada.
registro_modulo.migrar_familia_legacy(base)
prueba("el familia.json se renombra a .migrado",
       not os.path.exists(os.path.join(carpeta, "familia.json"))
       and os.path.exists(os.path.join(carpeta, "familia.json.migrado")))
base.cerrar()

base = db_modulo.BaseDatos(ruta_vieja)
registro_modulo.migrar_familia_legacy(base)
prueba("abrir la base otra vez no duplica al familiar",
       base.contar_familiares() == 1, base.contar_familiares())
prueba("ni reinicia los intentos de la alerta pendiente",
       base.obtener_envio("000000-b0008-0001", "777")["intentos"] == 3,
       base.obtener_envio("000000-b0008-0001", "777")["intentos"])
prueba("ni vuelve a mandar el aviso ya enviado",
       base.obtener_envio("000000-b0008-0002", "777")["enviado_en"] == 1500)
base.cerrar()

# Sin `familia.json`: los avisos que}LLEGARON no se reenvian a quien se registre
# despues; los que estaban pendientes, si. Es la parte que sale mal si se
# confunde "sin envios" con "esperando a alguien".
carpeta = tempfile.mkdtemp(prefix="migra-sin-")
ruta_vieja = base_vieja(os.path.join(carpeta, "o.sqlite3"), chat_previo=None)
cx = sqlite3.connect(ruta_vieja)
fila_vieja(cx, "000000-b0008-0011", "gas",
           '{"id":"000000-b0008-0011","gas_adc":2100}', "pendiente")
fila_vieja(cx, "000000-b0008-0012", "gas",
           '{"id":"000000-b0008-0012","gas_adc":2300}', "enviada")
fila_vieja(cx, "000000-b0008-0013", "gas",
           '{"id":"000000-b0008-0013","gas_adc":2400}', "resuelta")
cx.close()

base = db_modulo.BaseDatos(ruta_vieja)
prueba("sin familiar previo, no hay ninguno en la tabla", base.contar_familiares() == 0)
salieron = base.fan_out_sin_destino("555")
prueba("solo sale el aviso que estaba PENDIENTE de verdad", salieron == 1, salieron)
prueba("el pendiente es el que estaba pendiente",
       base.obtener_envio("000000-b0008-0011", "555") is not None)
prueba("el que ya se habia enviado NO se reenvia como si fuera nuevo",
       base.obtener_envio("000000-b0008-0012", "555") is None)
prueba("ni el que se habia resuelto",
       base.obtener_envio("000000-b0008-0013", "555") is None)
prueba("pero los dos siguen en el historial, con su estado",
       base.contar_por_estado().get("enviada") == 1
       and base.contar_por_estado().get("resuelta") == 1,
       base.contar_por_estado())
base.cerrar()


# =============================================================================
# 8. `/registro`: el silencio es de su propio chat, y las altas se suman
# =============================================================================

titulo("8. /registro")


class MensajeFalso:
    def __init__(self):
        self.respuestas = []

    async def reply_text(self, texto, **kw):
        self.respuestas.append(texto)


class ChatFalso:
    def __init__(self, chat_id, tipo="private"):
        self.id = chat_id
        self.type = tipo


class UpdateFalso:
    def __init__(self, chat_id, tipo="private", mensaje=None):
        self.effective_chat = ChatFalso(chat_id, tipo)
        self.effective_message = mensaje or MensajeFalso()

    @property
    def contestado(self):
        return self.effective_message.respuestas


class ContextoFalso:
    def __init__(self, argumentos):
        self.args = argumentos


base, _ = base_nueva("registro-")
base.registrar_familiar("111", nombre="Marta")
# Esta alerta entra con un familiar en la lista, y se reparte entre los que hay.
# Cuando se registre el segundo NO le toca: la regla es «un envio por cada familiar
# activo cuando llega la alerta», y el segundo todavia no lo era. Si se le mandara,
# a los tres dias de coger el telefono le llegarian avisos de la semana pasada, que
# es lo único que hace que alguien deje de fiarse del bot.
base.insertar_alerta("000000-b0009-0001", "gas",
                     '{"id":"000000-b0009-0001","gas_adc":2100}')
cliente_bot = bot_modulo.ClienteBot(base, RemitenteFalso())
ruta_codigos = os.path.join(datos, "codigos.json")

# El silencio: solo para el chat que ya esta dentro.
u = UpdateFalso("111")
asyncio.run(cliente_bot.cmd_registro(u, ContextoFalso([])))
prueba("al chat YA registrado no se le responde NADA (ni un 'ya estas registrado')",
       u.contestado == [], u.contestado)

u = UpdateFalso("111")
asyncio.run(cliente_bot.cmd_registro(u, ContextoFalso(["codigo-inventado"])))
prueba("ni siquiera con un codigo: el silencio no depende de los argumentos",
       u.contestado == [], u.contestado)

# Un chat nuevo SI recibe respuesta, y necesita código.
u = UpdateFalso("222")
asyncio.run(cliente_bot.cmd_registro(u, ContextoFalso([])))
prueba("a un chat NUEVO se le pide el codigo", len(u.contestado) == 1
       and "Falta el codigo" in u.contestado[0], u.contestado)
prueba("y se le dice que los avisos serian para los dos",
       "los dos" in u.contestado[0], u.contestado[0])

u = UpdateFalso("222")
asyncio.run(cliente_bot.cmd_registro(u, ContextoFalso(["codigo-que-no-existe"])))
prueba("un codigo invalido se rechaza y se pide otro",
       len(u.contestado) == 1 and "No se pudo registrar" in u.contestado[0],
       u.contestado)
prueba("y el chat NO se ha registrado igual", not base.familiar_registrado("222"))

codigo = registro_modulo.generar_codigo(ruta_codigos)
registro_modulo.cargar_codigos(ruta_codigos)
u = UpdateFalso("222")
asyncio.run(cliente_bot.cmd_registro(u, ContextoFalso([codigo, "Javi"])))
prueba("con un codigo bueno el segundo familiar entra", len(u.contestado) == 1
       and "Listo" in u.contestado[0], u.contestado)
prueba("y se SUMA a la lista, sin quitar al primero",
       base.contar_familiares() == 2
       and base.familiar_registrado("111") and base.familiar_registrado("222"),
       base.contar_familiares())
prueba("y la alerta que ya se habia repartido NO se le reenvia como nueva",
       base.obtener_envio("000000-b0009-0001", "222") is None,
       base.obtener_envio("000000-b0009-0001", "222"))
prueba("el codigo queda gastado",
       registro_modulo.comprobar_codigo(codigo) == "ese codigo ya se uso")

# El otro caso, el que si es un backfill: la alerta entra sin NINGUN familiar, asi
# que no se le ofrecio a nadie y sigue siendo de ahora. Sale con el primero que se
# registre. Sin esta distinción, «repartir entre los que hay» y «mandar lo que
# quedo colgando» serían la misma cosa, y habria que elegir entre perder avisos y
# reenviar el historico.
base_espera, _ = base_nueva("registro-espera-")
base_espera.insertar_alerta("000000-b0009-0002", "gas",
                            '{"id":"000000-b0009-0002","gas_adc":2200}')
bot_espera = bot_modulo.ClienteBot(base_espera, RemitenteFalso())
codigo_espera = registro_modulo.generar_codigo(ruta_codigos)
registro_modulo.cargar_codigos(ruta_codigos)
u_espera = UpdateFalso("333")
asyncio.run(bot_espera.cmd_registro(u_espera, ContextoFalso([codigo_espera])))
prueba("la alerta que no habia tenido a nadie sale al registrarse el primero",
       base_espera.obtener_envio("000000-b0009-0002", "333") is not None,
       base_espera.obtener_envio("000000-b0009-0002", "333"))
prueba("y el bot se lo dice, para que no se quede creyendo que se perdio",
       "esperando" in u_espera.contestado[0], u_espera.contestado[0])
base_espera.cerrar()

# Y el primero sigue siendo familiar de pleno derecho.
u = UpdateFalso("111")
asyncio.run(cliente_bot.cmd_alertas(u, ContextoFalso([])))
prueba("el primer familiar sigue pudiendo usar /alertas",
       len(u.contestado) == 1 and "Estado de las alertas" in u.contestado[0],
       u.contestado)

# En un grupo se avisa de que el comando es de chat privado, pero no se cuenta
# NADA. Lo que no puede aparecer en un canal donde lo lean mil personas es el
# número de nadie, si esta registrado, y el historial de la casa.
u_grupo_registro = UpdateFalso("222", tipo="group")
asyncio.run(cliente_bot.cmd_registro(u_grupo_registro, ContextoFalso([])))
texto_grupo = "\n".join(u_grupo_registro.contestado)
prueba("en un grupo, /registro solo dice que es de chat privado",
       all("chat privado" in r for r in u_grupo_registro.contestado)
       and u_grupo_registro.contestado, u_grupo_registro.contestado)
prueba("y no suelta ni el chat ni el codigo ni el estado de las alertas",
       "222" not in texto_grupo and "Listo" not in texto_grupo
       and "Estado de las alertas" not in texto_grupo, texto_grupo)

u_grupo_alertas = UpdateFalso("222", tipo="group")
asyncio.run(cliente_bot.cmd_alertas(u_grupo_alertas, ContextoFalso([])))
texto_grupo = "\n".join(u_grupo_alertas.contestado)
prueba("en un grupo, /alertas tampoco suelta el historial",
       "Estado de las alertas" not in texto_grupo
       and "pendiente" not in texto_grupo, texto_grupo)

base.cerrar()


# =============================================================================
# 9. `/alertas`: a cualquier familiar registrado, y a nadie mas
# =============================================================================

titulo("9. /alertas")

base, _ = base_nueva("alertas-cmd-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")
cliente_bot = bot_modulo.ClienteBot(base, RemitenteFalso())

base.insertar_alerta("000000-b0010-0001", "gas",
                     '{"id":"000000-b0010-0001","gas_adc":2100}')
base.marcar_enviado("000000-b0010-0001", "111")

for chat in ("111", "222"):
    u = UpdateFalso(chat)
    asyncio.run(cliente_bot.cmd_alertas(u, ContextoFalso([])))
    prueba("/alertas responde al familiar registrado %s" % chat,
           len(u.contestado) == 1 and "Estado de las alertas" in u.contestado[0],
           u.contestado)

u = UpdateFalso("999")
asyncio.run(cliente_bot.cmd_alertas(u, ContextoFalso([])))
prueba("/alertas NO responde a un chat que no esta registrado", u.contestado == [],
       u.contestado)

u = UpdateFalso("111")
asyncio.run(cliente_bot.cmd_alertas(u, ContextoFalso([])))
texto = u.contestado[0]
# Con una de las dos copias sin entregar la alerta esta PENDIENTE. Decirla
# «enviada» haría que /alertas>dijera que el aviso esta entregado mientras una de
# las dos personas no lo tiene.
prueba("el conteo de estados sale, y dice pendiente lo que falta",
       "pendiente: 1" in texto and "enviada: 0" in texto, texto)
prueba("con dos personas registradas, avisa de cuantas hay",
       "2 personas registradas" in texto, texto)
prueba("y dice cuantas copias salieron y cuantas no",
       "(1 de 2)" in texto, texto)

base.cerrar()


# =============================================================================
# 10. El boton «Ya lo atendi»: cualquier familiar, editando en SU chat
# =============================================================================

titulo("10. El boton de resolver")


class ConsultaFalsa:
    def __init__(self, mensaje, id_alerta):
        self.message = mensaje
        self.data = bot_modulo.PREFIJO_CODIGO + id_alerta
        self.answer_texts = []
        self.editado = []

    async def answer(self, texto, show_alert=False):
        self.answer_texts.append(texto)

    async def edit_message_text(self, texto, reply_markup=None, **kw):
        self.editado.append(texto)


class UpdateConsulta(UpdateFalso):
    def __init__(self, chat_id, consulta):
        super().__init__(chat_id)
        self.callback_query = consulta


base, _ = base_nueva("p17-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")


class RemitenteQueAnota:
    def __init__(self):
        self.ediciones = []
        self.token = "x"
        self.disponible = True

    def enviar(self, texto, teclado=None, chat_id=None):
        return len(texto)

    def editar(self, mensaje, texto, teclado=None):
        self.ediciones.append((mensaje, texto, teclado))
        return True

    def editar_futuro(self, mensaje, texto, teclado=None, chat_id=None):
        self.ediciones.append((chat_id, texto, teclado))
        futuro = asyncio.get_event_loop().create_future()
        futuro.set_result(None)
        return futuro


remitente = RemitenteQueAnota()
cliente_bot = bot_modulo.ClienteBot(base, remitente)
base.insertar_alerta("000000-b0011-0001", "gas",
                     '{"id":"000000-b0011-0001","gas_adc":2100}')

# Pulsa el SEGUNDO familiar, no el primero. Es el caso que con el diseño de un
# solo destino no se podia ni plantear.
consulta = ConsultaFalsa("mensaje", "000000-b0011-0001")
u = UpdateConsulta("222", consulta)
asyncio.run(cliente_bot.cmd_atendi(update=u, contexto=ContextoFalso([])))
prueba("el boton lo puede pulsar CUALQUIER familiar registrado",
       base.obtener_alerta("000000-b0011-0001")["resuelto_en"] is not None,
       base.obtener_alerta("000000-b0011-0001"))
prueba("la edicion va al chat de quien pulso", remitente.ediciones
       and remitente.ediciones[0][0] == "222", remitente.ediciones)
prueba("y el mensaje editado dice ATENDIDO",
       remitente.ediciones and "ATENDIDO" in remitente.ediciones[0][1])
prueba("y sin boton, para que no se pueda pulsar otra vez",
       remitente.ediciones and remitente.ediciones[0][2] is None)
prueba("el boton contesta al que pulso",
       consulta.answer_texts and "Ya esta" in consulta.answer_texts[0],
       consulta.answer_texts)

# Un chat que no esta en la lista no resuelve ni lee el aviso.
base.insertar_alerta("000000-b0011-0002", "gas",
                     '{"id":"000000-b0011-0002","gas_adc":2100}')
consulta = ConsultaFalsa("mensaje", "000000-b0011-0002")
u = UpdateConsulta("999", consulta)
asyncio.run(cliente_bot.cmd_atendi(update=u, contexto=ContextoFalso([])))
prueba("un chat que no esta registrado NO resuelve la alerta",
       base.obtener_alerta("000000-b0011-0002")["resuelto_en"] is None)
prueba("y se le avisa por que",
       consulta.answer_texts and "registrados" in consulta.answer_texts[0],
       consulta.answer_texts)

base.cerrar()


# =============================================================================
# 11. El CLI de familiares
# =============================================================================

titulo("11. CLI de familiares")

base, ruta = base_nueva("cli-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")
base.insertar_alerta("000000-b0012-0001", "gas",
                     '{"id":"000000-b0012-0001","gas_adc":2100}')

salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    codigo_salida = registro_modulo._listar_familiares(base)
texto_salida = salida.getvalue()
# `_listar_familiares` devuelve el CODIGO DE SALIDA (0 = todo bien), no cuantos hay.
# El número esta en la línea que imprime. Confundir las dos cosas hace que la prueba
# falle mirando el return cuando el listado esta perfecto.
prueba("`familiares` lista los dos y sale con codigo 0",
       codigo_salida == 0 and "111" in texto_salida and "222" in texto_salida,
       texto_salida)
prueba("y sale con su nombre", "Marta" in texto_salida and "Javi" in texto_salida)

# Dar de baja a uno: sale de la lista, y lo que le quedaba por recibir se cancela.
salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    cancelados = registro_modulo._dar_de_baja(base, "111")
prueba("`baja` quita al familiar de la lista",
       not base.familiar_registrado("111") and base.contar_familiares() == 1,
       base.contar_familiares())
# Tambien un código de salida; lo cancelado se comprueba en la base.
prueba("`baja` sale con codigo 0", cancelados == 0, cancelados)
prueba("y le dice cuantos avisos le cancela",
       "1 aviso(s)" in salida.getvalue(), salida.getvalue())
prueba("y explica que los ya entregados no se pueden deshacer",
       "no se pueden deshacer" in salida.getvalue(), salida.getvalue())
prueba("el envio cancelado no se manda nunca",
       base.obtener_envio("000000-b0012-0001", "111") is None)
prueba("el otro familiar no se entera: su envio sigue ahi",
       base.obtener_envio("000000-b0012-0001", "222") is not None)
prueba("y un boton suyo sigue resolviendo la alerta",
       base.obtener_alerta("000000-b0012-0001") is not None)

# Un chat que no esta no es un error de lo grave, pero tampoco un exito.
salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    resultado = registro_modulo._dar_de_baja(base, "no-existe")
# Codigo 1, no 0: no es un error grave, pero tampoco se ha hecho nada, y un shell
# que no distingue «baja hecha» de «no había nadie» no sirve para scripts.
prueba("dar de baja a uno que no esta avisa, y sale con codigo 1",
       resultado == 1, resultado)

base.cerrar()


# =============================================================================
# 12. Que ningun chat_id salga entero al log
# =============================================================================

titulo("12. Privacidad de los logs")

prueba("enmascarar deja los ultimos cuatro digitos y tapa el resto",
       db_modulo.enmascarar_chat("123456789") == "...6789",
       db_modulo.enmascarar_chat("123456789"))
prueba("y dos familiares distintos se distinguen sin publicar el entero",
       db_modulo.enmascarar_chat("551234567") != db_modulo.enmascarar_chat("551234568"),
       (db_modulo.enmascarar_chat("551234567"),
        db_modulo.enmascarar_chat("551234568")))
prueba("y un chat corto queda entero, porque enmascararlo no aporta nada",
       db_modulo.enmascarar_chat("111") == "111",
       db_modulo.enmascarar_chat("111"))

# El caso real: el log del servicio entero con dos familiares y un envio que falla.
base, _ = base_nueva("logs-")
base.registrar_familiar("987654321", nombre="Marta")
base.registrar_familiar("987654322", nombre="Javi")
base.insertar_alerta("000000-b0013-0001", "gas",
                     '{"id":"000000-b0013-0001","gas_adc":2100}')

registro_cola = cola_modulo.registro
registro_bot = bot_modulo.registro
for registro_ in (registro_cola, registro_bot):
    registro_.addHandler(manipulador)
    registro_.setLevel(logging.WARNING)

log_capturado.clear()
remitente = RemitenteFalso(que_falla="987654322")
cola_modulo.TrabajadorTelegram(base, remitente)._intentar_uno()

# Un bot NUEVO sobre esta base: el de las secciones 8 y 9 sobrevive con la suya ya
# cerrada, y preguntar a una `BaseDatos` cerrada tira «Cannot operate on a closed
# database». El fallo sale aquí, en la línea de la prueba, y parece del código.
bot_privado = bot_modulo.ClienteBot(base, RemitenteFalso())

u = UpdateFalso("987654321")
asyncio.run(bot_privado.cmd_registro(u, ContextoFalso([])))
consulta = ConsultaFalsa("mensaje", "000000-b0013-0001")
asyncio.run(bot_privado.cmd_atendi(update=UpdateConsulta("987654321", consulta),
                                   contexto=ContextoFalso([])))

texto_log = "\n".join(r.getMessage() for r in log_capturado)
prueba("el log del servicio NO lleva ningun chat_id entero",
       "987654321" not in texto_log and "987654322" not in texto_log, texto_log[:400])
prueba("pero si lleva la parte enmascarada, para poder diagnosticar",
       "..." in texto_log and "4321" in texto_log, texto_log[:400])
prueba("y los id de alerta, que no son de nadie, salen enteros",
       "000000-b0013-0001" in texto_log, texto_log[:400])

base.cerrar()


# =============================================================================
# Resumen
# =============================================================================

print("\n" + "=" * 66)
print("comprobaciones: %d" % COMPROBACIONES[0])
print("fallos: %d" % len(FALLOS))
for nombre in FALLOS:
    print("  - " + nombre)
sys.exit(1 if FALLOS else 0)
