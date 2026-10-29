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
  3b. Desactivar sin borrar: los inactivos salen del fan out, y reactivar no
      reenvía lo que se les acumuló mientras estaban fuera.
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
import re
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
import inactividad as inactividad_modulo  # noqa: E402
import mqtt_cliente  # noqa: E402
import panel_acceso as panel_acceso_modulo  # noqa: E402
import recordatorios as recordatorios_modulo  # noqa: E402
import registro as registro_modulo  # noqa: E402
import textos  # noqa: E402
import voz  # noqa: E402
import orquestador as orquestador_modulo  # noqa: E402

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
# 3b. Desactivar sin borrar: los inactivos salen del fan out
# =============================================================================

titulo("3b. Familiares activos e inactivos")

base, _ = base_nueva("activos-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")

# Un alta normal nace ACTIVA (`activo = 1`, el DEFAULT de la columna). Es lo que
# deja a los que ya estaban cuando llega la migración y a los que se dan de alta
# después exactamente igual: nadie queda sin avisar por no pedir el flag.
prueba("un alta nueva nace activa",
       [f["chat_id"] for f in base.familiares_activos()] == ["111", "222"],
       base.familiares_activos())
prueba("y el total cuenta a los dos",
       base.contar_familiares() == 2, base.contar_familiares())

# La desactivación NO borra: la fila sigue (es evidencia, como la «Prueba
# Publica» de P20), pero deja de ser un destino del fan out. Son dos listas
# distintas, y la prueba es que no se confunden.
base.marcar_familiar_inactivo("222")
prueba("inactivar saca al chat de los ACTIVOS",
       [f["chat_id"] for f in base.familiares_activos()] == ["111"],
       base.familiares_activos())
prueba("pero el registro sigue en la tabla",
       base.familiar_registrado("222")
       and any(f["chat_id"] == "222" and f["activo"] == 0
               for f in base.familiares_registrados()),
       base.familiares_registrados())
prueba("el total no pierde al desactivado",
       base.contar_familiares() == 2, base.contar_familiares())

# El fan out reparte entre los ACTIVOS en el momento de encolar. Una alerta que
# entra con "222" inactivo no le crea un envio, active o no después, y dar de
# alta a un tercero no reenvia nada del pasado.
base.insertar_alerta("000000-b0004-0101", "gas",
                     '{"id":"000000-b0004-0101","gas_adc":2100}')
base.registrar_familiar("333", nombre="Ana")
base.insertar_alerta("000000-b0004-0102", "gas",
                     '{"id":"000000-b0004-0102","gas_adc":2200}')
prueba("con un inactivo, las alertas solo van a los activos",
       sorted(f["chat_id"] for f in base.tomar_envios_pendientes(limite=50)
              if f["alerta_id"] == "000000-b0004-0102") == ["111", "333"],
       base.tomar_envios_pendientes(limite=50))

# Inactivar CANCELA los envios que le quedaban sin mandar, igual que hace la baja:
# si se dejaran, el trabajador de la cola los reintentaria para siempre contra un
# chat apagado.
base.marcar_familiar_activo("222")
base.insertar_alerta("000000-b0004-0103", "gas",
                     '{"id":"000000-b0004-0103","gas_adc":2300}')
prueba("con los tres ACTIVOS la alerta va a los tres",
       sorted(f["chat_id"] for f in base.tomar_envios_pendientes(limite=50)
              if f["alerta_id"] == "000000-b0004-0103") == ["111", "222", "333"],
       base.tomar_envios_pendientes(limite=50))
base.marcar_familiar_inactivo("222")
prueba("inactivar CANCELA su envio pendiente",
       base.obtener_envio("000000-b0004-0103", "222") is None,
       base.obtener_envio("000000-b0004-0103", "222"))
prueba("y a los otros dos no les toca nada",
       base.obtener_envio("000000-b0004-0103", "111") is not None
       and base.obtener_envio("000000-b0004-0103", "333") is not None,
       (base.obtener_envio("000000-b0004-0103", "111"),
        base.obtener_envio("000000-b0004-0103", "333")))

# Pedir inactivar a un inactivo (o activar a un activo) no hace nada: devuelve
# False, y así el CLI puede distinguir «se hizo» de «ya estaba así».
prueba("inactivar a un inactivo no hace nada",
       base.marcar_familiar_inactivo("222") is False)

# Reactivar solo vuelve a poner al chat en el fan out para lo NUEVO. Lo que se
# encolo mientras estuvo inactivo no se le reenvia, igual que no se le reenvia a
# un recién registrado el historico: en el momento de cada alerta no estaba activo.
base.marcar_familiar_activo("222")
prueba("reactivar vuelve a meterlo en el fan out",
       [f["chat_id"] for f in base.familiares_activos()] == ["111", "222", "333"],
       base.familiares_activos())
base.insertar_alerta("000000-b0004-0104", "gas",
                     '{"id":"000000-b0004-0104","gas_adc":2400}')
prueba("al reactivar solo recibe lo NUEVO, no lo de su estancia inactiva",
       base.obtener_envio("000000-b0004-0103", "222") is None
       and base.obtener_envio("000000-b0004-0104", "222") is not None,
       (base.obtener_envio("000000-b0004-0103", "222"),
        base.obtener_envio("000000-b0004-0104", "222")))
prueba("reactivar a un activo no hace nada",
       base.marcar_familiar_activo("111") is False)
base.cerrar()

# La migración de la 3 a la 4, igual que se probo la de la 2 a la 3: una base que
# ya estaba en la 3 (la columna NO existia) la gana al abrirla. Es el caso de la
# instalacion real: `esquema.sql` no puede tocar una tabla que ya existe, asi que
# la columna solo puede salir del `ALTER TABLE` de la migración.
ruta_v3 = os.path.join(tempfile.mkdtemp(prefix="v3-"), "o.sqlite3")
con = sqlite3.connect(ruta_v3)
con.executescript(db_modulo.leer_esquema())
con.execute("ALTER TABLE familiares DROP COLUMN activo")
con.execute("PRAGMA user_version = 3")
con.commit()
con.close()

base_v3 = db_modulo.BaseDatos(ruta_v3)
columnas_fam = {f["name"] for f in base_v3.conexion.execute(
    "PRAGMA table_info(familiares)").fetchall()}
prueba("una base en la versión 3 gana `activo` al abrirla",
       "activo" in columnas_fam, sorted(columnas_fam))
prueba("y sube a la versión 4 del esquema",
       base_v3.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA)
base_v3.registrar_familiar("555", nombre="Marta")
prueba("una alta en la base migrada nace activa",
       [f["chat_id"] for f in base_v3.familiares_activos()] == ["555"],
       base_v3.familiares_activos())
base_v3.cerrar()


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
       and "Falta el código" in u.contestado[0], u.contestado)
prueba("y se le dice que ya hay una persona y que ambos recibirán las alertas",
       "Ya hay una persona registrada" in u.contestado[0]
       and "ambos recibirán" in u.contestado[0], u.contestado[0])

u = UpdateFalso("222")
asyncio.run(cliente_bot.cmd_registro(u, ContextoFalso(["codigo-que-no-existe"])))
prueba("un codigo invalido se rechaza y se pide otro",
       len(u.contestado) == 1
       and u.contestado[0] == textos.registro_fallido(textos.MOTIVO_NO_EXISTE),
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
       registro_modulo.comprobar_codigo(codigo) == textos.MOTIVO_YA_USADO)

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
       "le llegará un aviso que estaba pendiente" in u_espera.contestado[0],
       u_espera.contestado[0])
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
       "Pendientes: 1" in texto and "Enviadas: 0" in texto, texto)
prueba("con dos personas registradas, avisa de cuantas hay",
       "2 personas registradas" in texto, texto)
prueba("y dice cuantas copias salieron y cuantas no",
       "(enviada a 1 de 2 personas)" in texto, texto)

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
prueba("y el mensaje editado dice ATENDIDA",
       remitente.ediciones and "ATENDIDA." in remitente.ediciones[0][1])
prueba("y sin boton, para que no se pueda pulsar otra vez",
       remitente.ediciones and remitente.ediciones[0][2] is None)
prueba("el boton contesta al que pulso",
       consulta.answer_texts and consulta.answer_texts[0] == "Ya está atendida.",
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
# 14. Panel web: enlaces de un solo uso y sesiones
# =============================================================================
print("\n14. Panel web")
print("-------------")

import hashlib  # noqa: E402


def hash_(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


base = db_modulo.BaseDatos(os.path.join(datos, "panel.sqlite3"))
ahora = int(time.time())

prueba("la base nueva queda en la última versión del esquema",
       base.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA)

tablas = {f[0] for f in base.conexion.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
prueba("existen las dos tablas del panel",
       {"panel_enlaces", "panel_tokens"} <= tablas)

indices = {f[0] for f in base.conexion.execute(
    "SELECT name FROM sqlite_master WHERE type='index'")}
prueba("y sus tres índices",
       {"idx_panel_enlaces_expira", "idx_panel_tokens_chat",
        "idx_panel_tokens_caduca"} <= indices)

# --- La migración desde una base que ya estaba en la versión 1 -------------
ruta_v1 = os.path.join(datos, "panel-v1.sqlite3")
con = sqlite3.connect(ruta_v1)
con.executescript(db_modulo.leer_esquema())
con.execute("DROP TABLE panel_enlaces")
con.execute("DROP TABLE panel_tokens")
con.execute("PRAGMA user_version = 1")
con.commit()
con.close()

base_v1 = db_modulo.BaseDatos(ruta_v1)
prueba("una base en la versión 1 sube a la última",
       base_v1.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA)
prueba("y las tablas se crean también al migrar, no solo al instalar de nuevo",
       {"panel_enlaces", "panel_tokens"} <= {
           f[0] for f in base_v1.conexion.execute(
               "SELECT name FROM sqlite_master WHERE type='table'")})
base_v1.cerrar()

# --- Alta y canje ----------------------------------------------------------
base.registrar_familiar("1001", nombre="Del panel")
prueba("el familiar queda registrado", base.familiar_registrado("1001"))

base.crear_enlace_panel(hash_("codigo1"), "1001", ahora + 600, ahora)
prueba("un código válido se canjea",
       base.tomar_enlace_panel(hash_("codigo1"), ahora) is not None)
prueba("y NO se puede canjear dos veces: es de un solo uso",
       base.tomar_enlace_panel(hash_("codigo1"), ahora) is None)

base.crear_enlace_panel(hash_("vencido"), "1001", ahora - 1, ahora - 3600)
prueba("un código caducado no se canjea",
       base.tomar_enlace_panel(hash_("vencido"), ahora) is None)
prueba("ni uno que no existe",
       base.tomar_enlace_panel(hash_("nada"), ahora) is None)

# --- Sesiones --------------------------------------------------------------
base.crear_token_panel(hash_("tok1"), "1001", ahora + 86400, ahora)
prueba("la sesión nueva vale", base.sesion_panel(hash_("tok1"), ahora) is not None)
prueba("un hash que no existe no vale",
       base.sesion_panel(hash_("otro"), ahora) is None)

base.marcar_token_usado(hash_("tok1"), ahora + 10)
prueba("se anota el último uso",
       base.conexion.execute(
           "SELECT ultimo_uso_en FROM panel_tokens WHERE token_hash = ?",
           (hash_("tok1"),)).fetchone()[0] == ahora + 10)

prueba("revocar una sesión viva lo dice", base.revocar_token_panel(hash_("tok1"), ahora + 20))
prueba("revocarla otra vez no", not base.revocar_token_panel(hash_("tok1"), ahora + 21))
prueba("y un token revocado ya no vale",
       base.sesion_panel(hash_("tok1"), ahora + 30) is None)

base.crear_token_panel(hash_("cad"), "1001", ahora - 10, ahora - 20)
prueba("un token caducado no vale", base.sesion_panel(hash_("cad"), ahora) is None)

# --- La baja cierra también el panel --------------------------------------
base.crear_token_panel(hash_("vivo"), "1001", ahora + 86400, ahora)
prueba("dar de baja funciona", base.dar_de_baja("1001"))
prueba("y el token de esa persona deja de servir",
       base.sesion_panel(hash_("vivo"), ahora) is None)

# --- La purga --------------------------------------------------------------
base.registrar_familiar("2002", nombre="Segundo")
base.crear_enlace_panel(hash_("usado"), "2002", ahora + 600, ahora)
base.tomar_enlace_panel(hash_("usado"), ahora)
base.crear_enlace_panel(hash_("muerto"), "2002", ahora - 10, ahora - 20)
base.crear_token_panel(hash_("t1"), "2002", ahora - 40 * 86400, ahora - 41 * 86400)
base.crear_token_panel(hash_("t2"), "2002", ahora + 86400, ahora)
base.revocar_token_panel(hash_("t2"), ahora - 40 * 86400)

base.purgar(ahora)
prueba("la purga respeta un enlace usado hace un segundo",
       base.conexion.execute(
           "SELECT COUNT(*) FROM panel_enlaces WHERE codigo_hash = ?",
           (hash_("usado"),)).fetchone()[0] == 1)

base.purgar(ahora + 2 * 86400)


def queda(clave, tabla="panel_enlaces"):
    # La columna se llama distinto en las dos tablas, y pasarla a mano es mejor que
    # adivinar: si las dos usaran `hash`, el error se veria aqui y no en la purga.
    columna = "token_hash" if tabla == "panel_tokens" else "codigo_hash"
    return base.conexion.execute(
        "SELECT COUNT(*) FROM %s WHERE %s = ?" % (tabla, columna),
        (clave,)).fetchone()[0]


prueba("la purga se lleva el enlace usado hace más de un día",
       queda(hash_("usado")) == 0)
prueba("y el que caducó sin usarse, que si no se acumularían para siempre",
       queda(hash_("muerto")) == 0)
prueba("se lleva el token caducado hace más de 30 días",
       queda(hash_("t1"), "panel_tokens") == 0)
prueba("y el revocado hace más de 30 días",
       queda(hash_("t2"), "panel_tokens") == 0)

base.crear_token_panel(hash_("t3"), "2002", ahora + 86400, ahora)
base.purgar(ahora)
prueba("pero NO se lleva una sesión viva", queda(hash_("t3"), "panel_tokens") == 1)

base.cerrar()


# =============================================================================
# 15. Resolver una alerta avisa al satélite UNA vez
# =============================================================================
print("\n15. Resolver una alerta")
print("----------------------")

base = db_modulo.BaseDatos(os.path.join(datos, "resolver.sqlite3"))
base.registrar_familiar("3003", nombre="Atiende")
base.insertar_alerta("al1", "gas", '{"valor": 400}', chat_ids=["3003"])

publicados = []


def publicar_falso(topico, carga, qos=0, retain=False):
    publicados.append((topico, carga, qos, retain))


fila, fresca = alertas.resolver(base, publicar_falso, "al1", ahora,
                                quien="el familiar ...3003", origen="el panel")
prueba("la primera resolución es fresca", fresca)
prueba("y avisa al satélite", len(publicados) == 1, len(publicados))
if publicados:
    topico, carga, qos, retain = publicados[0]
    prueba("en satelite/decir", topico == config.TOPICO_SATELITE_DECIR, topico)
    prueba("con el ts en epoch", carga.get("ts") == ahora, carga.get("ts"))
    prueba("sin retención", retain is False)
    prueba("con qos 1", qos == 1)
    prueba("y nombrando el tipo de alerta", "gas" in carga.get("texto", ""))
prueba("la alerta queda resuelta en la base",
       base.obtener_alerta("al1")["resuelto_en"] is not None)

# La segunda, que es el caso real: el familiar lo atiende en el panel y luego
# pulsa el botón de Telegram que tenía abierto.
publicados.clear()
_, fresca2 = alertas.resolver(base, publicar_falso, "al1", ahora + 5,
                              quien="el familiar ...3003", origen="el boton del bot")
prueba("la segunda resolución no es fresca", not fresca2)
prueba("y NO vuelve a avisar al satélite: es lo que el bot hacía mal",
       len(publicados) == 0, len(publicados))

# Sin cliente MQTT, y con el broker caído, no se rompe nada.
_, fresca3 = alertas.resolver(base, None, "al1", ahora + 6, origen="el panel")
prueba("sin cliente MQTT no hay excepción", not fresca3)


def publicar_roto(topico, carga, qos=0, retain=False):
    raise RuntimeError("el broker no está")


_, fresca4 = alertas.resolver(base, publicar_roto, "al1", ahora + 7, origen="el panel")
prueba("y si el broker falla, la resolución tampoco se deshace", not fresca4)

pendientes = base.conexion.execute(
    "SELECT COUNT(*) FROM envios WHERE alerta_id = ? AND enviado_en IS NULL",
    ("al1",)).fetchone()[0]
prueba("resolver cancela los envíos pendientes de los demás", pendientes == 0)

base.cerrar()


# =============================================================================
# 16. El token de Telegram NO puede salir al log
# =============================================================================
print("\n16. El token no se filtra")
print("-------------------------")

import http.server  # noqa: E402
import threading  # noqa: E402

import httpx  # noqa: E402

import sanear as sanear_modulo  # noqa: E402

TOKEN = config.TELEGRAM_BOT_TOKEN
prueba("la prueba tiene un token de mentira, no el de verdad",
       bool(TOKEN) and "NO-REAL" in TOKEN, "<no hay token de pruebas>")

# Un servidor HTTP de verdad, en un hilo, en 127.0.0.1 con un puerto que nos da el
# sistema. Se usa uno de verdad en vez de `MockTransport` por una razon concreta:
# `MockTransport` no loguea. El registro que filtra el token lo escribe httpx al
# RECIBIR una respuesta, y con el transporte simulado no llega ninguna. Es decir:
# con `MockTransport` esta prueba pasaria siempre, mirara lo que mirara.
peticiones = []


class _Contador(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        peticiones.append(self.path)
        cuerpo = b'{"ok": true, "result": {"id": 1, "is_bot": true, ' \
                 b'"first_name": "x", "username": "x"}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, formato, *args):
        pass  # el access log del servidor no, que tambien ensuciaria la prueba


servidor = http.server.HTTPServer(("127.0.0.1", 0), _Contador)
hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
hilo.start()
PUERTO = servidor.server_address[1]

captura = []
maneja_real = logging.getLogger().handlers[:]

# Esto es lo que hace el arranque del servicio (`orquestador.configurar_log`), y
# esta prueba lo repite a proposito: si el filtro no esta puesto, el token sale, y
# eso es justo lo que hay que comprobar. Sin esta linea la prueba mediria que no
# se ha puesto, no que el filtro funciona.
sanear_modulo.instalar_filtro_secretos()


class _Recoge(logging.Handler):
    def emit(self, registro_emitido):
        # Se guarda el TEXTO que se escribiria de verdad, no el registro sin
        # formatear: el token puede estar en `args` y no salir nunca en `msg`.
        captura.append(registro_emitido.getMessage())


recoge = _Recoge()
recoge.setLevel(logging.DEBUG)
logging.getLogger().addHandler(recoge)

# El peor caso a proposito: `httpx` hablado a DEBUG. Si la proteccion es solo
# subir el nivel del logger, aqui se ve. Ademas se hace una peticion al bot que se
# llama "telegram", para que la linea que sale sea la misma que filtro el 2026-10-05.
httpx_antes = logging.getLogger("httpx").level
logging.getLogger("httpx").setLevel(logging.DEBUG)
try:
    url = "http://127.0.0.1:%d/bot%s/getMe" % (PUERTO, TOKEN)
    with httpx.Client() as cliente:
        cliente.get(url)
finally:
    logging.getLogger("httpx").setLevel(httpx_antes)
    logging.getLogger().removeHandler(recoge)

servidor.shutdown()

texto = "\n".join(captura)
prueba("la petición llegó al servidor (si no, la prueba no probaría nada)",
       len(peticiones) == 1, peticiones)
prueba("y la URL que llegó llevaba el token, que es justo lo que hay que cazar",
       peticiones and TOKEN in peticiones[0], peticiones)
prueba("httpx sí escribió la línea con la URL",
       any("api.telegram.org/bot" in l or "/bot" in l for l in captura),
       captura[:3])
prueba("pero en el log el token NO aparece",
       TOKEN not in texto, [l for l in captura if TOKEN in l][:2])
prueba("y sale tapado con asteriscos", "bot***" in texto, captura[:3])
prueba("y no hubo ningún «Logging error» por el filtro",
       "Logging error" not in texto and "TypeError" not in texto,
       [l for l in captura if "TypeError" in l][:2])

# El caso que rompio la primera version del filtro: un `%d` con un entero. Si el
# filtro convierte los numeros a texto, el `%d` revienta, y el «Logging error» que
# sale lleva el registro entero dentro, con el token.
captura.clear()
recoge = _Recoge()
recoge.setLevel(logging.DEBUG)
logging.getLogger().addHandler(recoge)
prueba_filtro = logging.getLogger("prueba-filtro")
nivel_antes = prueba_filtro.level
prueba_filtro.setLevel(logging.DEBUG)  # sin esto, root en WARNING se come el .info
try:
    prueba_filtro.info("codigo %d y url %s", 200, url)
finally:
    logging.getLogger().removeHandler(recoge)
    prueba_filtro.setLevel(nivel_antes)
prueba("un entero con %d sobrevive al filtro (no se convierte a texto)",
       any("codigo 200" in l for l in captura), captura)
captura.clear()

# Y que el token siga saliendo en el sitio que debe: no tiene que estar callado
# entero, solo tapado.
captura.clear()
recoge = _Recoge()
logging.getLogger().addHandler(recoge)
try:
    prueba_filtro.setLevel(logging.DEBUG)
    prueba_filtro.info("token %s y otro %s", TOKEN, TOKEN)
finally:
    logging.getLogger().removeHandler(recoge)
    prueba_filtro.setLevel(nivel_antes)
prueba("un token escrito a mano también sale tapado",
       len(captura) == 1 and TOKEN not in captura[0] and "***" in captura[0],
       captura)
captura.clear()

prueba("y `sanear()` a pelo también funciona",
       sanear_modulo.sanear("dentro " + TOKEN) == "dentro ***")
prueba("sin token configurado, `sanear()` no toca nada",
       sanear_modulo.sanear("texto normal") == "texto normal")

# --- 17. `sanear()` tapa los secretos del `.env`, no solo dos ----------------
#
# El filtro tapaba el token de Telegram y la contraseña del panel, los dos
# escritos a mano en `sanear.py`. Con la contraseña del orquestador, la del nodo y
# las demás en el mismo `.env`, esa lista era un sitio más donde olvidarse: la
# contraseña del orquestador salía entera en el log de su propio servicio.
#
# Aquí se comprueba lo que pedía el arreglo: que la lista se lea de un solo sitio
# (`config.secretos()`) y que una variable NUEVA quede tapada sin tocar el código.
print("\n17. Los secretos del entorno, todos")
print("-----------------------------------")

# Se inventan valores de mentira para poder comprobar que se tapan sin depender de
# las contraseñas de verdad, que esta prueba no debe leer ni escribir.
inventados = {
    "MQTT_ORQUESTADOR_PASSWORD": "clave-de-prueba-orquestador-0001",
    "MQTT_PANEL_PASSWORD": "clave-de-prueba-panel-0002",
    "MQTT_NODO_PASSWORD": "clave-de-prueba-nodo-0003",
    "TELEGRAM_BOT_TOKEN": "987654:CLAVE-DE-PRUEBA-NO-REAL",
}
# Una clave que NO se escribe en ninguna parte del código, solo en el entorno: es
# el caso que falla si `sanear.py` vuelve a llevar su propia lista.
inventados["MQTT_FUTURO_PASSWORD"] = "clave-de-prueba-futuro-0004"
# Un valor corto: no es un secreto, y taparlo dejaria el log ilegible.
inventados["CLAVE_CORTA_TOKEN"] = "si"

antes = {nombre: os.environ.get(nombre) for nombre in inventados}
os.environ.update(inventados)
try:
    for nombre, valor in inventados.items():
        tapado = sanear_modulo.sanear("conexion %s con %s" % (nombre, valor))
        if len(valor) >= config.LARGO_MINIMO_DE_SECRETO:
            prueba("se tapa %s (no estaba en ninguna lista del código)" % nombre,
                   valor not in tapado and "***" in tapado, tapado)
        else:
            prueba("un valor de %d caracteres se deja como estaba (%s)"
                   % (len(valor), nombre), tapado.endswith(valor), tapado)

    prueba("y la contraseña del orquestador también, que es la que faltaba",
           sanear_modulo.sanear(inventados["MQTT_ORQUESTADOR_PASSWORD"])
           == "***")

    # El caso que hace necesaria la lista en `config`: un secreto que es PREFIJO de
    # otro. Tapal el corto primero deja el final del largo a la vista, y con una
    # contraseña de verdad eso es medio secreto en el log.
    os.environ["MQTT_CORTO_PASSWORD"] = "prefijo-1234"
    os.environ["MQTT_LARGO_PASSWORD"] = "prefijo-1234-sufijo-5678"
    with_prefijo = sanear_modulo.sanear(
        "clave larga prefijo-1234-sufijo-5678 y corta prefijo-1234")
    del os.environ["MQTT_CORTO_PASSWORD"], os.environ["MQTT_LARGO_PASSWORD"]
    prueba("un secreto que es prefijo de otro se tapa entero",
           "prefijo" not in with_prefijo, with_prefijo)

    prueba("`config.secretos()` ve los del entorno y no las variables normales",
           set(inventados.values()) - {"si"}
           <= set(config.secretos()))
    prueba("y no inventa secretos de los que no están",
           "no-existe-esta-variable" not in config.secretos())
finally:
    for nombre, valor in antes.items():
        if valor is None:
            os.environ.pop(nombre, None)
        else:
            os.environ[nombre] = valor

# --- 18. `/panel`: el código es de un solo uso y solo para los activos ----------
#
# El código es la puerta de entrada al panel, así que sus tres reglas se comprueban
# una a una: quién puede pedirlo (registrado y activo), qué pasa si no hay
# `PANEL_URL`, y que el código valga una vez y solo esa.
print("\n18. El comando /panel")
print("---------------------")

base_panel, ruta_panel = base_nueva("panel-cmd-")
base_panel.registrar_familiar("111", nombre="Marta")
bot_panel = bot_modulo.ClienteBot(base_panel, RemitenteFalso())


class MensajeConTeclado(MensajeFalso):
    """El de `MensajeFalso`, pero guardando también el teclado y el parse_mode."""

    async def reply_text(self, texto, **kw):
        self.respuestas.append(texto)
        self.teclado = kw.get("reply_markup")
        self.parse_mode = kw.get("parse_mode")


def codigo_de(texto):
    """El código que hay dentro de <code>...</code>, o '' si no lo hay."""
    import re  # noqa: PLC0415
    encontrado = re.search(r"<code>([^<]+)</code>", texto or "")
    return encontrado.group(1) if encontrado else ""


PANEL_URL_DE_PRUEBA = "http://villaca.tail722206.ts.net:8080"
url_antes = config.PANEL_URL
config.PANEL_URL = PANEL_URL_DE_PRUEBA
try:
    # Un chat que no está registrado no recibe ningún enlace, y el texto que recibe
    # es el de `/registro` sin código: el mismo, para no confirmar que el panel
    # existe a cualquiera que encuentre el bot.
    u = UpdateFalso("999")
    u.effective_message = MensajeConTeclado()
    asyncio.run(bot_panel.cmd_panel(u, ContextoFalso([])))
    prueba("un chat NO registrado no recibe enlace",
           getattr(u.effective_message, "teclado", None) is None, u.contestado)
    prueba("y el texto es EXACTAMENTE el de /registro sin código (nada nuevo)",
           u.contestado
           and u.contestado[0] == bot_modulo.texto_pedir_registro(base_panel),
           u.contestado)
    prueba("y no menciona el panel, que es lo que no hay que confirmar",
           "panel" not in " ".join(u.contestado).lower(), u.contestado)
    import sqlite3  # noqa: E402

    def _enlaces_guardados():
        con = sqlite3.connect(ruta_panel)
        try:
            return con.execute("SELECT codigo_hash FROM panel_enlaces").fetchall()
        finally:
            con.close()

    prueba("y no se creó ningún enlace para él", _enlaces_guardados() == [],
           _enlaces_guardados())

    # Sin PANEL_URL no se manda un enlace roto. Es el caso de hoy si se olvida la
    # variable, y un botón que lleva a una página en blanco hace que el familiar
    # pulse lo que sea en la próxima alerta de gas.
    config.PANEL_URL = ""
    u = UpdateFalso("111")
    u.effective_message = MensajeConTeclado()
    asyncio.run(bot_panel.cmd_panel(u, ContextoFalso([])))
    prueba("sin PANEL_URL el bot lo dice y no manda botón",
           getattr(u.effective_message, "teclado", None) is None
           and u.contestado and u.contestado[0] == textos.PANEL_NO_DISPONIBLE,
           u.contestado)
    config.PANEL_URL = PANEL_URL_DE_PRUEBA

    # Un familiar DESACTIVADO no puede pedir código, aunque siga registrado en la
    # base. El texto es el mismo que el de un no registrado: no se revela que está
    # desactivado, que es una información sobre una persona.
    base_panel.marcar_familiar_inactivo("111")
    u_baja = UpdateFalso("111")
    u_baja.effective_message = MensajeConTeclado()
    asyncio.run(bot_panel.cmd_panel(u_baja, ContextoFalso([])))
    prueba("a un familiar desactivado no se le manda ningún código",
           getattr(u_baja.effective_message, "teclado", None) is None
           and u_baja.contestado
           and u_baja.contestado[0] == bot_modulo.texto_pedir_registro(base_panel),
           u_baja.contestado)
    con = sqlite3.connect(ruta_panel)
    sin_codigo_baja = con.execute(
        "SELECT COUNT(*) FROM codigos_acceso WHERE chat_id = '111'"
    ).fetchone()[0]
    con.close()
    prueba("y tampoco se creó ningún código para él", sin_codigo_baja == 0,
           sin_codigo_baja)
    base_panel.marcar_familiar_activo("111")

    # El caso normal: el código en un mensaje de HTML, con la URL a la que
    # teclearlo y dos botones: copiar el código y abrir el panel.
    from telegram.constants import ParseMode  # noqa: E402

    u = UpdateFalso("111")
    u.effective_message = MensajeConTeclado()
    asyncio.run(bot_panel.cmd_panel(u, ContextoFalso([])))
    teclado = getattr(u.effective_message, "teclado", None)
    prueba("a un familiar registrado sí le llega un teclado",
           teclado is not None and len(teclado.inline_keyboard) == 2, teclado)
    prueba("y el mensaje va con parse_mode HTML, para pintar el código",
           getattr(u.effective_message, "parse_mode", None) == ParseMode.HTML,
           getattr(u.effective_message, "parse_mode", None))
    prueba("el mensaje lleva la URL completa de PANEL_URL, con dominio",
           PANEL_URL_DE_PRUEBA in " ".join(u.contestado), u.contestado)
    codigo = codigo_de(u.contestado[0] if u.contestado else "")
    prueba("el código son 6 caracteres del alfabeto sin 0/O ni 1/l/I",
           len(codigo) == 6
           and all(c in panel_acceso_modulo.ALFABETO_CODIGO for c in codigo), codigo)

    # El teclado: fila 1 = copiar el código (copy_text), fila 2 = abrir el panel.
    botones = teclado.to_dict()["inline_keyboard"] if teclado else []
    copiar = botones[0][0] if len(botones) >= 1 and botones[0] else {}
    abrir = botones[1][0] if len(botones) >= 2 and botones[1] else {}
    prueba("el primer botón copia el código con un toque",
           copiar.get("text") == textos.PANEL_BOTON_COPIAR
           and copiar.get("copy_text", {}).get("text") == codigo, copiar)
    prueba("el segundo botón abre el panel en la URL de PANEL_URL",
           abrir.get("text") == textos.PANEL_BOTON_ABRIR
           and abrir.get("url") == PANEL_URL_DE_PRUEBA, abrir)
    prueba("y el código NO va dentro de la URL del botón",
           codigo and codigo not in (abrir.get("url") or ""), abrir.get("url"))

    # Un mensaje con parse_mode HTML se le como HTML: si se escapa mal cualquier
    # cosa, Telegram lo rechaza y el familiar no ve ni el código ni el error. Se
    # parsea con el parser del navegador y se mira lo que sale: el unico marcado
    # permitido es <code>, y el texto tiene que seguir llevando el codigo y la URL.
    import html.parser  # noqa: E402, PLC0415

    class _ParserHTML(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.etiquetas = []
            self.texto = ""

        def handle_starttag(self, etiqueta, _atributos):
            self.etiquetas.append(etiqueta)

        def handle_data(self, dato):
            self.texto += dato

    _parser = _ParserHTML()
    _parser.feed(u.contestado[0] if u.contestado else "")
    prueba("el mensaje se puede parsear como HTML y solo lleva <code>",
           sorted(set(_parser.etiquetas)) == ["code"], _parser.etiquetas)
    prueba("y dentro sigue estando el codigo y la URL, sin que se pierda nada",
           codigo in _parser.texto and PANEL_URL_DE_PRUEBA in _parser.texto,
           _parser.texto[:120])
    prueba("el URL que se enseña es la de config, escapada como texto",
           html.escape(PANEL_URL_DE_PRUEBA) in u.contestado[0], u.contestado[0])

    # Ningún carácter con significado en HTML queda suelto fuera del <code>: si
    # alguien metiera un < o un & en la plantilla, Telegram devolveria un 400 y el
    # codigo no llegaria.
    plantilla_sin_tag = (textos.PANEL_CODIGO.replace("<code>", "")
                         .replace("</code>", ""))
    prueba("fuera del <code> la plantilla no tiene marcado HTML suelto",
           "<" not in plantilla_sin_tag and ">" not in plantilla_sin_tag,
           plantilla_sin_tag[:80])
    prueba("el URL que se enseña es la de config, escapada como texto",
           html.escape(PANEL_URL_DE_PRUEBA) in u.contestado[0], u.contestado[0])

    con = sqlite3.connect(ruta_panel)
    hashes = [f[0] for f in con.execute("SELECT codigo_hash FROM codigos_acceso")]
    expires = [f[0] for f in con.execute("SELECT expira_en FROM codigos_acceso")]
    con.close()
    prueba("en la base solo está el hash, nunca el código",
           panel_acceso_modulo.hash_de(codigo) in hashes
           and codigo not in hashes, hashes)
    prueba("y el enlace caduca a los 10 minutos",
           expires and 595 <= expires[0] - int(time.time()) <= 600, expires)

    # Un solo uso: el primero que canjea entra y el segundo no.
    ok_c, fila_c = panel_acceso_modulo.tomar_codigo_acceso(base_panel, codigo)
    prueba("el primer canje entra", ok_c is True and fila_c.get("chat_id") == "111",
           fila_c)
    prueba("el segundo canje NO entra (de un solo uso)",
           panel_acceso_modulo.tomar_codigo_acceso(base_panel, codigo)[0] is False)

    # Un código inventado no cuenta como intento ni abre nada: en la tabla solo hay
    # hashes, así que adivinar es recorrer 8x10^23 combinaciones.
    prueba("un código que no existe no abre nada",
           panel_acceso_modulo.tomar_codigo_acceso(base_panel, "ZZZZZZ")[0] is False)

    # Dos `/panel` seguidos dan dos enlaces distintos, y caducan por separado.
    u2 = UpdateFalso("111")
    u2.effective_message = MensajeConTeclado()
    asyncio.run(bot_panel.cmd_panel(u2, ContextoFalso([])))
    codigo2 = codigo_de(u2.contestado[0] if u2.contestado else "")
    prueba("dos /panel seguidos dan dos códigos distintos", codigo != codigo2,
           (codigo, codigo2))

    # El token de sesión: 32 bytes, y solo su hash en la base.
    token, caduca_en = panel_acceso_modulo.crear_token(base_panel, "111")
    prueba("el token de sesión existe y no es el código",
           bool(token) and token != codigo, token[:6] + "...")
    prueba("vale 30 días", 29 * 86400 <= caduca_en - int(time.time()) <= 30 * 86400,
           caduca_en)
    sesion = base_panel.sesion_panel(panel_acceso_modulo.hash_de(token))
    prueba("la sesión nueva vale y trae su chat", sesion is not None
           and sesion["chat_id"] == "111", sesion)
    con = sqlite3.connect(ruta_panel)
    hashes = [f[0] for f in con.execute("SELECT token_hash FROM panel_tokens")]
    con.close()
    prueba("y en la base solo está su hash", token not in hashes, hashes)

    # `db.nombre_de` es lo que saluda en la cabecera del panel. Se prueba aquí y no en
    # la sección del panel porque es un método de `db`: si el nombre sale vacío, la
    # cabecera dice «Hola, » y no se ve de dónde viene el fallo.
    prueba("el nombre del familiar sale de la tabla, no del token",
           db_modulo.BaseDatos is not None
           and base_panel.nombre_de("111") == "Marta",
           base_panel.nombre_de("111"))
    prueba("y un chat_id que no existe da cadena vacía y no revienta",
           base_panel.nombre_de("999") == "", base_panel.nombre_de("999"))
finally:
    config.PANEL_URL = url_antes
    base_panel.cerrar()

# --- 19. `panel_acceso.canjear()` y `panel_acceso.revocar()` -------------------
#
# Son los dos ayudantes que usa la API, y viven aquí y no en `panel/api.py` porque los
# necesita el proceso de la web y no solo el. Lo que se comprueba es lo que la API da
# por hecho: que `canjear` devuelve la fila con el `chat_id` y que el canje no se
# puede repetir, y que `revocar` cierra UNA sesión y deja vivas las demás del mismo
# familiar.
print("\n19. El canje y la revocación del panel")
print("------------------------------------")

base_panel2, ruta_panel2 = base_nueva("panel-canje-")
try:
    base_panel2.registrar_familiar("111", nombre="Marta")
    base_panel2.registrar_familiar("222", nombre="Luis")

    codigo_ok = panel_acceso_modulo.crear_enlace(base_panel2, "111")
    exito, fila = panel_acceso_modulo.canjear(base_panel2, codigo_ok)
    prueba("canjear un enlace bueno devuelve la fila con su chat_id",
           exito and fila.get("chat_id") == "111", fila)
    prueba("y el código en claro no está en la fila que devuelve",
           codigo_ok not in str(fila), sorted(fila))

    exito2, _ = panel_acceso_modulo.canjear(base_panel2, codigo_ok)
    prueba("canjear el MISMO enlace otra vez no entra", not exito2)

    exito3, fila3 = panel_acceso_modulo.canjear(base_panel2, "codigo-que-no-existe")
    prueba("canjear un código que no existe no entra y no revienta",
           not exito3 and fila3 == {}, fila3)

    # Un código caducado tampoco entra. Se adelanta el reloj de la base, que es lo
    # mismo que esperar 10 minutos sin esperar 10 minutos.
    caducado = panel_acceso_modulo.crear_enlace(base_panel2, "222",
                                                ahora=int(time.time()) - 3600)
    exito4, _ = panel_acceso_modulo.canjear(base_panel2, caducado)
    prueba("canjear un enlace caducado no entra", not exito4)

    # Dos sesiones del mismo familiar, y revocar una deja viva la otra.
    token_a, _ = panel_acceso_modulo.crear_token(base_panel2, "111")
    token_b, _ = panel_acceso_modulo.crear_token(base_panel2, "111")
    prueba("las dos sesiones valen antes de revocar",
           base_panel2.sesion_panel(panel_acceso_modulo.hash_de(token_a)) is not None
           and base_panel2.sesion_panel(panel_acceso_modulo.hash_de(token_b))
           is not None)

    revocada = panel_acceso_modulo.revocar(
        base_panel2, panel_acceso_modulo.hash_de(token_a))
    prueba("revocar devuelve True si la sesión estaba viva", revocada)
    prueba("revocada queda cerrada",
           base_panel2.sesion_panel(panel_acceso_modulo.hash_de(token_a)) is None)
    prueba("pero la OTRA sesión del mismo familiar sigue viva",
           base_panel2.sesion_panel(panel_acceso_modulo.hash_de(token_b)) is not None)
    prueba("y revocar dos veces la misma devuelve False",
           not panel_acceso_modulo.revocar(base_panel2,
                                           panel_acceso_modulo.hash_de(token_a)))

    # La baja de un familiar cierra el panel entero, y se comprueba por los DOS
    # caminos por los que puede pasar.
    #
    # El primero es `dar_de_baja`, que borra sus filas de `panel_tokens`. El segundo es
    # la condición de `sesion_panel`, que además exige que el `chat_id` siga en
    # `familiares`: por eso aquí se mete un token a mano DESPUÉS de la baja, para
    # comprobar que la puerta se sigue cerrando aunque el borrado se quedara sin
    # hacer. Si esa comprobación desapareciera de `sesion_panel`, la baja seguiría
    # pareciendo correcta en el uso normal y dejaría sesiones abiertas justo cuando
    # el operador cree que ha cerrado el acceso.
    base_panel2.dar_de_baja("111")
    prueba("dar de baja borra sus sesiones",
           base_panel2.sesion_panel(panel_acceso_modulo.hash_de(token_b)) is None)
    colado, _ = panel_acceso_modulo.crear_token(base_panel2, "111")
    prueba("y aunque se cuele un token después, la sesión no vale",
           base_panel2.sesion_panel(panel_acceso_modulo.hash_de(colado)) is None)
    prueba("las sesiones del otro familiar no se tocan",
           base_panel2.nombre_de("222") == "Luis")
finally:
    base_panel2.cerrar()

# =============================================================================
# 20. El aviso de Telegram se reescribe cuando se atiende desde el panel
# =============================================================================
print("\n20. Reescribir el aviso atendido desde el panel")
print("---------------------------------------------")

# El panel resuelve por `alertas.resolver`, pero no puede editar el mensaje de
# Telegram: es otro proceso y no lleva el token. Lo que hace que el familiar vea
# «ATENDIDO» igualmente es que el trabajador de la cola reescriba los avisos de
# alertas resueltas que todavia no se hayan reescrito. Aquí se prueba ese camino
# de punta a punta, con el MISMO `resolver` que llama la API del panel.

base, _ = base_nueva("reescribir-")
columnas_envios = {f["name"] for f in base.conexion.execute(
    "PRAGMA table_info(envios)").fetchall()}
prueba("`envios` tiene `mensaje_id` y `editado_en`",
       {"mensaje_id", "editado_en"} <= columnas_envios, sorted(columnas_envios))
prueba("la base abre ya en la versión actual del esquema",
       base.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA)

# Una base que ya estaba en la 2 (las columnas NO existian) las gana al abrirla.
# Es el caso que le pasa a la instalación real: `esquema.sql` no puede tocar una
# tabla que ya existe, asi que las columnas nuevas solo salen del `ALTER TABLE` de
# la migración.
ruta_v2 = os.path.join(tempfile.mkdtemp(prefix="v2-"), "o.sqlite3")
con = sqlite3.connect(ruta_v2)
con.executescript(db_modulo.leer_esquema())
con.execute("ALTER TABLE envios DROP COLUMN mensaje_id")
con.execute("ALTER TABLE envios DROP COLUMN editado_en")
con.execute("PRAGMA user_version = 2")
con.commit()
con.close()

base_v2 = db_modulo.BaseDatos(ruta_v2)
columnas_v2 = {f["name"] for f in base_v2.conexion.execute(
    "PRAGMA table_info(envios)").fetchall()}
prueba("una base en la versión 2 gana las dos columnas al abrirla",
       {"mensaje_id", "editado_en"} <= columnas_v2, sorted(columnas_v2))
prueba("y sube a la versión actual del esquema",
       base_v2.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA)
base_v2.cerrar()

base.registrar_familiar("551999991", nombre="Marta")


class RemitenteConId:
    """Un `Remitente` que devuelve `Message` de verdad (con `message_id`).

    Los remitentes de prueba anteriores devolvían `len(texto)`, y eso está bien
    mientras nada dependa del id. Aquí sí depende: sin `message_id` no hay manera
    de saber qué mensaje reescribir.
    """

    def __init__(self):
        self.token = "x"
        self.disponible = True
        self.enviados = []
        self.ediciones = []       # (chat_id, message_id, texto)
        self.ediciones_futuro = []  # (chat_id, texto), las del boton del bot
        self._siguiente = 900000

    def enviar(self, texto, teclado=None, chat_id=None):
        self._siguiente += 1
        self.enviados.append((str(chat_id), texto, teclado))
        mensaje = type("Message", (), {})()
        mensaje.message_id = self._siguiente
        mensaje.chat_id = str(chat_id)
        return mensaje

    def editar_mensaje(self, chat_id, message_id, texto, teclado=None):
        self.ediciones.append((str(chat_id), int(message_id), texto, teclado))
        return True

    def editar_futuro(self, mensaje, texto, teclado=None, chat_id=None):
        self.ediciones_futuro.append((str(chat_id), texto, teclado))
        futuro = asyncio.get_event_loop().create_future()
        futuro.set_result(None)
        return futuro


base.insertar_alerta("000000-b0020-0001", "gas",
                     '{"id":"000000-b0020-0001","gas_adc":2100}')

remitente = RemitenteConId()
trabajador = cola_modulo.TrabajadorTelegram(base, remitente)
trabajador._intentar_uno()

envio = base.obtener_envio("000000-b0020-0001", "551999991")
prueba("el envio guarda el message_id con el que salio",
       envio is not None and envio["mensaje_id"] is not None,
       dict(envio) if envio else None)

# Con la alerta aun sin resolver no hay nada que reescribir, aunque el aviso ya
# este en el chat con sus botones.
trabajador._editar_resueltas()
prueba("mientras la alerta no este resuelta, el aviso no se toca",
       remitente.ediciones == [], remitente.ediciones)

# La resolucion del PANEL, que es exactamente lo que hace `POST .../atender`.
atendida = int(time.time())
alertas.resolver(base, None, "000000-b0020-0001", ahora=atendida,
                 quien="Marta", origen="el panel")

trabajador._editar_resueltas()
prueba("resuelta desde el panel, el aviso se reescribe una sola vez",
       len(remitente.ediciones) == 1, remitente.ediciones)
if remitente.ediciones:
    chat, message_id, texto, teclado = remitente.ediciones[0]
    prueba("y va al chat y al message_id del aviso que se mando",
           (chat, message_id) == ("551999991", envio["mensaje_id"]),
           (chat, message_id))
    prueba("y dice ATENDIDA", "ATENDIDA." in texto, texto)
    prueba("y se le quitan los botones", teclado is None, teclado)
    prueba("y pone la hora en que se atendio, sin segundos (HH:MM)",
           ("a las %s." % time.strftime("%H:%M", time.localtime(atendida))) in texto
           and time.strftime("%H:%M:%S", time.localtime(atendida)) not in texto,
           texto)

prueba("ya no queda nada en la cola de reescritura",
       base.envios_a_editar() == [], base.envios_a_editar())
trabajador._editar_resueltas()
prueba("y la vuelta siguiente no vuelve a editar el mismo mensaje",
       len(remitente.ediciones) == 1, remitente.ediciones)

# Si ademas alguien pulsa el boton del bot DESPUES, tiene que contestarle a el y
# no rehacer lo que ya hizo la cola.
base.insertar_alerta("000000-b0020-0002", "gas",
                     '{"id":"000000-b0020-0002","gas_adc":2100}')
trabajador._intentar_uno()
envio2 = base.obtener_envio("000000-b0020-0002", "551999991")
consulta = ConsultaFalsa("mensaje", "000000-b0020-0002")
u = UpdateConsulta("551999991", consulta)
asyncio.run(bot_modulo.ClienteBot(base, remitente)
            .cmd_atendi(update=u, contexto=ContextoFalso([])))
prueba("el boton del bot tambien deja la fila marcada como reescrita",
       base.envios_a_editar() == [], base.envios_a_editar())
prueba("y el que la resolvio el boton lo reescribio EL, no la cola",
       remitente.ediciones_futuro and remitente.ediciones_futuro[0][0]
       == "551999991", remitente.ediciones_futuro)
prueba("el message_id del aviso del boton se guardo en la base",
       envio2 is not None and envio2["mensaje_id"] is not None)

# Y el texto de los dos caminos es el mismo texto, porque sale de la misma funcion.
# La hora que pone el boton es `resuelto_en`, la del aviso, y no la de cuando
# pulsa: si alguien atiende desde el panel y otro despues pulsa el boton en
# Telegram, el mensaje tiene que decir la hora real de atencion.
fila2 = base.obtener_alerta("000000-b0020-0002")
mismo = alertas.texto_atendido(fila2["tipo"], fila2["resuelto_en"],
                               "000000-b0020-0002")
prueba("el boton del bot escribe EXACTAMENTE lo que reescribiria la cola",
       remitente.ediciones_futuro and remitente.ediciones_futuro[0][1] == mismo,
       (remitente.ediciones_futuro, mismo))

base.cerrar()


# =============================================================================
# 20. Voz (etapa A): el texto reconocido, su eco y su reparto
# =============================================================================
#
# La etapa A anade un topico nuevo (`casa/sala/voz/texto`) y un camino nuevo en el
# orquestador: el texto reconocido no es una alerta y no puede pasar por
# `alertas.procesar`, que decide el tipo por el topico y lo rechazaria. Estas
# pruebas fijan las tres cosas que pueden romperse sin que se note: que el topico
# sea el del contrato, que el eco diga lo que tiene que decir (y nada cuando el eco
# esta apagado), y que `enrutar` mande cada topico a su procesador de verdad.

print("\n20. Voz (etapa A)")
print("------------------")

prueba("el topico del texto de voz es el del contrato",
       config.TOPICO_VOZ_TEXTO == "casa/sala/voz/texto", config.TOPICO_VOZ_TEXTO)

# --- Fuente unica de la gramatica ---
import tempfile as _tmp
_ruta_faltante = os.path.join(_tmp.gettempdir(), "no-existe-gramatica.json")
os.environ["GRAMATICA_JSON"] = _ruta_faltante
prueba("sin archivo de gramatica, voz no carga intenciones",
       voz._cargar() == {}, voz._cargar())
_ruta_vacia = os.path.join(_tmp.gettempdir(), "gramatica-vacia.json")
with open(_ruta_vacia, "w") as _f:
    _f.write("{}")
os.environ["GRAMATICA_JSON"] = _ruta_vacia
prueba("gramatica vacia, voz no carga intenciones",
       voz._cargar() == {}, voz._cargar())
os.environ.pop("GRAMATICA_JSON", None)

# La regla de antiguedad de voz/texto compara el `ts` del mensaje con la hora del
# sistema, asi que los `ts` de estas pruebas se calculan al correr y NO se dejan
# literales: un ts fijo vence a los 10 s y convierte la suite en una bomba de
# reloj (el eco deja de publicarse y las comprobaciones fallan sin tocar nada).
AHORA = int(time.time())


def publicar_que_recoge():
    """Un `publicar` falso que apunta lo que se publica, para las pruebas de voz."""
    apuntes = []

    def publicar(topico, carga, qos=1, retain=False):
        apuntes.append((topico, carga, qos, retain))

    return apuntes, publicar


# --- Intenciones de voz (Paso 3) ---
apuntes, publicar = publicar_que_recoge()
publicado = voz.procesar('{"frase_id":"f1","texto":"prende la luz","modo":"comandos","ts":%d}' % AHORA, publicar)
prueba("una frase reconocida publica accion y respuesta", publicado is True and len(apuntes) == 2,
       apuntes)
if len(apuntes) >= 2:
    topico, carga, qos, retain = apuntes[0]
    prueba("la accion va a casa/sala/luz/set",
           topico == config.TOPICO_LUZ_SET, topico)
    prueba("luz/set pide on con origen voz",
           carga.get("estado") == "on" and carga.get("origen") == "voz", carga)
    topico2, carga2, qos2, retain2 = apuntes[1]
    prueba("la respuesta va a satelite/decir",
           topico2 == config.TOPICO_SATELITE_DECIR, topico2)
    prueba("la respuesta es la de encender",
           carga2.get("texto") == textos.RESPUESTA_ENCENDER_LUZ, carga2)

# --- `[unk]` y vacio: no se entendio ---
for texto_raro in ("[unk]", "", "   ", "la luz [unk] cosa"):
    apuntes, publicar = publicar_que_recoge()
    voz.procesar('{"frase_id":"f","texto":"%s","modo":"comandos","ts":%d}'
                 % (texto_raro, AHORA), publicar)
    prueba("texto %r contesta la frase de no entendido" % texto_raro,
           len(apuntes) == 1
           and apuntes[0][1].get("texto") == textos.NO_ENTENDIDO, apuntes)

# --- Frase no reconocida: NO_ENTENDI ---
apuntes, publicar = publicar_que_recoge()
voz.procesar('{"frase_id":"f","texto":"prende la tele","modo":"comandos","ts":%d}' % AHORA, publicar)
prueba("frase ajena contesta NO_ENTENDIDO",
       len(apuntes) == 1 and apuntes[0][1].get("texto") == textos.NO_ENTENDIDO, apuntes)

# --- Cuerpos que no son una frase ---
apuntes, publicar = publicar_que_recoge()
prueba("un cuerpo que no es JSON no publica nada",
       voz.procesar("esto no es json", publicar) is False and apuntes == [], apuntes)
apuntes, publicar = publicar_que_recoge()
prueba("un cuerpo que no es objeto no publica nada",
       voz.procesar('["una","lista"]', publicar) is False and apuntes == [], apuntes)

# --- Antigüedad de voz/texto (regla > 10 s o sin ts) ---
apuntes, publicar = publicar_que_recoge()
prueba("voz sin ts no publica nada",
       voz.procesar('{"frase_id": "f", "texto": "prende la luz"}', publicar) is False and apuntes == [],
       apuntes)
apuntes, publicar = publicar_que_recoge()
prueba("voz con ts > 10 s se descarta",
       voz.procesar('{"frase_id": "f", "texto": "prende la luz", "ts": %d}' % (AHORA - 60), publicar) is False and apuntes == [],
       apuntes)
apuntes, publicar = publicar_que_recoge()
publicado = voz.procesar('{"frase_id": "f", "texto": "prende la luz", "ts": %d}' % AHORA, publicar)
prueba("voz con ts reciente publica accion", publicado is True and len(apuntes) >= 1, apuntes)

apuntes, publicar = publicar_que_recoge()
prueba("voz con ts en milisegundos se descarta (unidades incorrectas)",
       voz.procesar('{"frase_id": "f", "texto": "prende", "ts": %d}' % (AHORA * 1000), publicar) is False and apuntes == [],
       apuntes)

# --- Luz ya en ese estado ---
voz.actualizar_estado_luz('{"estado":"on","origen":"panel","ts":%d}' % AHORA)
apuntes, publicar = publicar_que_recoge()
voz.procesar('{"frase_id":"f","texto":"enciende la luz","ts":%d}' % AHORA, publicar)
prueba("luz ya encendida no publica luz/set",
       all(t != config.TOPICO_LUZ_SET for t, *_ in apuntes), apuntes)
prueba("luz ya encendida responde ya encendida",
       len(apuntes) == 1 and apuntes[0][1].get("texto") == textos.LUZ_YA_ENCENDIDA, apuntes)
voz.actualizar_estado_luz('{"estado":"off","origen":"panel","ts":%d}' % AHORA)

# --- Nodo desconectado ---
voz.actualizar_nodo('{"conectado":false}')
apuntes, publicar = publicar_que_recoge()
voz.procesar('{"frase_id":"f","texto":"enciende la luz","ts":%d}' % AHORA, publicar)
prueba("nodo desconectado no publica luz/set",
       all(t != config.TOPICO_LUZ_SET for t, *_ in apuntes), apuntes)
prueba("nodo desconectado responde no pudo",
       len(apuntes) == 1 and apuntes[0][1].get("texto") == textos.NO_PUDO_ENCENDER_LUZ, apuntes)
voz.actualizar_nodo('{"conectado":true}')
voz.actualizar_estado_luz('{"estado":"off","origen":"panel","ts":%d}' % AHORA)

# --- Confirmacion de luz en 3 s ---
voz._estado_luz["valor"] = "off"
apuntes, publicar = publicar_que_recoge()
_sleep_original = voz.time.sleep
_time_original = voz.time.time
contador_tiempo = {"t": 0.0}
def _tiempo_falso():
    contador_tiempo["t"] += 0.5
    return contador_tiempo["t"]
voz.time.sleep = lambda _s: None
voz.time.time = _tiempo_falso
voz._confirmar_luz("on", publicar, "f3s")
voz.time.sleep = _sleep_original
voz.time.time = _time_original
prueba("sin confirmacion en 3 s publica no pudo encender",
       any(c.get("texto") == textos.NO_PUDO_ENCENDER_LUZ for _, c, *_ in apuntes),
       apuntes)
voz._estado_luz["valor"] = None

apuntes, publicar = publicar_que_recoge()
voz.procesar('{"frase_id":"f","texto":"necesito ayuda","ts":%d}' % AHORA, publicar)
prueba("ayuda publica alerta/ayuda",
       any(t == config.TOPICO_ALERTA_AYUDA for t, *_ in apuntes), apuntes)
prueba("ayuda responde texto de ayuda",
       any(c.get("texto") == textos.RESPUESTA_AYUDA
           for _, c, *_ in apuntes), apuntes)

# --- Cancelar ---
apuntes, publicar = publicar_que_recoge()
voz.procesar('{"frase_id":"f","texto":"cancela","ts":%d}' % AHORA, publicar)
prueba("cancelar responde cancelado",
       len(apuntes) == 1 and apuntes[0][1].get("texto") == textos.RESPUESTA_CANCELAR, apuntes)

# --- Ayuda antirrebote con base ---
base_ar, _ = base_nueva("voz-ar-")
# Sembrar una ayuda reciente.
base_ar.insertar_alerta("ayuda-reciente", "ayuda", '{"id":"ayuda-reciente","origen":"voz"}')
# hay_ayuda_reciente mira envios enviados; forzar marcando enviado? Insertar envio manual.
base_ar.conexion.execute("INSERT INTO envios (alerta_id, chat_id, creado_en, enviado_en) VALUES (?,?,?,?)",
                         ("ayuda-reciente", "1", int(time.time()), int(time.time())))
base_ar.conexion.commit()
apuntes, publicar = publicar_que_recoge()
voz.procesar('{"frase_id":"f","texto":"ayuda","ts":%d}' % AHORA, publicar, base=base_ar)
prueba("ayuda en antirrebote responde reciente",
       len(apuntes) == 1 and apuntes[0][1].get("texto") == textos.RESPUESTA_AYUDA_RECIENTE, apuntes)
prueba("ayuda en antirrebote no publica alerta/ayuda",
       all(t != config.TOPICO_ALERTA_AYUDA for t, *_ in apuntes), apuntes)
base_ar.cerrar()

# --- El eco no espera el PUBACK desde el callback de paho ---
# `publicar` con `esperar=False` NO debe llamar a `wait_for_publish`: cuando se
# invoca desde `on_message` (el hilo de red de paho) esperar el PUBACK se
# autobloquea hasta el timeout, porque ese mismo hilo es el que lo procesaria.
# Ese autobloqueo era el retraso de 5 s del eco. `esperar=True` (lo normal desde
# otros hilos) si tiene que esperar.
class PublicadorFalso:
    """Un doble del cliente paho que cuenta las esperas de PUBACK."""

    def __init__(self):
        self.publicados = []
        self.esperas = 0

    def publish(self, topico, cuerpo, qos=1, retain=False):
        self.publicados.append((topico, cuerpo, qos, retain))
        return self

    def wait_for_publish(self, timeout=None):
        self.esperas += 1


def cliente_con_publicador_falso():
    doble = mqtt_cliente.ClienteMQTT.__new__(mqtt_cliente.ClienteMQTT)
    doble.cliente = PublicadorFalso()
    doble._hilo_red = None
    return doble


sin_espera = cliente_con_publicador_falso()
sin_espera.publicar(config.TOPICO_SATELITE_DECIR, {"texto": "Escuché: hola"},
                    qos=1, esperar=False)
prueba("publicar con esperar=False no espera el PUBACK",
       sin_espera.cliente.esperas == 0 and len(sin_espera.cliente.publicados) == 1,
       sin_espera.cliente.esperas)

con_espera = cliente_con_publicador_falso()
con_espera.publicar(config.TOPICO_SATELITE_DECIR, {"texto": "Escuché: hola"}, qos=1)
prueba("publicar por defecto si espera el PUBACK",
       con_espera.cliente.esperas == 1, con_espera.cliente.esperas)

# Y la red de seguridad: si lo llaman desde el MISMO hilo de red de paho —el que
# guarda `_al_conectar`— no espera aunque se pida `esperar=True`. Así ningún
# camino que publique desde un callback (hoy la voz, mañana cualquier tópico)
# puede autobloquearse por olvidar pasar `esperar=False`.
en_callback = cliente_con_publicador_falso()
en_callback._hilo_red = threading.current_thread()
en_callback.publicar(config.TOPICO_SATELITE_DECIR, {"texto": "Escuché: hola"}, qos=1)
prueba("publicar desde el hilo de red de paho no espera aunque esperar=True",
       en_callback.cliente.esperas == 0
       and len(en_callback.cliente.publicados) == 1,
       en_callback.cliente.esperas)
# --- El reparto por topico ---
# `enrutar` tiene que mandar el texto de voz a `voz.procesar` y todo lo demas a
# `alertas.procesar`. Es lo unico que impide que el topico nuevo caiga en la rama
# de "topico no atendido" de alertas y se pierda sin ruido.
base, _ruta = base_nueva("voz-despacho-")
apuntes, publicar = publicar_que_recoge()
orquestador_modulo.enrutar(config.TOPICO_VOZ_TEXTO,
                           '{"frase_id":"f","texto":"hola","modo":"comandos","ts":%d}'
                           % AHORA, base, publicar)
prueba("enrutar manda el texto de voz al eco", len(apuntes) == 1, apuntes)
prueba("y NO lo mete en la base como si fuera una alerta",
       base.obtener_alerta("f") is None, base.obtener_alerta("f"))

apuntes, publicar = publicar_que_recoge()
orquestador_modulo.enrutar(
    config.TOPICO_ALERTA_GAS, '{"id":"000000-b0030-0001","gas_adc":2100}',
    base, publicar)
prueba("enrutar manda la alerta de gas a alertas.procesar",
       base.obtener_alerta("000000-b0030-0001") is not None, None)
prueba("y una alerta NO dispara el eco, sino la alerta hablada (Fase 2C)",
       len(apuntes) == 1 and apuntes[0][1].get("tipo") == "alerta"
       and "Usted dijo" not in apuntes[0][1].get("texto", ""), apuntes)
base.cerrar()

# --- El ACL: el usuario voz solo escribe su topico; el orquestador lo lee ---
ruta_acl = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mosquitto", "config", "acl.conf")


def bloques_de_usuario(texto):
    """Devuelve `{usuario: [lineas topic ...]}` del acl.conf."""
    bloques = {}
    usuario = None
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#"):
            continue
        if limpia.startswith("user "):
            usuario = limpia.split()[1]
            bloques[usuario] = []
        elif usuario is not None and limpia.startswith("topic "):
            bloques[usuario].append(limpia)
    return bloques


if os.path.exists(ruta_acl):
    with open(ruta_acl, encoding="utf-8") as f:
        acl_texto = f.read()
    bloques = bloques_de_usuario(acl_texto)
    prueba("el ACL tiene un usuario voz", "voz" in bloques, sorted(bloques))
    prueba("voz puede escribir casa/sala/voz/texto",
           "topic write casa/sala/voz/texto" in bloques.get("voz", []),
           bloques.get("voz"))
    prueba("voz NO puede escribir nada mas",
           bloques.get("voz") == ["topic write casa/sala/voz/texto"],
           bloques.get("voz"))
    prueba("el orquestador puede leer casa/sala/voz/texto",
           "topic read casa/sala/voz/texto" in bloques.get("orquestador", []),
           bloques.get("orquestador"))
else:
    prueba("existe el acl.conf para revisar el usuario voz", False, ruta_acl)


# =============================================================================
# 20d. Los recordatorios: siguiente vez, ciclo y no repetir
# =============================================================================
#
# `siguiente_vez` es pura (recibe `desde_epoch` y la zona), asi que se prueba con
# un jueves fijo y horas concretas, sin tocar la base ni el reloj. El ciclo se
# prueba sobre una base temporal, con un `publicar` que apunta lo publicado.
print("\n20d. Los recordatorios")
print("-----------------------")

from datetime import datetime, timedelta  # noqa: E402

ZONA = recordatorios_modulo.ZONA


def local_epoch(anio, mes, dia, hora, minuto):
    """El epoch de una hora LOCAL de Mazatlan."""
    return int(datetime(anio, mes, dia, hora, minuto, tzinfo=ZONA).timestamp())


# La referencia: jueves 2026-10-15 a las 12:00 en Mazatlan (isoweekday 4).
DESDE = local_epoch(2026, 10, 15, 12, 0)


def rec(**campos):
    """Una fila de recordatorio como la que devuelve la base (dict)."""
    fila = {"hora_min": 0, "repeticion": "diario", "fecha": None, "dias": None}
    fila.update(campos)
    return fila


prueba("Mazatlan es -7 h en invierno y en verano (sin horario de verano)",
       datetime(2026, 1, 15, tzinfo=ZONA).utcoffset() == timedelta(hours=-7)
       and datetime(2026, 7, 15, tzinfo=ZONA).utcoffset() == timedelta(hours=-7),
       (datetime(2026, 1, 15, tzinfo=ZONA).utcoffset(),
        datetime(2026, 7, 15, tzinfo=ZONA).utcoffset()))

prueba("diario a una hora que aun no ha pasado: hoy",
       recordatorios_modulo.siguiente_vez(rec(hora_min=15 * 60), DESDE)
       == local_epoch(2026, 10, 15, 15, 0))
prueba("diario a una hora ya pasada: manana",
       recordatorios_modulo.siguiente_vez(rec(hora_min=8 * 60), DESDE)
       == local_epoch(2026, 10, 16, 8, 0))
prueba("diario justo a la hora de referencia: manana (la busqueda es estricta)",
       recordatorios_modulo.siguiente_vez(rec(hora_min=12 * 60), DESDE)
       == local_epoch(2026, 10, 16, 12, 0))
prueba("semanal: el proximo dia marcado (viernes 5)",
       recordatorios_modulo.siguiente_vez(
           rec(repeticion="semanal", dias="5", hora_min=9 * 60), DESDE)
       == local_epoch(2026, 10, 16, 9, 0))
prueba("semanal: hoy toca y aun no pasa, es hoy",
       recordatorios_modulo.siguiente_vez(
           rec(repeticion="semanal", dias="4", hora_min=15 * 60), DESDE)
       == local_epoch(2026, 10, 15, 15, 0))
prueba("semanal: hoy toca pero ya paso, es la semana que viene",
       recordatorios_modulo.siguiente_vez(
           rec(repeticion="semanal", dias="4", hora_min=8 * 60), DESDE)
       == local_epoch(2026, 10, 22, 8, 0))
prueba("semanal sin dias no vuelve a tocar (None)",
       recordatorios_modulo.siguiente_vez(
           rec(repeticion="semanal", dias=""), DESDE) is None)
prueba("una_vez futura da esa fecha y hora",
       recordatorios_modulo.siguiente_vez(
           rec(repeticion="una_vez", fecha="2026-10-15", hora_min=15 * 60), DESDE)
       == local_epoch(2026, 10, 15, 15, 0))
prueba("una_vez ya pasada no toca (None)",
       recordatorios_modulo.siguiente_vez(
           rec(repeticion="una_vez", fecha="2026-10-15", hora_min=8 * 60), DESDE)
       is None)

# --- El registro de satelites conectados (clase en memoria) ---
conj = recordatorios_modulo.Satelites()
prueba("sin nadie, no hay satelite conectado", conj.hay_conectado() is False)
conj.actualizar("vm", True)
prueba("con uno conectado, hay; y se sabe cual",
       conj.hay_conectado() is True and conj.conectados() == ["vm"])
conj.actualizar("vm", False)
prueba("al desconectarse deja de haber",
       conj.hay_conectado() is False and conj.conectados() == [])

# --- El ciclo sobre una base temporal ---
base_rec, _ = base_nueva("recordatorios-")
AHORA = local_epoch(2026, 10, 15, 8, 0)
sat = recordatorios_modulo.Satelites()

# Un diario a las 8:00 que vence ahora, SIN satelite conectado.
base_rec.crear_recordatorio("rec-diario", "Tomar la medicina", 8 * 60,
                            "diario", None, None, "1001", AHORA - 60, AHORA)
apuntes, publicar = publicar_que_recoge()
n = recordatorios_modulo.procesar_vencidos(base_rec, publicar, sat, AHORA)
prueba("sin satelite conectado no se publica y el vencimiento queda pendiente",
       n == 1 and apuntes == [], apuntes)
entrega = base_rec.obtener_entrega("rec-diario", AHORA - 60)
prueba("la entrega queda 'pendiente' con su vencimiento",
       entrega is not None and entrega["estado"] == "pendiente", entrega)
fila = base_rec.obtener_recordatorio("rec-diario")
prueba("y avanza a la proxima vez (hoy 8:00 ya paso: manana)",
       fila["proximo_en"] == local_epoch(2026, 10, 16, 8, 0), dict(fila))

# Diez minutos despues se conecta un satelite: se le dice el pendiente reciente.
DIEZ = AHORA + 10 * 60
sat.actualizar("vm", True)
apuntes, publicar = publicar_que_recoge()
dichas = recordatorios_modulo.entregar_pendientes(base_rec, publicar, "vm", DIEZ)
prueba("al conectarse un satelite se le dice el pendiente reciente",
       dichas == 1 and len(apuntes) == 1, (dichas, apuntes))
if apuntes:
    _, carga, qos, _ = apuntes[0]
    prueba("con destino a ESE satelite y tipo recordatorio",
           carga.get("destino") == "vm" and carga.get("tipo") == "recordatorio",
           carga)
    prueba("y con la frase hablada aprobada, el texto dos veces",
           carga.get("texto")
           == "Le recuerdo: Tomar la medicina. Repito: Tomar la medicina.",
           carga.get("texto"))
    prueba("la entrega pasa a entregado con su destino",
           base_rec.obtener_entrega("rec-diario", AHORA - 60)["destino"] == "vm")

# Un vencimiento que YA tiene entrega no se repite aunque siga vencido.
base_rec.crear_recordatorio("rec-ya", "Regar las plantas", 8 * 60, "diario",
                            None, None, "1001", AHORA - 5, AHORA)
base_rec.registrar_entrega("rec-ya", AHORA - 5, "entregado", AHORA - 5)
apuntes, publicar = publicar_que_recoge()
n = recordatorios_modulo.procesar_vencidos(base_rec, publicar, sat, AHORA)
prueba("un vencimiento que ya tiene entrega NO se vuelve a decir",
       n == 0 and apuntes == [], apuntes)

# Con satelite conectado, un vencimiento nuevo se publica SIN destino.
base_rec.crear_recordatorio("rec-dos", "Sacar la basura", 9 * 60, "diario",
                            None, None, "1001", AHORA - 1, AHORA)
apuntes, publicar = publicar_que_recoge()
n = recordatorios_modulo.procesar_vencidos(base_rec, publicar, sat, AHORA)
prueba("con satelite conectado se publica, sin destino (lo dicen todos)",
       n == 1 and len(apuntes) == 1 and "destino" not in apuntes[0][1],
       apuntes)
entrega = base_rec.obtener_entrega("rec-dos", AHORA - 1)
prueba("y se marca entregado sin destino",
       entrega["estado"] == "entregado" and entrega["destino"] is None, entrega)

# Un pendiente que pasa la ventana de 30 min se marca no_entregado.
base_rec.registrar_entrega("rec-viejo", AHORA - 3600, "pendiente")
n = recordatorios_modulo.expirar_pendientes(base_rec, AHORA)
prueba("un pendiente de hace mas de 30 min se marca no_entregado",
       n == 1 and base_rec.obtener_entrega("rec-viejo", AHORA - 3600)["estado"]
       == "no_entregado", n)

# Un 'una_vez' ya dicho se cierra (hecho_en) y no se borra.
base_rec.crear_recordatorio("rec-una", "Pagar el agua", 8 * 60, "una_vez",
                            "2026-10-15", None, "1001", AHORA - 60, AHORA)
apuntes, publicar = publicar_que_recoge()
n = recordatorios_modulo.procesar_vencidos(base_rec, publicar, sat, AHORA)
fila = base_rec.obtener_recordatorio("rec-una")
prueba("un 'una_vez' ya dicho se cierra: activo=0, hecho_en, sin cancelar",
       n == 1 and fila["activo"] == 0 and fila["hecho_en"] == AHORA
       and fila["cancelado_en"] is None, dict(fila))

# Cancelar es idempotente.
prueba("cancelar devuelve True la primera vez",
       base_rec.cancelar_recordatorio("rec-dos", AHORA) is True)
prueba("y False la segunda (idempotente)",
       base_rec.cancelar_recordatorio("rec-dos", AHORA) is False)
prueba("cancelar un id que no existe tambien es False",
       base_rec.cancelar_recordatorio("rec-no-existe", AHORA) is False)
base_rec.cerrar()

# --- La migracion v4 -> v5 ---
ruta_v4 = os.path.join(tempfile.mkdtemp(prefix="recordatorios-v4-"),
                       "v4.sqlite3")
cruda = sqlite3.connect(ruta_v4)
cruda.execute("CREATE TABLE familiares (id INTEGER PRIMARY KEY, chat_id TEXT)")
cruda.execute("INSERT INTO familiares (id, chat_id) VALUES (1, '999')")
cruda.execute("PRAGMA user_version = 4")
cruda.commit()
cruda.close()

base_v4 = db_modulo.BaseDatos(ruta_v4)
# Ahora la version actual es la 6, asi que abrir una base en la 4 la lleva a la 6
# (pasando por la 5 de los recordatorios). Se compara contra VERSION_ESQUEMA y no
# contra un 6 literal, para que la prueba siga diciendo la verdad el dia que haya
# una 7.
prueba("una base en la version 4 sube a la version actual (%d)"
       % db_modulo.VERSION_ESQUEMA,
       base_v4.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA,
       base_v4.conexion.execute("PRAGMA user_version").fetchone()[0])
tablas_v4 = {f[0] for f in base_v4.conexion.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
prueba("y gana las dos tablas de recordatorios",
       {"recordatorios", "recordatorio_entregas"} <= tablas_v4, sorted(tablas_v4))
indices_v4 = {f[0] for f in base_v4.conexion.execute(
    "SELECT name FROM sqlite_master WHERE type='index'")}
prueba("y sus indices (proximo, creador y entregas por estado)",
       {"idx_recordatorios_proximo", "idx_recordatorios_creador",
        "idx_recordatorio_entregas_estado"} <= indices_v4, sorted(indices_v4))
fila_v4 = base_v4.conexion.execute(
    "SELECT chat_id FROM familiares WHERE id = 1").fetchone()
prueba("sin perder lo que ya habia en la base",
       fila_v4 is not None and fila_v4[0] == "999", fila_v4)
base_v4.cerrar()


# =============================================================================
# 21. Los client ids MQTT de cada servicio no se montan
# =============================================================================
#
# El 2026-10-07 los dos procesos usaban el mismo `MQTT_CLIENT_ID` (el del
# orquestador, de `config.py`), así que el panel y el orquestador se expulsaban
# mutuamente del broker y el panel podía tomar la sesión persistente del
# orquestador. La corrección separó los client ids en el `docker-compose.yml` y
# la sesión en `config.py`. Esta prueba falla si vuelven a coincidir o si el
# compose no los define de forma explícita (sin depender del valor por defecto).
print("\n21. Los client ids MQTT de cada servicio")
print("------------------------------------------")

ruta_compose = None
_candidato = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _ in range(4):
    _posible = os.path.join(_candidato, "docker-compose.yml")
    if os.path.exists(_posible):
        ruta_compose = _posible
        break
    _candidato = os.path.dirname(_candidato)


def limite_servicio(texto: str, desde: int):
    """El final del bloque de un servicio: la siguiente linea `  nombre:` al nivel de servicio."""
    fin = len(texto)
    for coincidencia in re.finditer(r"(?m)^  [A-Za-z0-9_]+:", texto[desde:]):
        fin = desde + coincidencia.start()
        break
    return fin


def client_id_y_sesion_de(servicio: str, texto: str):
    """El `MQTT_CLIENT_ID` y `MQTT_SESION_LIMPIA` del bloque `environment` de un servicio."""
    indice = texto.index("  %s:" % servicio)
    fin = limite_servicio(texto, indice + len(servicio) + 2)
    bloque = texto[indice:fin]
    valores = {}
    for linea in bloque.splitlines():
        linea = linea.strip()
        if linea.startswith("MQTT_CLIENT_ID:"):
            valores["id"] = linea.split(":", 1)[1].strip()
        if linea.startswith("MQTT_SESION_LIMPIA:"):
            valores["sesion"] = linea.split(":", 1)[1].strip().strip('"')
    return valores


if os.path.exists(ruta_compose):
    with open(ruta_compose, encoding="utf-8") as f:
        compose = f.read()
    orquestador = client_id_y_sesion_de("orquestador", compose)
    panel = client_id_y_sesion_de("panel", compose)

    prueba("el compose define el client id EXPLICITO del orquestador",
           orquestador.get("id") == "orquestador", orquestador)
    prueba("el compose define el client id EXPLICITO del panel",
           panel.get("id") == "panel-api", panel)
    prueba("y los dos client ids son DISTINTOS",
           bool(orquestador.get("id")) and orquestador["id"] != panel.get("id"),
           (orquestador.get("id"), panel.get("id")))
    prueba("el orquestador lleva sesion PERSISTENTE en el compose",
           orquestador.get("sesion") == "false", orquestador)
    prueba("el panel lleva sesion LIMPIA en el compose",
           panel.get("sesion") == "true", panel)
    prueba("ningun servicio comparte client id con otro",
           len({v.get("id") for v in (orquestador, panel) if v.get("id")}) == 2,
           (orquestador.get("id"), panel.get("id")))

    # El servicio de voz (etapa A) tambien tiene client id propio y explicito, y es
    # el unico con limites de CPU y RAM altos, porque es el unico que carga un
    # modelo. Los demas se quedan en 256m y 0.50.
    voz_compose = client_id_y_sesion_de("voz", compose)
    prueba("el compose define el client id EXPLICITO del servicio de voz",
           voz_compose.get("id") == "voz", voz_compose)
    prueba("el servicio de voz no usa sesion persistente",
           voz_compose.get("sesion") in (None, "true"), voz_compose)
    prueba("ningun servicio comparte client id con el de voz",
           len({orquestador.get("id"), panel.get("id"), voz_compose.get("id")}
               - {None}) == 3,
           (orquestador.get("id"), panel.get("id"), voz_compose.get("id")))

    indice_voz = compose.index("  voz:")
    bloque_voz = compose[indice_voz:limite_servicio(compose, indice_voz + 6)]
    prueba("el servicio de voz limita la RAM a 512m",
           "mem_limit: 512m" in bloque_voz, bloque_voz[:200])
    prueba("el servicio de voz limita la CPU a 1.0",
           "cpus: 1.0" in bloque_voz, None)
    prueba("el servicio de voz corre con el sistema de archivos en solo lectura",
           "read_only: true" in bloque_voz, None)
    prueba("el modelo de voz se monta en solo lectura",
           "./voz/modelos:/modelo:ro" in bloque_voz, None)
    prueba("el token del satelite es obligatorio en el compose",
           "VOZ_TOKEN_SHA256: ${VOZ_TOKEN_SHA256:?" in bloque_voz, None)
    prueba("la contrasena del usuario voz es obligatoria en el compose",
           "MQTT_VOZ_PASSWORD: ${MQTT_VOZ_PASSWORD:?" in bloque_voz, None)
else:
    prueba("existe el docker-compose.yml con los client ids", False, ruta_compose)


# --- Textos (Fase 2B): plurales, origen de la ayuda, lugar del gas, eco ------
titulo("Textos de la Fase 2B")

prueba("pedir registro con 0 personas: sin aviso de cuantas hay",
       textos.pedir_registro(0) == textos.PEDIR_REGISTRO, textos.pedir_registro(0))
prueba("pedir registro con 1 persona: singular y «ambos»",
       textos.pedir_registro(1).endswith(
           "Ya hay una persona registrada. Si usted se registra, ambos recibirán "
           "las alertas."), textos.pedir_registro(1))
prueba("pedir registro con 2 personas: plural y «todos»",
       textos.pedir_registro(2).endswith(
           "Ya hay 2 personas registradas. Si usted se registra, todos recibirán "
           "las alertas."), textos.pedir_registro(2))

prueba("registro listo con 0 avisos pendientes: sin «Además»",
       textos.registro_listo(0, False) == textos.REGISTRO_LISTO,
       textos.registro_listo(0, False))
prueba("registro listo con 1 aviso pendiente: singular",
       "Además, le llegará un aviso que estaba pendiente." in
       textos.registro_listo(1, False), textos.registro_listo(1, False))
prueba("registro listo con 2 avisos pendientes: plural",
       "Además, le llegarán 2 avisos que estaban pendientes." in
       textos.registro_listo(2, False), textos.registro_listo(2, False))
prueba("registro listo con más personas: lo dice",
       textos.registro_listo(0, True).endswith(
           "Las próximas alertas las recibirán todas las personas registradas."),
       textos.registro_listo(0, True))

prueba("/alertas con 0 personas",
       textos.personas_registradas(0) == "Todavía no hay ninguna persona registrada.")
prueba("/alertas con 1 persona: singular",
       textos.personas_registradas(1) == "Hay una persona registrada.")
prueba("/alertas con 2 personas: plural",
       textos.personas_registradas(2) == "Hay 2 personas registradas.")
prueba("destinos: 1 de 2", textos.destinos(1, 2) == "enviada a 1 de 2 personas",
       textos.destinos(1, 2))
prueba("destinos: 2 de 2", textos.destinos(2, 2) == "enviada a las 2 personas",
       textos.destinos(2, 2))
prueba("destinos: 1 de 1", textos.destinos(1, 1) == "enviada a 1 persona",
       textos.destinos(1, 1))
prueba("destinos: 0 de 2",
       textos.destinos(0, 2) == "todavía no se ha enviado a ninguna de las 2 personas",
       textos.destinos(0, 2))
prueba("destinos: sin copias, nada", textos.destinos(0, 0) == "")

for codigo, esperado in (("boton", "Se pidió ayuda por medio del botón de ayuda."),
                         ("voz", "Se pidió ayuda por medio del asistente de voz."),
                         ("alexa", "Se pidió ayuda por medio de Alexa."),
                         ("panel", "Se pidió ayuda por medio del panel.")):
    texto = alertas.texto_para_telegram({"id": "a-1", "origen": codigo}, "ayuda")
    prueba("ayuda con origen %s se traduce" % codigo, esperado in texto, texto)
    prueba("y no deja el codigo %r a la vista" % codigo,
           "(origen" not in texto and "medio de %s." % codigo not in texto, texto)

salida_log = io.StringIO()
manejador = logging.StreamHandler(salida_log)
logging.getLogger("alertas").addHandler(manejador)
texto = alertas.texto_para_telegram({"id": "a-2", "origen": "telepatia"}, "ayuda")
logging.getLogger("alertas").removeHandler(manejador)
prueba("ayuda con origen desconocido: «desde la casa»",
       "Se pidió ayuda desde la casa." in texto and "telepatia" not in texto, texto)
prueba("y el codigo desconocido queda en el log",
       "telepatia" in salida_log.getvalue(), salida_log.getvalue())
texto = alertas.texto_para_telegram({"id": "a-3"}, "ayuda")
prueba("ayuda sin origen: «desde la casa»",
       "Se pidió ayuda desde la casa." in texto, texto)
texto = alertas.texto_para_telegram({"id": "a-4", "origen": ["raro"]}, "ayuda")
prueba("ayuda con origen que no es texto: no revienta",
       "Se pidió ayuda desde la casa." in texto, texto)

esperado_ayuda = (
    "PETICIÓN DE AYUDA desde la casa.\n\n"
    "Se pidió ayuda por medio del botón de ayuda.\n\n"
    "Qué hacer:\n"
    "1. Llame de inmediato a la persona que está en la casa.\n"
    "2. Si no contesta o la situación es grave, llame al 911.\n"
    "3. Cuando todo esté resuelto, presione «Ya lo atendí».\n\n"
    "Este aviso lo envía el asistente de la casa de forma automática.\n"
    "Referencia de la alerta: a-5")
texto = alertas.texto_para_telegram({"id": "a-5", "origen": "boton"}, "ayuda")
prueba("el texto de ayuda es exactamente el aprobado", texto == esperado_ayuda, texto)

ubicacion_original = config.UBICACION_GAS
config.UBICACION_GAS = "cocina"
esperado_gas = (
    "ALERTA DE GAS en la casa.\n\n"
    "El sensor de la cocina detectó gas (valor: 2100).\n\n"
    "Qué hacer:\n"
    "1. Llame de inmediato a la persona que está en la casa. Pídale que no "
    "encienda ni apague luces ni aparatos, que abra puertas y ventanas y que "
    "salga de la casa.\n"
    "2. Si no contesta o la situación es grave, llame al 911.\n"
    "3. Cuando todo esté resuelto, presione «Ya lo atendí».\n\n"
    "Este aviso lo envía el asistente de la casa de forma automática.\n"
    "Referencia de la alerta: g-1")
texto = alertas.texto_para_telegram({"id": "g-1", "gas_adc": 2100}, "gas")
prueba("el texto de gas por defecto es exactamente el aprobado (la cocina)",
       texto == esperado_gas, texto)
config.UBICACION_GAS = "comedor"
texto = alertas.texto_para_telegram({"id": "g-2", "gas_adc": 2100}, "gas")
prueba("gas con lugar configurado y masculino: «del comedor»",
       "El sensor del comedor detectó gas" in texto, texto)
config.UBICACION_GAS = "la Sala "
texto = alertas.texto_para_telegram({"id": "g-3", "gas_adc": 2100}, "gas")
prueba("gas con lugar que ya trae articulo: se respeta",
       "El sensor de la sala detectó gas" in texto, texto)
config.UBICACION_GAS = ubicacion_original
prueba("lugar vacio = la cocina", textos.lugar_con_articulo("") == "la cocina")
_entorno_original = os.environ.pop("UBICACION_GAS", None)
import importlib  # noqa: E402
importlib.reload(config)
prueba("sin UBICACION_GAS en el entorno, config dice cocina",
       config.UBICACION_GAS == "cocina", config.UBICACION_GAS)
os.environ["UBICACION_GAS"] = "baño"
importlib.reload(config)
prueba("con UBICACION_GAS=baño en el entorno, config lo lee",
       config.UBICACION_GAS == "baño", config.UBICACION_GAS)
if _entorno_original is None:
    os.environ.pop("UBICACION_GAS", None)
else:
    os.environ["UBICACION_GAS"] = _entorno_original
importlib.reload(config)

texto = alertas.texto_para_telegram({"id": "x-1", "algo": 1}, "inventado")
prueba("tipo desconocido: «Aviso de la casa» y la referencia",
       texto == 'Aviso de la casa: {"id":"x-1","algo":1}\n\nReferencia de la alerta: x-1',
       texto)

for tipo, frase in (("gas", "La alerta de gas se marcó como atendida a las "),
                    ("ayuda", "La petición de ayuda se marcó como atendida a las ")):
    texto = alertas.texto_atendido(tipo, AHORA, "r-1")
    prueba("atendida (%s) con su frase" % tipo,
           texto.startswith("ATENDIDA.\n\n" + frase)
           and texto.endswith("\n\nReferencia de la alerta: r-1"), texto)
for tipo, frase in (("gas", "Su familia ya recibió la alerta de gas y la está atendiendo."),
                    ("ayuda", "Su familia ya recibió su petición de ayuda y la está atendiendo."),
                    ("otro", "Su familia ya recibió el aviso y lo está atendiendo.")):
    prueba("aviso hablado de atendida (%s)" % tipo,
           textos.aviso_atendida(tipo) == frase, textos.aviso_atendida(tipo))

for texto_raro in ("[unk]", "", "  [unk]  "):
    apuntes, publicar = publicar_que_recoge()
    voz.procesar(json.dumps({"frase_id": "f", "texto": texto_raro,
                             "modo": "comandos", "ts": int(time.time())}), publicar)
    prueba("eco con %r: frase de no entendido, no «Usted dijo»" % texto_raro,
           len(apuntes) == 1 and apuntes[0][1]["texto"] == textos.NO_ENTENDIDO,
           apuntes)

prueba("ningun texto de cara al usuario lleva «(s)»",
       not any("(s)" in v for k, v in vars(textos).items()
               if isinstance(v, str) and not k.startswith("__")))
prueba("motivo de codigo vencido, con sus minutos",
       textos.registro_fallido(textos.MOTIVO_VENCIDO.format(minutos=10))
       == "No se pudo completar el registro: el código ya venció (valía 10 "
          "minutos).\n\nPida un código nuevo.")


# --- Fase 2C: frase_id en el eco, alertas habladas y repeticion al conectarse --
titulo("Fase 2C: alertas habladas y estado por satelite")

apuntes, publicar = publicar_que_recoge()
voz.procesar(json.dumps({"frase_id": "satelite-vm-1791900000-1", "texto": "prende la luz",
                         "modo": "comandos", "ts": int(time.time())}), publicar)
prueba("la respuesta reenvia el frase_id del satelite que pregunto",
       any(c.get("frase_id") == "satelite-vm-1791900000-1" and c.get("tipo") == "respuesta"
           for _, c, *_ in apuntes), apuntes)
apuntes, publicar = publicar_que_recoge()
voz.procesar(json.dumps({"texto": "prende la luz", "ts": int(time.time())}), publicar)
prueba("sin frase_id, la respuesta sale sin frase_id (no se inventa uno)",
       all("frase_id" not in c for t, c, *_ in apuntes if t == config.TOPICO_SATELITE_DECIR)
       and any(t == config.TOPICO_SATELITE_DECIR for t, *_ in apuntes), apuntes)

ubicacion_original = config.UBICACION_GAS
config.UBICACION_GAS = "cocina"
prueba("texto hablado del gas, con el lugar y su articulo",
       textos.alerta_hablada("gas", textos.lugar_con_articulo("cocina"))
       == "Atención: se detectó gas en la cocina. Por favor, no encienda ni apague "
          "luces ni aparatos, abra las ventanas y salga de la casa. Estoy avisando a "
          "su familia.")
prueba("texto hablado del gas con un lugar masculino",
       "se detectó gas en el comedor." in
       textos.alerta_hablada("gas", textos.lugar_con_articulo("comedor")))
prueba("texto hablado de la ayuda",
       textos.alerta_hablada("ayuda", "x")
       == "No se preocupe, estoy avisando a su familia. Alguien se comunicará con "
          "usted pronto.")
prueba("un tipo sin texto hablado no dice nada",
       textos.alerta_hablada("inventado", "x") is None)

base_2c, _ = base_nueva("fase2c-")
base_2c.registrar_familiar("552000000001")
for topico, cuerpo, tipo in (
        (config.TOPICO_ALERTA_GAS, '{"id":"2c0000-b0001-0001","gas_adc":2100}', "gas"),
        (config.TOPICO_ALERTA_AYUDA, '{"id":"2c0000-b0001-0002","origen":"boton"}',
         "ayuda")):
    apuntes, publicar = publicar_que_recoge()
    alertas.procesar(topico, cuerpo, base_2c, publicar)
    prueba("al registrar una alerta de %s se publica la alerta hablada" % tipo,
           len(apuntes) == 1 and apuntes[0][0] == config.TOPICO_SATELITE_DECIR
           and apuntes[0][1]["tipo"] == "alerta"
           and apuntes[0][1]["texto"] == textos.alerta_hablada(
               tipo, textos.lugar_con_articulo(config.UBICACION_GAS))
           and "destino" not in apuntes[0][1]
           and abs(apuntes[0][1]["ts"] - int(time.time())) <= 2
           and apuntes[0][2] == 1, apuntes)
    prueba("y la alerta sigue yendo a la cola de Telegram (%s)" % tipo,
           base_2c.obtener_alerta(json.loads(cuerpo)["id"]) is not None)

apuntes, publicar = publicar_que_recoge()
alertas.procesar(config.TOPICO_ALERTA_GAS, '{"id":"2c0000-b0001-0001","gas_adc":2100}',
                 base_2c, publicar)
prueba("un duplicado NO vuelve a decir la alerta", apuntes == [], apuntes)
# El antirrebote cuenta las ayudas que YA salieron a Telegram.
base_2c.marcar_enviado("2c0000-b0001-0002", "552000000001")
apuntes, publicar = publicar_que_recoge()
alertas.procesar(config.TOPICO_ALERTA_AYUDA, '{"id":"2c0000-b0001-0003","origen":"voz"}',
                 base_2c, publicar)
prueba("una ayuda dentro del antirrebote NO se vuelve a decir", apuntes == [], apuntes)

# --- Origen de la ayuda: Alexa anuncia; voz no (evita doble voz) ---
base_origen, _ = base_nueva("ayuda-origen-")
apuntes, publicar = publicar_que_recoge()
alertas.procesar(config.TOPICO_ALERTA_AYUDA,
                 '{"id":"origen-alexa-1","origen":"alexa"}',
                 base_origen, publicar)
prueba("ayuda de Alexa se anuncia por satelite/decir",
       any(c.get("tipo") == "alerta" for _, c, *_ in apuntes), apuntes)
prueba("ayuda de Alexa queda en la base (Telegram)",
       base_origen.obtener_alerta("origen-alexa-1") is not None)
apuntes, publicar = publicar_que_recoge()
alertas.procesar(config.TOPICO_ALERTA_AYUDA,
                 '{"id":"origen-voz-1","origen":"voz"}',
                 base_origen, publicar)
prueba("ayuda de voz NO se anuncia por satelite/decir (la anuncia el orquestador)",
       not any(c.get("tipo") == "alerta" for _, c, *_ in apuntes), apuntes)
prueba("ayuda de voz queda en la base (Telegram)",
       base_origen.obtener_alerta("origen-voz-1") is not None)
base_origen.cerrar()


def publicar_que_falla(topico, carga, qos=1, retain=False):
    raise RuntimeError("broker caido")


prueba("si publicar la alerta hablada falla, la alerta se registra igual",
       alertas.procesar(config.TOPICO_ALERTA_GAS,
                        '{"id":"2c0000-b0001-0004","gas_adc":2200}', base_2c,
                        publicar_que_falla) is True
       and base_2c.obtener_alerta("2c0000-b0001-0004") is not None)

# Repeticion al conectarse un satelite.
TOPICO_VM = "casa/sala/satelite/satelite-vm/estado"
prueba("client id del topico de estado",
       alertas.client_id_de_estado(TOPICO_VM) == "satelite-vm"
       and alertas.client_id_de_estado("casa/sala/satelite/estado") is None
       and alertas.client_id_de_estado("casa/sala/satelite/+/estado") is None)

ahora_2c = int(time.time())
apuntes, publicar = publicar_que_recoge()
n = alertas.al_estado_satelite(TOPICO_VM, '{"conectado":true}', False, base_2c,
                               publicar, ahora=ahora_2c)
prueba("al conectarse un satelite se le repite cada gas sin atender (hay 2)",
       n == 2 and len(apuntes) == 2, (n, apuntes))
prueba("solo a el (destino = su client id), con tipo alerta",
       all(a[1].get("destino") == "satelite-vm" and a[1]["tipo"] == "alerta"
           and a[1]["texto"].startswith("Atención: se detectó gas") for a in apuntes),
       apuntes)
prueba("la ayuda NO se repite al conectarse (solo el gas)",
       not any("No se preocupe" in a[1]["texto"] for a in apuntes), apuntes)

apuntes, publicar = publicar_que_recoge()
n = alertas.al_estado_satelite(TOPICO_VM, '{"conectado":true}', True, base_2c,
                               publicar, ahora=ahora_2c)
prueba("con el estado RETENIDO (reinicio del orquestador) no repite nada",
       n == 0 and apuntes == [], apuntes)
apuntes, publicar = publicar_que_recoge()
n = alertas.al_estado_satelite(TOPICO_VM, '{"conectado":false}', False, base_2c,
                               publicar, ahora=ahora_2c)
prueba("con conectado:false (Last Will) no repite nada", n == 0 and apuntes == [])
apuntes, publicar = publicar_que_recoge()
alertas.al_estado_satelite(TOPICO_VM, "no es json", False, base_2c, publicar)
prueba("un estado que no es JSON no rompe ni repite", apuntes == [])

apuntes, publicar = publicar_que_recoge()
n = alertas.al_estado_satelite(TOPICO_VM, '{"conectado":true}', False, base_2c,
                               publicar, ahora=ahora_2c + 2 * 3600 + 60)
prueba("una alerta de gas de mas de 2 h NO se repite", n == 0 and apuntes == [],
       apuntes)

alertas.resolver(base_2c, None, "2c0000-b0001-0001", ahora_2c)
alertas.resolver(base_2c, None, "2c0000-b0001-0004", ahora_2c)
apuntes, publicar = publicar_que_recoge()
n = alertas.al_estado_satelite(TOPICO_VM, '{"conectado":true}', False, base_2c,
                               publicar, ahora=ahora_2c)
prueba("sin alertas de gas pendientes no se dice nada", n == 0 and apuntes == [],
       apuntes)

apuntes, publicar = publicar_que_recoge()
orquestador_modulo.enrutar(TOPICO_VM, '{"conectado":true}', base_2c, publicar,
                           retenido=False)
prueba("enrutar manda el estado de un satelite a al_estado_satelite, no a alertas",
       base_2c.obtener_alerta("satelite-vm") is None and apuntes == [], apuntes)
prueba("el orquestador se suscribe al estado de todos los satelites",
       config.TOPICO_SATELITE_ESTADO == "casa/sala/satelite/+/estado")
base_2c.cerrar()
config.UBICACION_GAS = ubicacion_original


# =============================================================================
# 22. Inactividad (Fase 4): el reloj, el ajuste en caliente y el aviso
# =============================================================================
#
# El reloj de la inactividad no reacciona a UN mensaje, sino a la AUSENCIA de
# mensajes durante un tiempo. Se prueba con un reloj falso (todas las funciones
# reciben `ahora=`), que es lo unico que hace repetible una prueba basada en el
# tiempo: con `sleep` la suite tardaria media hora y aun asi seria fragil.
titulo("22. Inactividad")

# --- La franja de configuracion y el valor por defecto de la base ---
base_cfg, _ = base_nueva("inactividad-cfg-")
prueba("sin valor guardado, el tiempo de inactividad es el de fabrica",
       base_cfg.tiempo_inactividad() == config.TIEMPO_INACTIVIDAD_MIN_DEFECTO,
       base_cfg.tiempo_inactividad())
prueba("el rango por defecto es 2..720 min",
       (config.TIEMPO_INACTIVIDAD_MIN_MINIMO,
        config.TIEMPO_INACTIVIDAD_MIN_MAXIMO) == (2, 720),
       (config.TIEMPO_INACTIVIDAD_MIN_MINIMO, config.TIEMPO_INACTIVIDAD_MIN_MAXIMO))

base_cfg.guardar_tiempo_inactividad(5)
prueba("guardar 5 min deja el valor en la base",
       base_cfg.tiempo_inactividad() == 5, base_cfg.tiempo_inactividad())
for malo in (1, 721, 0, -3):
    try:
        base_cfg.guardar_tiempo_inactividad(malo)
        prueba("guardar %r fuera de rango lanza ValueError" % malo, False, malo)
    except ValueError:
        prueba("guardar %r fuera de rango lanza ValueError" % malo, True)

# Un valor que se cuela en la base por otra via (una version vieja, una edicion a
# mano) NO se usa: se cae al de fabrica. Es lo que impide que un 0 dispare la
# alerta en bucle.
for malo in ("1", "99999", "abc", "0"):
    base_cfg._config_escribir("tiempo_inactividad_min", malo)
    prueba("un valor invalido %r en la base cae al de fabrica" % malo,
           base_cfg.tiempo_inactividad() == config.TIEMPO_INACTIVIDAD_MIN_DEFECTO,
           base_cfg.tiempo_inactividad())
base_cfg.cerrar()

# --- El reloj: siembra, presencia, episodio unico y no repetir ---
base_i, _ = base_nueva("inactividad-")
base_i.registrar_familiar("555000000001")
T0 = 1_700_000_000
reloj = inactividad_modulo.Estado(base_i, ahora=T0)
prueba("al arrancar sin valor, el reloj se siembra con la hora de arranque",
       reloj.ultimo_movimiento == T0, reloj.ultimo_movimiento)
prueba("y se persiste, para que un reinicio no lo borre",
       base_i.ultimo_movimiento() == T0, base_i.ultimo_movimiento())

# Sin saber nada del nodo, no se avisa aunque pase mucho tiempo: no hay señal.
apuntes, publicar = publicar_que_recoge()
prueba("sin noticia del nodo NO se avisa (no se puede saber si hay movimiento)",
       reloj.verificar(publicar, ahora=T0 + 10 ** 6) is False, apuntes)
reloj.registrar_nodo('{"conectado":true}', ahora=T0)
prueba("con el nodo conectado y el reloj recien sembrado, no toca",
       reloj.verificar(publicar, ahora=T0) is False, apuntes)

# La presencia true mueve el reloj; el primer false tras un true tambien (es
# cuando se sabe que el movimiento paro); un false suelto no.
reloj.registrar_sensores('{"presencia":true}', ahora=T0 + 10)
prueba("un presencia true mueve el reloj", reloj.ultimo_movimiento == T0 + 10,
       reloj.ultimo_movimiento)
reloj.registrar_sensores('{"presencia":false}', ahora=T0 + 70)
prueba("el primer false tras un true mueve el reloj al momento en que paro",
       reloj.ultimo_movimiento == T0 + 70, reloj.ultimo_movimiento)
reloj.registrar_sensores('{"presencia":false}', ahora=T0 + 130)
prueba("un false sin un true delante NO mueve el reloj (son latidos)",
       reloj.ultimo_movimiento == T0 + 70, reloj.ultimo_movimiento)

# El borde exacto del periodo.
LIMITE = T0 + 70 + config.TIEMPO_INACTIVIDAD_MIN_DEFECTO * 60
apuntes, publicar = publicar_que_recoge()
prueba("un segundo antes del periodo no se avisa",
       reloj.verificar(publicar, ahora=LIMITE - 1) is False, apuntes)
apuntes, publicar = publicar_que_recoge()
prueba("justo al cumplir el periodo se avisa",
       reloj.verificar(publicar, ahora=LIMITE) is True, apuntes)
ID_ESPERADO = "srv-inactividad-%d" % (T0 + 70)
prueba("la alerta se publica en el topico del contrato, con id y minutos",
       len(apuntes) == 1 and apuntes[0][0] == config.TOPICO_ALERTA_INACTIVIDAD
       and apuntes[0][1]["id"] == ID_ESPERADO
       and apuntes[0][1]["minutos"] == config.TIEMPO_INACTIVIDAD_MIN_DEFECTO
       and apuntes[0][2] == 1 and apuntes[0][3] is False, apuntes)
prueba("y queda en la cola de Telegram, para la familia",
       base_i.obtener_alerta(ID_ESPERADO) is not None)

# El episodio esta abierto: no se repite, aunque el reloj siga corriendo.
apuntes, publicar = publicar_que_recoge()
prueba("una vez abierto el episodio, no se vuelve a avisar",
       reloj.verificar(publicar, ahora=LIMITE + 100000) is False and apuntes == [],
       apuntes)

# «Ya lo atendí» resuelve la alerta pero NO reinicia el reloj.
apuntes, publicar = publicar_que_recoge()
alertas.resolver(base_i, publicar, ID_ESPERADO, ahora=LIMITE + 1)
prueba("resolver una inactividad NO habla por satelite/decir (es silenciosa)",
       apuntes == [], apuntes)
prueba("y NO reinicia ultimo_movimiento (atender no es moverse)",
       reloj.ultimo_movimiento == T0 + 70, reloj.ultimo_movimiento)
apuntes, publicar = publicar_que_recoge()
prueba("tras atender, el episodio sigue abierto: no se repite",
       reloj.verificar(publicar, ahora=LIMITE + 200) is False and apuntes == [],
       apuntes)

# Un movimiento nuevo cierra el episodio y arranca otro.
reloj.registrar_sensores('{"presencia":true}', ahora=T0 + 200000)
prueba("un presencia true cierra el episodio", reloj.episodio_abierto is False)
# Ajuste en caliente: el panel escribe y la SIGUIENTE vuelta lo usa. Sin reiniciar.
base_i.guardar_tiempo_inactividad(5)
SIG = T0 + 200000
apuntes, publicar = publicar_que_recoge()
prueba("con el ajuste nuevo (5 min) aun no toca a los 4:59",
       reloj.verificar(publicar, ahora=SIG + 4 * 60 + 59) is False, apuntes)
apuntes, publicar = publicar_que_recoge()
prueba("y a los 5:00 avisa: el ciclo RELEYO el ajuste sin reiniciar",
       reloj.verificar(publicar, ahora=SIG + 5 * 60) is True, apuntes)
prueba("con el numero de minutos nuevo en el aviso",
       apuntes and apuntes[0][1]["minutos"] == 5, apuntes)

# Reinicio del orquestador con el episodio ya avisado: el id determinista evita
# el doble aviso aunque `episodio_abierto` (memoria) se haya perdido.
reloj_reinicio = inactividad_modulo.Estado(base_i, ahora=SIG + 5 * 60 + 30)
prueba("tras reiniciar, el reloj se lee de la base (no se siembra de nuevo)",
       reloj_reinicio.ultimo_movimiento == SIG, reloj_reinicio.ultimo_movimiento)
reloj_reinicio.registrar_nodo('{"conectado":true}', ahora=SIG + 5 * 60 + 30)
apuntes, publicar = publicar_que_recoge()
prueba("y NO vuelve a avisar el mismo episodio (el id ya estaba)",
       reloj_reinicio.verificar(publicar, ahora=SIG + 6 * 60) is False
       and apuntes == [], apuntes)
base_i.cerrar()

# --- El nodo desconectado y el enlace caido bloquean el aviso ---
base_b, _ = base_nueva("inactividad-bloqueo-")
base_b.registrar_familiar("555000000002")
reloj_b = inactividad_modulo.Estado(base_b, ahora=T0)
reloj_b.registrar_nodo('{"conectado":false}', ahora=T0)
apuntes, publicar = publicar_que_recoge()
prueba("con el nodo desconectado no se avisa aunque pase el tiempo",
       reloj_b.verificar(publicar, ahora=T0 + 10 ** 6) is False, apuntes)

# El nodo PASA a conectado: el reloj se reinicia (durante el corte no se vio nada).
reloj_b.registrar_nodo('{"conectado":true}', ahora=T0 + 10 ** 6)
prueba("al reconectar el nodo, el reloj se reinicia",
       reloj_b.ultimo_movimiento == T0 + 10 ** 6, reloj_b.ultimo_movimiento)
apuntes, publicar = publicar_que_recoge()
prueba("y justo al reconectar no hay aviso",
       reloj_b.verificar(publicar, ahora=T0 + 10 ** 6) is False, apuntes)

# El enlace del sitio: solo un 0 EXPLICITO lo cae. Vacio o ausente = conectado.
reloj_b.registrar_enlace("1")
prueba("un 1 en el enlace no marca caido", reloj_b.sitio_conectado is True)
reloj_b.registrar_enlace("0")
prueba("un 0 explicito marca el enlace caido", reloj_b.sitio_conectado is False)
apuntes, publicar = publicar_que_recoge()
prueba("con el enlace caido no se avisa aunque el nodo este conectado",
       reloj_b.verificar(publicar, ahora=T0 + 10 ** 6 + 10 ** 6) is False, apuntes)
reloj_b.registrar_enlace("1")
prueba("al volver el enlace, el reloj se reinicia",
       reloj_b.sitio_conectado is True
       and reloj_b.ultimo_movimiento == int(time.time()), reloj_b.ultimo_movimiento)

# Ausencia de noticia del enlace (modo instalacion, sin puente): conectado.
reloj_n, _ = base_nueva("inactividad-sinpuente-")
reloj_n = inactividad_modulo.Estado(reloj_n, ahora=T0)
prueba("sin noticia del enlace, se trata como conectado (sitio_conectado None)",
       reloj_n.sitio_conectado is None)
reloj_n.registrar_nodo('{"conectado":true}', ahora=T0)
reloj_n.registrar_sensores('{"presencia":true}', ahora=T0)
apuntes, publicar = publicar_que_recoge()
prueba("y por tanto SI se avisa cuando toca (no se queda mudo para siempre)",
       reloj_n.verificar(publicar, ahora=T0 + config.TIEMPO_INACTIVIDAD_MIN_DEFECTO * 60)
       is True, apuntes)
base_b.cerrar()

# --- La reconexion de MQTT tambien reinicia el reloj ---
base_r, _ = base_nueva("inactividad-reconexion-")
reloj_r = inactividad_modulo.Estado(base_r, ahora=T0)
reloj_r.registrar_nodo('{"conectado":true}', ahora=T0)
reloj_r.registrar_sensores('{"presencia":true}', ahora=T0 + 100)
prueba("antes de reconectar, el reloj esta en el movimiento",
       reloj_r.ultimo_movimiento == T0 + 100)
reloj_r.registrar_reconexion(ahora=T0 + 100000)
prueba("al reconectar MQTT, el reloj se reinicia",
       reloj_r.ultimo_movimiento == T0 + 100000, reloj_r.ultimo_movimiento)
reloj_r.registrar_reconexion(ahora=T0 + 50000)
prueba("y nunca lo ATRASA (mensaje fuera de orden)",
       reloj_r.ultimo_movimiento == T0 + 100000, reloj_r.ultimo_movimiento)
base_r.cerrar()

# --- El texto del aviso (Telegram) y la frase de atendida ---
texto_inact = alertas.texto_para_telegram(
    {"id": "srv-inactividad-1", "minutos": 120}, "inactividad")
prueba("el texto de Telegram lleva la frase literal con los minutos",
       "No se ha detectado movimiento en la sala en los últimos 120 minutos."
       in texto_inact, texto_inact)
prueba("y la referencia de la alerta (como las demas)",
       "Referencia de la alerta: srv-inactividad-1" in texto_inact, texto_inact)
prueba("la frase de atendida de inactividad existe y es distinta",
       "inactividad" in textos.ATENDIDA_FRASE,
       textos.ATENDIDA_FRASE.get("inactividad"))

# --- El reparto por topico de las señales del reloj ---
base_e, _ = base_nueva("inactividad-enrutar-")
reloj_e = inactividad_modulo.Estado(base_e, ahora=T0)
apuntes, publicar = publicar_que_recoge()
orquestador_modulo.enrutar(config.TOPICO_SENSORES, '{"presencia":true}', base_e,
                           publicar, reloj=reloj_e)
prueba("enrutar manda los sensores al reloj (no a alertas)",
       reloj_e.presencia_anterior is True
       and base_e.listar_recientes() == [], base_e.listar_recientes())
orquestador_modulo.enrutar(config.TOPICO_NODO_ESTADO, '{"conectado":true}', base_e,
                           publicar, reloj=reloj_e)
prueba("enrutar manda el estado del nodo al reloj",
       reloj_e.nodo_conectado is True)
orquestador_modulo.enrutar("$SYS/broker/connection/puente-sitio/state", "0",
                           base_e, publicar, reloj=reloj_e)
prueba("enrutar reconoce el $SYS del enlace por FORMA y lo manda al reloj",
       reloj_e.sitio_conectado is False)
prueba("el reconocedor del enlace acepta el topico real y rechaza otro",
       inactividad_modulo.es_enlace_sitio(
           "$SYS/broker/connection/puente-sitio/state") is True
       and inactividad_modulo.es_enlace_sitio("casa/sala/nodo/estado") is False)
# Sin reloj (pruebas viejas), las señales se ignoran y NO se crean alertas.
apuntes, publicar = publicar_que_recoge()
orquestador_modulo.enrutar(config.TOPICO_SENSORES, '{"presencia":true}', base_e,
                           publicar)
prueba("sin reloj, enrutar ignora las señales de inactividad sin romper",
       base_e.listar_recientes() == [])
base_e.cerrar()

# --- El gancho de reconexion del cliente MQTT: no en la primera conexion ---
llamadas = []
cliente_falso = mqtt_cliente.ClienteMQTT.__new__(mqtt_cliente.ClienteMQTT)
cliente_falso.al_reconectar = lambda: llamadas.append(1)
cliente_falso._primera_conexion = True
cliente_falso.conectado = False
cliente_falso._hilo_red = None
cliente_falso.suscribirse = lambda: None


class _CodigoOk:
    is_failure = False


cliente_falso._al_conectar(None, None, None, _CodigoOk())
prueba("la PRIMERA conexion no dispara el gancho de reconexion", llamadas == [],
       llamadas)
cliente_falso._al_conectar(None, None, None, _CodigoOk())
prueba("una reconexion SI lo dispara (reinicia el reloj)", llamadas == [1], llamadas)

# --- El ACL: el orquestador puede con las señales; el panel no toca la alerta ---
if os.path.exists(ruta_acl):
    prueba("el orquestador puede publicar casa/sala/alerta/inactividad",
           "topic write casa/sala/alerta/inactividad"
           in bloques.get("orquestador", []), bloques.get("orquestador"))
    for topico in ("casa/sala/sensores", "casa/sala/nodo/estado",
                   "$SYS/broker/connection/+/state"):
        prueba("el orquestador puede leer %s" % topico,
               "topic read %s" % topico in bloques.get("orquestador", []),
               bloques.get("orquestador"))
    prueba("el panel NO lee ni escribe casa/sala/alerta/inactividad",
           "topic read casa/sala/alerta/inactividad" not in bloques.get("panel", [])
           and "topic write casa/sala/alerta/inactividad"
           not in bloques.get("panel", []), bloques.get("panel"))

# --- La migracion v5 -> v6 ---
ruta_v5 = os.path.join(tempfile.mkdtemp(prefix="inactividad-v5-"), "v5.sqlite3")
cruda5 = sqlite3.connect(ruta_v5)
cruda5.execute("CREATE TABLE familiares (chat_id TEXT PRIMARY KEY, nombre TEXT, "
               "registrado_en INTEGER, activo INTEGER DEFAULT 1)")
cruda5.execute("INSERT INTO familiares (chat_id, registrado_en) VALUES ('999', 1)")
cruda5.execute("PRAGMA user_version = 5")
cruda5.commit()
cruda5.close()
base_v5 = db_modulo.BaseDatos(ruta_v5)
prueba("una base en la version 5 sube a la actual (%d)"
       % db_modulo.VERSION_ESQUEMA,
       base_v5.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA,
       base_v5.conexion.execute("PRAGMA user_version").fetchone()[0])
tablas_v5 = {f[0] for f in base_v5.conexion.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
prueba("y gana la tabla configuracion", "configuracion" in tablas_v5,
       sorted(tablas_v5))
prueba("y sin valor guardado, el tiempo de inactividad es el de fabrica",
       base_v5.tiempo_inactividad() == config.TIEMPO_INACTIVIDAD_MIN_DEFECTO)
prueba("sin perder lo que ya habia en la base",
       base_v5.familiar_registrado("999") is True)
base_v5.cerrar()


# =============================================================================
# 23. Sensores sin datos, origen «arranque», hora del firmware y el conteo del fan out
# =============================================================================
#
# La entrega del 2026-10-19 cambia tres cosas en el firmware y una en el servidor, y
# ninguna de las cuatro se puede probar con una red: se comprueba leyendo el codigo
# que va a compilar (la misma filosofia de la seccion 21, que ya lee el acl.conf y
# el docker-compose) y, en el servidor, con una base de 1 activo y 1 inactivo.
print("\n23. Sensores sin datos, arranque, hora y el conteo del fan out")
print("---------------------------------------------------------------")

# --- a) El log del fan out cuenta ACTIVOS, no el total ------------------------
#
# insertar_alerta hace el fan out SOLO a familiares_activos (db.insertar_alerta), y
# el log decia contar_familiares(), que cuenta tambien a los dados de baja: en la
# base real (2 registrados, 1 activo) salia «encolada para 2 familiar(es)» con un
# solo envio. Un log que miente sobre cuantos reciben es un log con el que no se
# puede diagnosticar nada.
base_fan, _ = base_nueva("fanout-")
base_fan.registrar_familiar("1111111111111")
base_fan.registrar_familiar("2222222222222")
base_fan.marcar_familiar_inactivo("2222222222222")
# inactividad y alertas tienen cada uno su logger (logging.getLogger("inactividad")
# y "alertas"). El manipulador de arriba cuelga del de alertas, asi que para esta
# prueba hace falta capturar tambien el de inactividad.
log_inactividad = []


class _CapturaInactividad(logging.Handler):
    def emit(self, registro_emitido):
        log_inactividad.append(registro_emitido.getMessage())


manejador_inactividad = _CapturaInactividad()
manejador_inactividad.setLevel(logging.WARNING)
inactividad_modulo.registro.addHandler(manejador_inactividad)
inactividad_modulo.registro.setLevel(logging.WARNING)

T0F = 1_700_000_000
reloj_fan = inactividad_modulo.Estado(base_fan, ahora=T0F)
reloj_fan.registrar_nodo('{"conectado":true}', ahora=T0F)
reloj_fan.registrar_sensores('{"presencia":true}', ahora=T0F + 1)
reloj_fan.registrar_sensores('{"presencia":false}', ahora=T0F + 10)
base_fan.guardar_tiempo_inactividad(2)
log_inactividad.clear()
apuntes_fan, publicar_fan = publicar_que_recoge()
alertada_fan = reloj_fan.verificar(publicar_fan, ahora=T0F + 10 + 120)
avisos_fan = [m for m in log_inactividad if "encolada" in m]
prueba("con 1 activo y 1 inactivo la inactividad si se avisa",
       alertada_fan is True and apuntes_fan, apuntes_fan)
prueba("y el log dice 1 familiar activo, no el total de 2",
       len(avisos_fan) == 1 and "1 familiar(es) activo(s)" in avisos_fan[0]
       and "2 familiar" not in avisos_fan[0], avisos_fan)
envios_fan = base_fan.conexion.execute(
    "SELECT COUNT(*) FROM envios WHERE alerta_id = ?",
    ("srv-inactividad-%d" % (T0F + 10),)).fetchone()[0]
prueba("y solo hay UN envio en la base, el del familiar activo", envios_fan == 1,
       envios_fan)
inactividad_modulo.registro.removeHandler(manejador_inactividad)

# El mismo log, ahora para una alerta de ayuda (alertas.procesar).
log_capturado.clear()
base_fan2, _ = base_nueva("fanout2-")
base_fan2.registrar_familiar("1111111111111")
base_fan2.registrar_familiar("2222222222222")
base_fan2.marcar_familiar_inactivo("2222222222222")
alertas.procesar(config.TOPICO_ALERTA_AYUDA,
                 '{"id":"000000-b023-0001","origen":"alexa"}', base_fan2)
avisos_gas = [r.getMessage() for r in log_capturado if "encolada" in r.getMessage()]
prueba("el log de alertas.procesar tambien cuenta 1 familiar activo",
       len(avisos_gas) == 1 and "1 familiar(es) activo(s)" in avisos_gas[0]
       and "ningun familiar" not in avisos_gas[0], avisos_gas)
prueba("y solo un envio en la base", base_fan2.conexion.execute(
    "SELECT COUNT(*) FROM envios WHERE alerta_id = ?",
    ("000000-b023-0001",)).fetchone()[0] == 1)

# Sin NINGUN activo (el unico registrado esta dado de baja), el log lo dice.
log_capturado.clear()
base_fan3, _ = base_nueva("fanout3-")
base_fan3.registrar_familiar("3333333333333")
base_fan3.marcar_familiar_inactivo("3333333333333")
alertas.procesar(config.TOPICO_ALERTA_AYUDA,
                 '{"id":"000000-b023-0002","origen":"alexa"}', base_fan3)
avisos_baja = [r.getMessage() for r in log_capturado if "encolada" in r.getMessage()]
prueba("sin ningun familiar activo el log lo dice y no inventa un numero",
       len(avisos_baja) == 1 and "ningun familiar activo" in avisos_baja[0],
       avisos_baja)
prueba("y la alerta se guarda igual, esperando a que se active alguien",
       base_fan3.obtener_alerta("000000-b023-0002") is not None)

# --- b) Un latido con temp/hum/gas en null no rompe el orquestador ------------
#
# Es lo que manda el firmware cuando el DHT22 falla tres veces, el MQ-2 calienta o
# la bandera del sensor esta en false. El orquestador solo mira `presencia`, y ese
# «solo» hay que probarlo, no creerlo.
base_nul, _ = base_nueva("nulos-")
T0N = 1_700_000_000
reloj_nul = inactividad_modulo.Estado(base_nul, ahora=T0N)
reloj_nul.registrar_nodo('{"conectado":true}', ahora=T0N)
try:
    reloj_nul.registrar_sensores(
        '{"temp_c":null,"hum_pct":null,"gas_adc":null,"presencia":true}',
        ahora=T0N + 1)
    nulos_ok = True
except Exception as error:  # noqa: BLE001
    nulos_ok = error
prueba("un latido con temp, hum y gas en null no revienta", nulos_ok is True,
       nulos_ok)
prueba("y la presencia que trae el mismo mensaje si se tiene en cuenta",
       reloj_nul.ultimo_movimiento == T0N + 1, reloj_nul.ultimo_movimiento)

# --- c) El firmware: lo que hay que ver en el codigo --------------------------
ruta_firmware = None
_candidato = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _ in range(4):
    _posible = os.path.join(_candidato, "firmware", "platformio.ini")
    if os.path.exists(_posible):
        ruta_firmware = os.path.dirname(_posible)
        break
    _candidato = os.path.dirname(_candidato)

if ruta_firmware:
    def _leer(relativa):
        with open(os.path.join(ruta_firmware, relativa), encoding="utf-8") as f:
            return f.read()

    hora_cpp = _leer("src/hora.cpp")
    hora_h = _leer("src/hora.h")
    config_h = _leer("include/config.h")
    sensores_cpp = _leer("src/sensores.cpp")
    sensores_h = _leer("src/sensores.h")
    gas_cpp = _leer("src/gas.cpp")
    nodo_estado_cpp = _leer("src/nodo_estado.cpp")
    luz_cpp = _leer("src/luz.cpp")
    platformio_ini = _leer("platformio.ini")

    # Hora (decision 7). El NTP primario es el de la VM y el respaldo el publico.
    prueba("el NTP primario es el de la VM y el respaldo el publico",
           'NTP_PRIMARIO[] = "192.168.1.169"' in hora_cpp
           and 'NTP_RESPALDO[] = "pool.ntp.org"' in hora_cpp, hora_cpp[:400])
    prueba("el reintento sin hora baja de 60 s a 5 s",
           "MS_REINTENTO_SIN_HORA = 5000UL" in hora_cpp, None)
    # hora_es_valida lee el reloj en cada llamada, sin depender de la bandera.
    cuerpo_hora_valida = hora_cpp[hora_cpp.find("bool hora_es_valida()"):]
    cuerpo_hora_valida = cuerpo_hora_valida[:cuerpo_hora_valida.find("\n}")]
    prueba("hora_es_valida lee time(nullptr) y compara con el umbral",
           "time(nullptr)" in cuerpo_hora_valida
           and "UMBRAL_HORA_VALIDA" in cuerpo_hora_valida, cuerpo_hora_valida)
    prueba("y no devuelve la bandera guardada",
           "return hora_valida" not in cuerpo_hora_valida, cuerpo_hora_valida)
    cuerpo_epoch = hora_cpp[hora_cpp.find("int64_t hora_epoch_o_desconocida()"):]
    cuerpo_epoch = cuerpo_epoch[:cuerpo_epoch.find("\n}")]
    prueba("hora_epoch_o_desconocida usa hora_es_valida, no la bandera",
           "hora_es_valida()" in cuerpo_epoch, cuerpo_epoch)
    prueba("el -1 de «todavia no hay hora» sigue siendo -1 y no 0",
           "hora_es_valida() ? hora_epoch() : -1" in cuerpo_epoch, cuerpo_epoch)
    prueba("hora.h documenta que el criterio se aplica leyendo el reloj",
           "LEYENDO el reloj" in hora_h, None)

    # Sensores (decisiones 1 y 2): sin sensor no hay valor inventado.
    prueba("el DHT22 esta en el GPIO 32 del cableado real",
           "#define GPIO_PIN_DHT22 32" in config_h, None)
    prueba("las dos banderas de sensor arrancan en true",
           "#define DHT22_HABILITADO true" in config_h
           and "#define MQ2_HABILITADO true" in config_h, None)
    prueba("el MQ-2 calienta 3 minutos antes de dar gas_adc",
           "MQ2_MS_CALIENTE MS_3MIN" in config_h
           and "MQ2_MS_CALIENTE" in sensores_cpp, None)
    prueba("el DHT22 se lee cada 2 s y cae a null a los 3 fallos",
           "DHT22_MS_ENTRE_LECTURAS 2000UL" in config_h
           and "DHT22_FALLIDAS_MAX 3" in config_h
           and "DHT22_FALLIDAS_MAX" in sensores_cpp, None)
    prueba("el ADC del MQ-2 sigue con atenuacion de 11 dB",
           "analogSetPinAttenuation(GPIO_PIN_GAS_ADC, ADC_11db)" in sensores_cpp
           and "ADCN_GAS ADC1_CHANNEL_6" in config_h, None)
    prueba("los campos sin dato van como null explicito en el JSON de sensores",
           'String("null")' in sensores_cpp
           and "lectura.hay_temp ?" in sensores_cpp
           and "lectura.hay_hum ?" in sensores_cpp
           and "lectura.hay_gas ?" in sensores_cpp, None)
    prueba("gas_adc sin dato sale null tambien en nodo/estado",
           '\\"gas_adc\\":null' in nodo_estado_cpp
           and "gas < 0" in nodo_estado_cpp, None)
    prueba("y el resumen del monitor dice sin_dato en vez de -1",
           '"sin_dato"' in nodo_estado_cpp, None)
    prueba("la simulacion de temp, hum y gas se borro",
           "#define SIM_GAS_POR_DEFECTO" not in config_h
           and "#define SIM_TEMP_BASE_C" not in config_h
           and "#define SIM_HUM_BASE_PCT" not in config_h
           and "#define SIM_TEMP_VARIACION_C" not in config_h
           and "sensores_aleatorio_entre" not in sensores_h
           and "SIM_GAS_POR_DEFECTO" not in sensores_cpp, None)
    prueba("y sigue existiendo la de presencia, que es el respaldo del PIR",
           "#define SIM_PRESENCIA_PERIODO_S 180" in config_h, None)
    prueba("el gas sin dato no abre ni cierra episodios",
           "SENSORES_SIN_GAS" in gas_cpp
           and "Sin dato no se abren ni se cierran episodios" in gas_cpp
           and "int ultima_lectura = SIM_GAS_POR_DEFECTO" not in gas_cpp, None)
    prueba("la libreria del DHT22 va con version fija en platformio.ini",
           "adafruit/DHT sensor library@1.4.7" in platformio_ini
           and "adafruit/Adafruit Unified Sensor@1.1.15" in platformio_ini, None)

    # Origen «arranque» (decision 4): lo publica el nodo al arrancar, y solo eso.
    prueba("la luz nace apagada con origen arranque",
           '#define LUZ_ORIGEN_ARRANQUE "arranque"' in config_h
           and "origen_actual = LUZ_ORIGEN_ARRANQUE" in luz_cpp, None)
    prueba("y el arranque en frio deja el estado como conocido",
           "estado_conocido = true" in luz_cpp, None)
    prueba("arranque NO se acepta como origen de una orden de luz/set",
           "LUZ_ORIGEN_ARRANQUE" not in _leer("src/contrato.cpp"), None)
    prueba("el orquestador no mete arranque en los origenes de AYUDA",
           "arranque" not in config.ORIGENES_VALIDOS, config.ORIGENES_VALIDOS)
else:
    prueba("existe el firmware con platformio.ini para revisar el codigo", False,
           ruta_firmware)

# --- d) El nodo simulado: «arranque» al conectar y comandos «sin» -------------
ruta_simulador = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "nodo_simulado", "nodo_simulado.py")
if os.path.exists(ruta_simulador):
    with open(ruta_simulador, encoding="utf-8") as _f:
        simulador = _f.read()
    prueba("el simulador publica luz/estado con origen «arranque» al conectar",
           'publicar_luz_estado("arranque")' in simulador, None)
    prueba("tiene comandos para publicar campos sin dato (temp/hum/gas sin)",
           "temp <valor> | temp sin" in simulador
           and "hum <valor> | hum sin" in simulador
           and "gas oscilar | gas sin" in simulador, None)
    prueba("y su comando estado no usa %d con un gas que puede ser None",
           'gas_texto = "sin dato" if gas_sin_dato' in simulador, None)
else:
    prueba("existe el nodo simulado para revisarlo", False, ruta_simulador)


# =============================================================================
# 24. Fase 2: el umbral de gas, `resuelto_por` y la config del nodo
# =============================================================================
#
# Tres cosas de las decisiones aprobadas el 2026-10-20 que no se pueden probar con
# la red (aquí no hay broker):
#
#   a) `GAS_ON_ADC` / `GAS_OFF_ADC` en `config.py`, con su validación, y el
#      mensaje `{"gas_on_adc":...,"gas_off_adc":...}` en `casa/sala/nodo/config`,
#      RETENIDO y con QoS 1, publicado al conectar.
#   b) La migración v7 -> v8 (`alertas.resuelto_por`) y `marcar_resuelta`
#      guardando el NOMBRE de quien atendió, NUNCA un chat_id.
#   c) El ACL sigue dejando escribir `nodo/config` al orquestador, sin tocarlo.

titulo("24. Fase 2: umbral de gas, resuelto_por y nodo/config")

import subprocess  # noqa: E402

# --- a) Los umbrales, leídos del entorno -------------------------------------
prueba("config trae el umbral de gas por defecto: 1800 al encender y 1550 al soltar",
       config.GAS_ON_ADC == 1800 and config.GAS_OFF_ADC == 1550,
       (config.GAS_ON_ADC, config.GAS_OFF_ADC))
prueba("y el de soltar siempre queda por debajo del de encender",
       0 < config.GAS_OFF_ADC < config.GAS_ON_ADC < 4096,
       (config.GAS_ON_ADC, config.GAS_OFF_ADC))
prueba("el tópico de la config del nodo es el del contrato",
       config.TOPICO_NODO_CONFIG == "casa/sala/nodo/config",
       config.TOPICO_NODO_CONFIG)


def config_con_umbral(**entorno):
    """`(GAS_ON_ADC, GAS_OFF_ADC)` de un proceso limpio con ese entorno.

    Se prueba en un proceso aparte y no con `importlib.reload(config)`: recargar
    el módulo en mitad de las pruebas reejecutaría TODO `config.py` (con el
    entorno cambiado a medias) sobre un módulo al que el resto ya le toma su
    nombre, y un fallo ahí se vería como un fallo de cualquier otra cosa.
    """
    entorno_completo = dict(os.environ)
    entorno_completo.pop("GAS_ON_ADC", None)
    entorno_completo.pop("GAS_OFF_ADC", None)
    entorno_completo.update(entorno)
    salida = subprocess.run(
        [sys.executable, "-c",
         "import config; print(config.GAS_ON_ADC, config.GAS_OFF_ADC)"],
        cwd=RAIZ, env=entorno_completo, capture_output=True, text=True, timeout=60,
    )
    return salida.stdout.strip(), salida.stderr.strip()[-200:]


valor, error = config_con_umbral(GAS_ON_ADC="2000", GAS_OFF_ADC="1600")
prueba("un par del entorno se usa tal cual (el umbral es configurable)",
       valor == "2000 1600", (valor, error))
valor, error = config_con_umbral(GAS_ON_ADC="1500", GAS_OFF_ADC="1600")
prueba("un par imposible (soltar por ENCIMA de encender) cae en los de fábrica",
       valor == "1800 1550", (valor, error))
valor, _ = config_con_umbral(GAS_ON_ADC="9999")
prueba("y un umbral fuera del rango del ADC, también", valor == "1800 1550", valor)


# --- a2) `nodo/config` publicado AL CONECTAR ---------------------------------
class _CodigoFalso:
    """Solo lo que `_al_conectar` le pregunta: si el código de éxito falla."""
    is_failure = False


class _DobleRed:
    """Un doble del `cliente` de paho que anota publicaciones y suscripciones."""

    def __init__(self):
        self.publicados = []
        self.suscritos = []
        self.esperas = 0

    def publish(self, topico, cuerpo, qos=1, retain=False):
        self.publicados.append((topico, cuerpo, qos, retain))
        return self

    def wait_for_publish(self, timeout=None):
        self.esperas += 1

    def subscribe(self, topico, qos=1):
        self.suscritos.append((topico, qos))
        return (0, 1)


def cliente_de_prueba():
    """Un `ClienteMQTT` sin `__init__`, para llamarle `_al_conectar` a mano.

    Igual que hace la sección de publicaciones de más arriba: el `__init__`
    exige la contraseña del broker, y aquí hace falta un cliente entero que
    nunca se conecta de verdad.
    """
    doble = mqtt_cliente.ClienteMQTT.__new__(mqtt_cliente.ClienteMQTT)
    doble.cliente = _DobleRed()
    doble.al_recibir = lambda *args, **kwargs: None
    doble.al_reconectar = None
    doble._primera_conexion = True
    doble.conectado = False
    doble._hilo_red = None
    doble.contador_ayuda = 0
    return doble


al_conectar = cliente_de_prueba()
al_conectar._al_conectar(None, None, None, _CodigoFalso())
publicados = [p for p in al_conectar.cliente.publicados
              if p[0] == config.TOPICO_NODO_CONFIG]
prueba("al conectar publica en casa/sala/nodo/config", len(publicados) == 1,
       publicados)
cuerpo_config = json.loads(publicados[0][1])
prueba("con gas_on_adc y gas_off_adc, en el formato del contrato",
       cuerpo_config == {"gas_on_adc": config.GAS_ON_ADC,
                         "gas_off_adc": config.GAS_OFF_ADC}, cuerpo_config)
prueba("retenido y con QoS 1, para que lo reciba quien se conecte después",
       publicados[0][2] == 1 and publicados[0][3] is True, publicados[0])
esperados = [config.TOPICO_ALERTA_GAS, config.TOPICO_ALERTA_AYUDA,
             config.TOPICO_VOZ_TEXTO, config.TOPICO_SATELITE_ESTADO,
             config.TOPICO_SENSORES, config.TOPICO_NODO_ESTADO,
             config.TOPICO_ENLACE_SITIO, config.TOPICO_LUZ_ESTADO]
prueba("y la suscripción sigue siendo la de siempre, sin nodo/config",
       [t for t, _ in al_conectar.cliente.suscritos] == esperados,
       al_conectar.cliente.suscritos)
prueba("la primera conexión no dispara el aviso de reconexión",
       al_conectar._primera_conexion is False, al_conectar._primera_conexion)

reconectado = cliente_de_prueba()
reconectado._al_conectar(None, None, None, _CodigoFalso())
reconectado._al_conectar(None, None, None, _CodigoFalso())
prueba("y AL RECONECTAR vuelve a publicarla, con la config del proceso actual",
       len([p for p in reconectado.cliente.publicados
            if p[0] == config.TOPICO_NODO_CONFIG]) == 2,
       reconectado.cliente.publicados)


# --- b) La migración v7 -> v8 y el NOMBRE de quien atendió -------------------
ruta_v7 = os.path.join(tempfile.mkdtemp(prefix="resuelto-por-v7-"), "v7.sqlite3")
cruda7 = sqlite3.connect(ruta_v7)
cruda7.execute(
    "CREATE TABLE alertas ("
    "id TEXT NOT NULL PRIMARY KEY, tipo TEXT NOT NULL, "
    "carga_json TEXT NOT NULL, resuelto_en INTEGER, descartada_en INTEGER, "
    "creado_en INTEGER NOT NULL, purgable_en INTEGER NOT NULL, "
    "fanout_hecho INTEGER NOT NULL DEFAULT 0)")
cruda7.execute("INSERT INTO alertas (id, tipo, carga_json, creado_en, "
               "purgable_en, resuelto_en) "
               "VALUES ('a1', 'gas', '{}', 111, 222, 333)")
cruda7.execute("INSERT INTO alertas (id, tipo, carga_json, creado_en, "
               "purgable_en) VALUES ('a2', 'ayuda', '{}', 444, 555)")
cruda7.execute("PRAGMA user_version = 7")
cruda7.commit()
cruda7.close()
base_v7 = db_modulo.BaseDatos(ruta_v7)
prueba("una base en la versión 7 sube a la actual (%d)"
       % db_modulo.VERSION_ESQUEMA,
       base_v7.conexion.execute("PRAGMA user_version").fetchone()[0]
       == db_modulo.VERSION_ESQUEMA,
       base_v7.conexion.execute("PRAGMA user_version").fetchone()[0])
columnas_v7 = {f["name"] for f in
               base_v7.conexion.execute("PRAGMA table_info(alertas)")}
prueba("y alertas gana la columna resuelto_por", "resuelto_por" in columnas_v7,
       sorted(columnas_v7))
filas_v7 = {f["id"]: f for f in base_v7.conexion.execute("SELECT * FROM alertas")}
prueba("sin perder ninguna fila de antes",
       set(filas_v7) == {"a1", "a2"}
       and filas_v7["a1"]["resuelto_en"] == 333
       and filas_v7["a2"]["creado_en"] == 444, sorted(filas_v7))
prueba("y las de antes quedan con resuelto_por en NULL (no se inventa historico)",
       filas_v7["a1"]["resuelto_por"] is None
       and filas_v7["a2"]["resuelto_por"] is None, None)
base_v7.cerrar()

# Quién atendió, por los DOS caminos (Telegram y panel).
base_24, _ruta_24 = base_nueva("fase2-")
base_24.registrar_familiar("3003", nombre="Ana")
base_24.registrar_familiar("4004", nombre="Luis")
base_24.insertar_alerta("f2-1", "gas", json.dumps({"gas_adc": 2100}),
                        chat_ids=["3003"])
base_24.marcar_resuelta("f2-1", quien="Ana")
fila_24 = base_24.obtener_alerta("f2-1")
prueba("marcar_resuelta guarda el NOMBRE en resuelto_por",
       fila_24["resuelto_por"] == "Ana", fila_24["resuelto_por"])
base_24.marcar_resuelta("f2-1", quien="Luis")
prueba("y una segunda pulsación no cambia al de la primera",
       base_24.obtener_alerta("f2-1")["resuelto_por"] == "Ana",
       base_24.obtener_alerta("f2-1")["resuelto_por"])

base_24.insertar_alerta("f2-2", "gas", json.dumps({"gas_adc": 2100}),
                        chat_ids=["3003"])
alertas.resolver(base_24, None, "f2-2", quien="el familiar ...3003",
                 origen="el boton del bot", nombre_quien=base_24.nombre_de("3003"))
prueba("por el botón del bot se guarda el nombre del familiar",
       base_24.obtener_alerta("f2-2")["resuelto_por"] == "Ana",
       base_24.obtener_alerta("f2-2")["resuelto_por"])
base_24.insertar_alerta("f2-3", "gas", json.dumps({"gas_adc": 2100}),
                        chat_ids=["4004"])
alertas.resolver(base_24, None, "f2-3", quien="el familiar ...4004",
                 origen="el panel", nombre_quien="")
prueba("sin nombre se guarda «Familiar» y no un chat_id",
       base_24.obtener_alerta("f2-3")["resuelto_por"] == "Familiar",
       base_24.obtener_alerta("f2-3")["resuelto_por"])
prueba("ningún resuelto_por es un chat_id",
       all(base_24.obtener_alerta(i)["resuelto_por"] in ("Ana", "Familiar")
           for i in ("f2-1", "f2-2", "f2-3")), None)

# Un aviso SIN resolver todavía tiene su fila en `envios` (al resolverse se
# cancelan los que no salieron), y esa fila es la que la línea de tiempo enseña.
base_24.insertar_alerta("f2-4", "ayuda", json.dumps({"origen": "voz"}),
                        chat_ids=["3003"])
envios_24 = base_24.envios_de_alerta("f2-4")
prueba("envios_de_alerta trae el nombre y NO el chat_id",
       len(envios_24) == 1
       and set(envios_24[0].keys()) == {"creado_en", "enviado_en", "nombre"}
       and envios_24[0]["nombre"] == "Ana", sorted(envios_24[0].keys()))
base_24.cerrar()


# --- c) El ACL: sigue sin tocarse --------------------------------------------
ruta_acl_24 = os.path.join(
    os.path.dirname(RAIZ), "mosquitto", "config", "acl.conf")
if os.path.exists(ruta_acl_24):
    with open(ruta_acl_24, encoding="utf-8") as f_24:
        bloques_24 = bloques_de_usuario(f_24.read())
    prueba("el ACL deja al orquestador ESCRIBIR casa/sala/nodo/config "
           "(verificado sin tocarlo)",
           "topic write casa/sala/nodo/config" in bloques_24.get("orquestador", []),
           bloques_24.get("orquestador"))
    prueba("y el panel sigue SIN leerlo: el umbral le llega por /api/estado",
           "topic read casa/sala/nodo/config" not in bloques_24.get("panel", []),
           bloques_24.get("panel"))
else:
    prueba("existe el acl.conf para verificar la escritura de nodo/config", False,
           ruta_acl_24)



# =============================================================================
# Chat de dos vias: conversacion
# =============================================================================

titulo("Chat: conversacion")

base, ruta = base_nueva("chat-")
base.registrar_familiar("111", nombre="Ana")
id1 = base.crear_mensaje_conversacion("Ana", "111", "panel", "hola", ahora=1000)
prueba("crear mensaje devuelve id", isinstance(id1, int) and id1 > 0)
fila = base.obtener_conversacion(id1)
prueba("mensaje nace con estado enviado", fila["estado"] == "enviado")
prueba("autor_chat_id se guarda internamente", fila["autor_chat_id"] == "111")
base.actualizar_estado_conversacion(id1, "se_dijo", ahora=1001)
prueba("estado se actualiza a se_dijo", base.obtener_conversacion(id1)["estado"] == "se_dijo")

# rate limit
base.crear_mensaje_conversacion("Ana", "111", "telegram", "otro", ahora=1002)
ult = base.ultimo_mensaje_de("111")
prueba("ultimo_mensaje_de devuelve la fila", ult is not None and int(ult["creado_en"]) == 1002)

# paginacion
for i in range(60):
    base.crear_mensaje_conversacion("Ana", "111", "panel", "m%d" % i, ahora=2000 + i)
filas, hay_mas = base.listar_conversacion(limite=5)
prueba("listar_conversacion devuelve 5", len(filas) == 5)
prueba("hay_mas es True con mas de 5", hay_mas is True)
# antes_id
filas2, _ = base.listar_conversacion(limite=5, antes_id=filas[0]["id"])
prueba("paginacion con antes_id devuelve filas", len(filas2) == 5)

# backfill de alertas en migracion: simular version 8
base2, ruta2 = base_nueva("chatmig-")
base2.insertar_alerta("alerta-mig", "gas", '{"id":"alerta-mig","gas_adc":2100}')
base2.conexion.execute("PRAGMA user_version=8")
base2.conexion.execute("DROP TABLE IF EXISTS conversacion")
base2.conexion.execute("DROP TABLE IF EXISTS conversacion_envios")
base2.conexion.commit()
base2.cerrar()
base3 = db_modulo.BaseDatos(ruta2)
mig = base3.conexion.execute(
    "SELECT * FROM conversacion WHERE alerta_id='alerta-mig'").fetchall()
prueba("backfill crea entrada tipo alerta", len(mig) == 1 and mig[0]["tipo"] == "alerta")
base3.cerrar()

# ventana de respuesta se reinicia: comprobar en voz
import voz
voz.abrir_ventana_respuesta()
primera = voz._ventana_hasta["valor"]
voz.abrir_ventana_respuesta()
prueba("abrir ventana actualiza el limite", voz._ventana_hasta["valor"] >= primera)

# texto_libre fuera de la ventana se ignora: procesar no registra nada
voz.cerrar_ventana_respuesta()
antes = COMPROBACIONES[0]
# procesar con texto_libre pero sin ventana; no debe lanzar ni guardar
voz.procesar('{"ts": %d, "texto": "enciende la luz", "texto_libre": "secreto libre", "frase_id": "f1"}' % int(__import__("time").time()),
             lambda *a, **k: None, base)
prueba("procesar fuera de ventana no lanza", True)

# Migracion 9->10 con filas en conversacion_envios: las filas se conservan y
# foreign_key_check queda vacio.
base_mig, ruta_mig = base_nueva("mig9a10-")
base_mig.insertar_alerta("alerta-mig9", "gas", '{"id":"alerta-mig9","gas_adc":2100}')
base_mig.conexion.execute("PRAGMA user_version=9")
# simular una tabla conversacion v9 (sin 'aviso') con una fila
base_mig.conexion.execute("DROP TABLE IF EXISTS conversacion")
base_mig.conexion.execute("""CREATE TABLE conversacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT, creado_en INTEGER NOT NULL,
    tipo TEXT NOT NULL CHECK(tipo IN ('mensaje','respuesta','alerta')),
    autor TEXT, origen TEXT, autor_chat_id TEXT, texto TEXT,
    estado TEXT, estado_en INTEGER, alerta_id TEXT, notificado_en INTEGER,
    purgable_en INTEGER NOT NULL)""")
base_mig.conexion.execute("""INSERT INTO conversacion (id, creado_en, tipo, alerta_id, purgable_en)
    VALUES (1, 100, 'alerta', 'alerta-mig9', 200)""")
base_mig.conexion.execute("""CREATE TABLE IF NOT EXISTS conversacion_envios (
    conversacion_id INTEGER NOT NULL, chat_id TEXT NOT NULL,
    mensaje_id INTEGER, editado_en INTEGER, creado_en INTEGER NOT NULL,
    PRIMARY KEY (conversacion_id, chat_id))""")
base_mig.conexion.execute("""INSERT INTO conversacion_envios (conversacion_id, chat_id, creado_en)
    VALUES (1, '111', 100)""")
base_mig.conexion.commit()
base_mig.cerrar()
base_mig2 = db_modulo.BaseDatos(ruta_mig)
envios_mig = base_mig2.conexion.execute(
    "SELECT count(*) FROM conversacion_envios").fetchone()[0]
fk_mig = base_mig2.conexion.execute("PRAGMA foreign_key_check").fetchall()
prueba("migracion 9->10 conserva filas de conversacion_envios",
       envios_mig == 1, envios_mig)
prueba("migracion 9->10 deja foreign_key_check vacio",
       fk_mig == [], fk_mig)
base_mig2.cerrar()

base.cerrar()


# =============================================================================
# Privacidad de logs: texto libre y mensajes del chat no se registran
# =============================================================================

titulo("Privacidad de logs")

import io as _io
import logging as _logging

# voz.procesar con texto libre, fuera de ventana: no debe aparecer el texto libre
voz.cerrar_ventana_respuesta()
buf = _io.StringIO()
handler = _logging.StreamHandler(buf)
handler.setLevel(_logging.DEBUG)
for name in ("voz", "mqtt", "orquestador"):
    _logging.getLogger(name).addHandler(handler)
secreto = "TEXTO-SECRETO-LIBRE-123"
voz.procesar('{"ts": %d, "texto": "[unk]", "texto_libre": "%s", "frase_id": "fpriv"}'
             % (int(time.time()), secreto),
             lambda *a, **k: None, None)
for name in ("voz", "mqtt", "orquestador"):
    _logging.getLogger(name).removeHandler(handler)
salida = buf.getvalue()
prueba("el texto libre no aparece en el log de voz", secreto not in salida, salida[:200])

# mqtt_cliente no registra el payload completo de voz/texto
buf2 = _io.StringIO()
h2 = _logging.StreamHandler(buf2)
h2.setLevel(_logging.DEBUG)
_logging.getLogger("mqtt").addHandler(h2)
import mqtt_cliente as _mc
# simular _al_mensaje con un mensaje falso
class _Msg:
    topic = "casa/sala/voz/texto"
    payload = ('{"texto":"SECRETO-MQTT","texto_libre":"LIBRE-MQTT"}').encode()
    retain = False
mc = _mc.ClienteMQTT.__new__(_mc.ClienteMQTT)
mc._hilo_red = None
# evitar init completo: llamar solo _al_mensaje con un cliente falso no necesario
try:
    _mc.ClienteMQTT._al_mensaje(mc, None, None, _Msg())
except Exception:
    pass
_logging.getLogger("mqtt").removeHandler(h2)
salida2 = buf2.getvalue()
prueba("mqtt no registra el payload de voz/texto",
       "SECRETO-MQTT" not in salida2 and "LIBRE-MQTT" not in salida2,
       salida2[:200])


# =============================================================================
# Chat: ayuda dentro de la ventana de respuesta
# =============================================================================

titulo("Chat: ayuda dentro de la ventana")

import json as _json_ayuda
_gram = _json_ayuda.load(open(os.path.join(RAIZ, "..", "voz", "gramatica.json"), encoding="utf-8"))
_frases_ayuda = _gram["intenciones"]["AYUDA"]

for _frase in _frases_ayuda:
    _base, _ruta = base_nueva("ayuda-vent-")
    voz.abrir_ventana_respuesta()
    _publicados = []
    def _pub(topico, carga, qos=1, retain=False, esperar=True):
        _publicados.append((topico, carga))
    voz.procesar(
        _json_ayuda.dumps({"ts": int(time.time()), "texto": _frase,
                           "frase_id": "fayuda"}),
        _pub, _base)
    _respuestas = _base.conexion.execute(
        "SELECT COUNT(*) AS n FROM conversacion WHERE tipo='respuesta'").fetchone()["n"]
    prueba("ayuda '%s' dentro de la ventana NO se guarda como respuesta" % _frase,
           _respuestas == 0)
    prueba("ayuda '%s' dentro de la ventana publica alerta de ayuda" % _frase,
           any(t == config.TOPICO_ALERTA_AYUDA for t, _ in _publicados))
    _base.cerrar()

# Fuera de la ventana se comporta igual (genera alerta).
_base2, _ = base_nueva("ayuda-fuera-")
_pub2 = []
voz.cerrar_ventana_respuesta()
voz.procesar(
    _json_ayuda.dumps({"ts": int(time.time()), "texto": _frases_ayuda[0],
                       "frase_id": "fayuda2"}),
    lambda t, c, qos=1, retain=False, esperar=True: _pub2.append((t, c)), _base2)
prueba("ayuda fuera de la ventana tambien publica alerta de ayuda",
       any(t == config.TOPICO_ALERTA_AYUDA for t, _ in _pub2))
_base2.cerrar()


# =============================================================================
# 13. Errores permanentes de Telegram
# =============================================================================

titulo("13. Errores permanentes de Telegram")

from telegram.error import (BadRequest, Forbidden, NetworkError, RetryAfter,
                            TimedOut, TelegramError)

# 13a. Forbidden -> permanente: no se reintenta y queda marcado como fallido.
base, _ = base_nueva("perm-forbidden-")
base.registrar_familiar("111", nombre="Marta")
base.insertar_alerta("000000-pf-0001", "gas",
                     '{"id":"000000-pf-0001","gas_adc":2100}')

class RemitentePermanente(cola_modulo.TrabajadorTelegram.__class__ and object):
    pass

class RemPerm:
    def __init__(self, exc):
        self.exc = exc
        self.token = "x"
        self.disponible = True
        self.enviados = []
    def enviar(self, texto, teclado=None, chat_id=None):
        raise self.exc
    def editar_mensaje(self, *a, **k):
        return True

trab = cola_modulo.TrabajadorTelegram(base, RemPerm(Forbidden("bot was blocked by the user")))
trab._intentar_uno()
fila = base.obtener_envio("000000-pf-0001", "111")
prueba("Forbidden marca el envio como fallido de forma permanente",
       fila is not None and fila["proximo_intento_en"] == db_modulo.ENVIO_FALLIDO_PERMANENTE,
       fila["proximo_intento_en"] if fila else None)
prueba("Forbidden no borra el envio ni lo marca enviado",
       fila is not None and fila["enviado_en"] is None, fila)
prueba("Forbidden deja ultimo_error con el tipo",
       fila is not None and fila["ultimo_error"] == "Forbidden", fila["ultimo_error"] if fila else None)
prueba("Forbidden no desactiva al familiar",
       base.familiar_activo("111") is True)
# segundo intento no vuelve a llamar a Telegram
antes = fila["intentos"]
trab._intentar_uno()
prueba("Forbidden no se reintenta (intentos no suben)",
       base.obtener_envio("000000-pf-0001", "111")["intentos"] == antes,
       base.obtener_envio("000000-pf-0001", "111")["intentos"])
prueba("Forbidden deja la cola vacia",
       base.tomar_envios_pendientes() == [])
base.cerrar()

# 13b. BadRequest "chat not found" -> permanente.
base, _ = base_nueva("perm-badrequest-")
base.registrar_familiar("111", nombre="Marta")
base.insertar_alerta("000000-pb-0001", "gas",
                     '{"id":"000000-pb-0001","gas_adc":2100}')
trab = cola_modulo.TrabajadorTelegram(
    base, RemPerm(BadRequest("Bad Request: chat not found")))
trab._intentar_uno()
fila = base.obtener_envio("000000-pb-0001", "111")
prueba("BadRequest chat not found es permanente",
       fila["proximo_intento_en"] == db_modulo.ENVIO_FALLIDO_PERMANENTE,
       fila["proximo_intento_en"])
prueba("BadRequest chat not found no se reintenta",
       base.tomar_envios_pendientes() == [])
base.cerrar()

# 13c. BadRequest de formato -> se reintenta y queda ERROR en el log sin el texto.
base, _ = base_nueva("perm-formato-")
base.registrar_familiar("111", nombre="Marta")
base.insertar_alerta("000000-pf2-0001", "gas",
                     '{"id":"000000-pf2-0001","gas_adc":2100}')
manipulador = logging.Handler()
capturado = []
manipulador.emit = lambda r: capturado.append("%s %s" % (r.levelname, r.getMessage()))
cola_modulo.registro.addHandler(manipulador)
cola_modulo.registro.setLevel(logging.DEBUG)
trab = cola_modulo.TrabajadorTelegram(
    base, RemPerm(BadRequest("Message text is empty")))
trab._intentar_uno()
texto_log = "\n".join(capturado)
prueba("BadRequest de formato deja el envio pendiente para reintentar",
       base.obtener_envio("000000-pf2-0001", "111")["proximo_intento_en"]
       != db_modulo.ENVIO_FALLIDO_PERMANENTE
       and base.obtener_envio("000000-pf2-0001", "111")["proximo_intento_en"]
       <= int(time.time()) + 100)
prueba("BadRequest de formato queda en el log como ERROR",
       "ERROR" in texto_log and "BadRequest" in texto_log)
prueba("el log del BadRequest de formato no contiene el texto del mensaje",
       "Message text is empty" not in texto_log, texto_log[:200])
cola_modulo.registro.removeHandler(manipulador)
base.cerrar()

# 13d. NetworkError, RetryAfter y 5xx se reintentan.
for nombre_exc, exc in (("NetworkError", NetworkError("network down")),
                        ("RetryAfter", RetryAfter(10)),
                        ("TimedOut", TimedOut("timed out")),
                        ("5xx", TelegramError("Internal Server Error"))):
    base, _ = base_nueva("perm-trans-" + nombre_exc + "-")
    base.registrar_familiar("111", nombre="Marta")
    base.insertar_alerta("000000-pt-0001", "gas",
                         '{"id":"000000-pt-0001","gas_adc":2100}')
    trab = cola_modulo.TrabajadorTelegram(base, RemPerm(exc))
    trab._intentar_uno()
    fila = base.obtener_envio("000000-pt-0001", "111")
    prueba("%s se reintenta (no permanente)" % nombre_exc,
           fila["proximo_intento_en"] != db_modulo.ENVIO_FALLIDO_PERMANENTE,
           fila["proximo_intento_en"])
    base.cerrar()

# 13e. Un familiar con error permanente no frena al otro.
base, _ = base_nueva("perm-dos-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")
base.insertar_alerta("000000-pd-0001", "gas",
                     '{"id":"000000-pd-0001","gas_adc":2100}')

class RemPermDos:
    def __init__(self):
        self.token = "x"
        self.disponible = True
        self.enviados = []
    def enviar(self, texto, teclado=None, chat_id=None):
        if str(chat_id) == "222":
            raise Forbidden("bot was blocked by the user")
        self.enviados.append(str(chat_id))
        return len(texto)
    def editar_mensaje(self, *a, **k):
        return True

trab = cola_modulo.TrabajadorTelegram(base, RemPermDos())
for _ in range(4):
    trab._intentar_uno()
prueba("el familiar sin error permanente recibe el aviso",
       "111" in RemPermDos.enviados if False else
       base.obtener_envio("000000-pd-0001", "111")["enviado_en"] is not None)
prueba("el familiar con error permanente queda fallido",
       base.obtener_envio("000000-pd-0001", "222")["proximo_intento_en"]
       == db_modulo.ENVIO_FALLIDO_PERMANENTE)
prueba("el familiar con error permanente no frena al otro (1 enviado)",
       base.listar_recientes(limite=1)[0]["enviados"] == 1)
base.cerrar()

# 13f. Estado de la alerta con 1 enviado y 1 fallido; "Ya lo atendi" funciona.
base, _ = base_nueva("perm-estado-")
base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Javi")
base.insertar_alerta("000000-pe-0001", "gas",
                     '{"id":"000000-pe-0001","gas_adc":2100}')
trab = cola_modulo.TrabajadorTelegram(base, RemPermDos())
for _ in range(4):
    trab._intentar_uno()
rec = base.listar_recientes(limite=1)[0]
prueba("estado con 1 enviado y 1 fallido es pendiente",
       rec["estado"] == "pendiente", rec["estado"])
prueba("desglose con 1 enviado y 1 fallido es 1 y 1",
       (rec["enviados"], rec["sin_mandar"]) == (1, 1),
       (rec["enviados"], rec["sin_mandar"]))
base.marcar_resuelta("000000-pe-0001", ahora=int(time.time()), quien="Marta")
prueba("Ya lo atendi marca resuelto_en",
       base.conexion.execute(
           "SELECT resuelto_en FROM alertas WHERE id='000000-pe-0001'"
       ).fetchone()[0] is not None)
prueba("Ya lo atendi borra los envios pendientes (incluido el fallido)",
       base.conexion.execute(
           "SELECT COUNT(*) FROM envios WHERE alerta_id='000000-pe-0001' AND enviado_en IS NULL"
       ).fetchone()[0] == 0)
base.cerrar()



# =============================================================================
# 14. Aviso del microfono (tipo aviso en conversacion)
# =============================================================================

titulo("14. Aviso del microfono")

base, _ = base_nueva("aviso-")
base.registrar_familiar("111", nombre="Marta")
id_aviso = base.crear_aviso_conversacion("Marta", "111", "panel",
                                         "Marta activo el microfono de la sala")
prueba("crear_aviso_conversacion crea tipo aviso",
       base.obtener_conversacion(id_aviso)["tipo"] == "aviso")

# El aviso no se dice en la sala ni abre la ventana de respuesta, pero va a Telegram.
import threading as _threading
_parando = _threading.Event()
_publicados = []
_enviados_telegram = []

class RemAviso:
    def __init__(self):
        self.token = "x"
        self.disponible = True
        self.enviados = _enviados_telegram
    def enviar(self, texto, teclado=None, chat_id=None):
        self.enviados.append((str(chat_id), texto))
        return len(texto)
    def editar_mensaje(self, *a, **k):
        return True

_satelites = type("S", (), {"hay_conectado": lambda self: True})()
_hilo = _threading.Thread(
    target=orquestador_modulo.tarea_conversacion,
    args=(base, lambda t, c, qos=1, retain=False: _publicados.append((t, c)),
          RemAviso(), _satelites, _parando),
    daemon=True)
_hilo.start()
time.sleep(1.5)
_parando.set()
_hilo.join(timeout=5)

prueba("el aviso NO se publica en satelite/decir",
       not any(t == config.TOPICO_SATELITE_DECIR for t, _ in _publicados),
       [t for t, _ in _publicados])
prueba("el aviso NO abre la ventana de respuesta",
       not voz.ventana_respuesta_abierta())
prueba("el aviso va a Telegram",
       len(_enviados_telegram) >= 1, _enviados_telegram)
base.cerrar()


print("\n" + "=" * 66)
print("comprobaciones: %d" % COMPROBACIONES[0])
print("fallos: %d" % len(FALLOS))
for nombre in FALLOS:
    print("  - " + nombre)
sys.exit(1 if FALLOS else 0)
