# Pruebas de la API del panel: las rutas, el WebSocket y la autorización.
#
# Se ejecuta dentro de la imagen del panel, con las dependencias de prueba instaladas:
#
#     cd servidor
#     docker run --rm -v "$PWD:/app" -w /app asistente-panel:1.0 \
#         sh -c "pip install -q -r panel/requerimientos-pruebas.txt && \
#                python panel/pruebas_api.py"
#
# Lo que aquí se prueba y lo que no:
#
#   - la autorización de cada ruta: sin sesión, con sesión, con sesión caducada, con
#     sesión revocada y con un familiar dado de baja;
#   - que `/api/salud` y `/api/acceso` son las únicas rutas abiertas, y que
#     `/api/salud` no devuelve nada de la casa;
#   - que `POST /api/luz` responde 202 y publica lo que dice, y que un `estado` que no
#     es `on`/`off` es un 422 y no llega al broker;
#   - que resolver una alerta dos veces avisa al satélite UNA vez;
#   - que el WebSocket manda la instantánea inicial, los eventos y el latido, y cierra
#     con 1008 sin sesión;
#   - que la respuesta de la página del enlace antiguo es solo un aviso (sin
#     JavaScript, sin POST y sin inyectar el código de la URL);
#
# No se prueba aquí:
#   - la conexión MQTT real. El cliente se sustituye por un doble que anota lo
#     publicado. La conexión de verdad se mira en los logs del contenedor al arrancar,
#     y probarla aquí montaría un Mosquitto en el test, que es otra cosa.
#   - el WebSocket con saturación real de la cola: se marca `saturado` a mano en el
#     oyente. Probarlo de verdad son 64 eventos y un temporizador, y lo que importa es
#     que el cierre 1013 se mande cuando la marca está puesta, que es lo que se
#     comprueba.

import asyncio
import json
import os
import re
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
RAIZ_ORQUESTADOR = os.path.join(os.path.dirname(RAIZ), "orquestador")
sys.path.insert(0, RAIZ_ORQUESTADOR)
sys.path.insert(0, RAIZ)

datos = tempfile.mkdtemp(prefix="pruebas-api-")
os.environ["ORQUESTADOR_DB"] = os.path.join(datos, "pruebas.sqlite3")
os.environ.setdefault("MQTT_PANEL_PASSWORD", "CONTRASENA-DE-PRUEBAS-NO-REAL")
os.environ.setdefault("PANEL_URL", "http://villaca.tail722206.ts.net:8080")
os.environ["PANEL_RAIZ_WEB"] = os.path.join(datos, "web")
# El `TestClient` arranca el lifespan, y sin esta puerta intentaría conectar con un
# broker de verdad. Está en la docstring de `api.ciclo`.
os.environ["PANEL_SIN_MQTT_EN_PRUEBAS"] = "1"
# Un `index.html` de mentira: lo que se prueba aquí es la API, no el build de Flutter.
os.makedirs(os.environ["PANEL_RAIZ_WEB"], exist_ok=True)
with open(os.path.join(os.environ["PANEL_RAIZ_WEB"], "index.html"), "w") as _f:
    _f.write("<!DOCTYPE html><html lang='es'><body>panel</body></html>")

from fastapi.testclient import TestClient  # noqa: E402

import alertas as alertas_modulo  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import estado as estado_modulo  # noqa: E402
import panel_acceso  # noqa: E402
import api as api_modulo  # noqa: E402

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


class PublicadorFalso:
    """Sustituye a `ClienteEstado` y anota lo publicado.

    `publicar` es el nombre que usa la API real (`mqtt.publicar`), y `conectado` es el
    atributo que mira antes de publicar. Los dos nombres están porque son los que usa el
    código: un doble con otros nombres probaría que el doble funciona, no que la API
    funciona.
    """

    conectado = True

    def __init__(self):
        self.publicado = []

    def publicar(self, topico, carga, qos=1, retain=False):
        self.publicado.append((topico, carga, qos, retain))

    def publicar_luz(self, estado, origen="panel", ts=None):
        """La orden de luz, como la construye `ClienteEstado`.

        Se replica aquí a propósito en vez de llamar al real: el real serializa con
        `json.dumps` y espera a que paho confirme la publicación, y en una prueba sin
        broker eso se queda esperando. Lo que se comprueba es que la API llame a este
        método con el estado que le dieron y no algo más.

        El `ts` se copia tal cual, y si no viene se pone el de ahora, igual que el real.
        """
        self.publicar("casa/sala/luz/set",
                      {"estado": estado, "origen": origen,
                       "ts": int(time.time()) if ts is None else int(ts)},
                      qos=1)

    def desconectar(self):
        """Lo que el `finally` del lifespan llama al cerrar. No hay nada que cerrar."""
        self.desconectado = True


base = db.BaseDatos(os.environ["ORQUESTADOR_DB"])
publicador = PublicadorFalso()
api_modulo.publicador_de_pruebas = publicador

cliente = TestClient(api_modulo.app)

# El lifespan se arranca a mano con `__enter__`, y no con un `with`, porque sus pruebas
# son de nivel superior y-meter todo el fichero en un `with` lo haria ilegible.
#
# Hace falta porque `estados.scrupular()` lo llama el lifespan: sin él, `estados.bucle`
# apuntaría a un bucle que nadie ejecuta, y entonces `emitir` encolaría los eventos con
# `call_soon_threadsafe` y nadie los sacaría nunca. En esas condiciones la primera
# prueba de WebSocket se queda esperando un `estado` que no va a llegar, y parece un
# fallo de la API cuando lo que falta es el bucle.
cliente.__enter__()

# Lo que el lifespan no pone, porque es de las pruebas: la puerta
# PANEL_SIN_MQTT_EN_PRUEBAS lo deja sin cliente MQTT, y aquí se le da el doble. La base
# también se vuelve a poner, pero no por gusto: el lifespan ha abierto la suya sobre el
# mismo fichero, y con dos conexiones SQLite al WAL las escrituras de una no las ve la
# otra hasta que la primera confirma. Con una sola conexión no hay sorpresas.
api_modulo.base = base
api_modulo.mqtt = publicador

base.registrar_familiar("111", nombre="Marta")
base.registrar_familiar("222", nombre="Luis")


def entrar(chat_id):
    """El camino completo de entrada: `/panel` genera el código y la API lo canjea."""
    codigo = panel_acceso.crear_codigo_acceso(base, chat_id)
    r = cliente.post("/api/acceso", json={"codigo": codigo})
    return r.json()["token"]


token_marta = entrar("111")


def cab(token):
    return {"Authorization": "Bearer " + token}


# =============================================================================
# 1. La autorización
# =============================================================================

titulo("1. La puerta")

r = cliente.get("/api/estado")
prueba("sin cabecera, /api/estado es 401", r.status_code == 401, r.status_code)
prueba("y el 401 no explica nada de la casa",
       "chat" not in r.text.lower() and "marta" not in r.text.lower(), r.text[:120])

r = cliente.get("/api/estado", headers={"Authorization": "bearer " + token_marta})
prueba("con `Bearer` en minúsculas también vale (no importa la capitalización)",
       r.status_code == 200, r.status_code)

r = cliente.get("/api/estado", headers={"Authorization": "BEARER " + token_marta})
prueba("ni en mayúsculas", r.status_code == 200, r.status_code)

r = cliente.get("/api/estado", headers={"Authorization": "token " + token_marta})
prueba("pero otra palabra que no sea `Bearer` es 401",
       r.status_code == 401, r.status_code)

r = cliente.get("/api/estado", headers=cab("este-token-no-existe"))
prueba("con un token que no existe es 401", r.status_code == 401, r.status_code)

r = cliente.get("/api/estado", headers={"Authorization": token_marta})
prueba("sin la palabra `Bearer` es 401", r.status_code == 401, r.status_code)

r = cliente.get("/api/estado", headers=cab(token_marta))
prueba("con una sesión válida es 200", r.status_code == 200, r.status_code)

# Todas las rutas de casa exigen sesión. Se comprueba una por una y no «todas», porque
# una lista de rutas que se comprueba con un bucle puede dejar pasar la que se añadió
# ayer sin acordarse de añadirla al bucle.
for metodo, ruta in (("get", "/api/yo"), ("get", "/api/estado"),
                     ("get", "/api/alertas"), ("post", "/api/salir"),
                     ("get", "/api/recordatorios"), ("post", "/api/recordatorios"),
                     ("post", "/api/recordatorios/rec-x/cancelar"),
                     ("post", "/api/inactividad"),
                     ("post", "/api/luz"), ("post", "/api/acceso-inexistente")):
    respuesta = getattr(cliente, metodo)(ruta)
    if ruta == "/api/acceso-inexistente":
        # Una ruta que no existe es 404 aunque no haya sesión: el orden en que se
        # comprueba no debe revelar la existencia de una ruta.
        continue
    prueba("%s %s sin sesión es 401" % (metodo.upper(), ruta),
           respuesta.status_code == 401, respuesta.status_code)

# `atender` lleva sesión en la comprobación del bucle de arriba, pero se repite aquí sin
# cabecera porque es la única ruta POST con cuerpo además de la luz, y `atender` no
# puede devolver 404 a un anónimo: si lo hiciera, se podría saber qué alertas hay.
r = cliente.post("/api/alertas/000000-b0001-0001/atender")
prueba("atender sin sesión es 401 y no 404 (no revela qué alertas existen)",
       r.status_code == 401, r.status_code)

# Una sesión revocada.
token_temporal = entrar("222")
prueba("el segundo familiar entra con el suyo",
       cliente.get("/api/estado", headers=cab(token_temporal)).status_code == 200)
cliente.post("/api/salir", headers=cab(token_temporal))
prueba("después de /api/salir su token ya no vale",
       cliente.get("/api/estado", headers=cab(token_temporal)).status_code == 401)

# Una sesión caducada. Se caduca en la base directamente porque esperar 30 días no es
# una prueba, es una tardar.
token_caducado = entrar("222")
fila = base.sesion_panel(panel_acceso.hash_de(token_caducado))
base.conexion.execute("UPDATE panel_tokens SET caduca_en = ? WHERE token_hash = ?",
                      (int(time.time()) - 1, fila["token_hash"]))
base.conexion.commit()
prueba("un token caducado es 401 aunque el hash exista",
       cliente.get("/api/estado", headers=cab(token_caducado)).status_code == 401)

# Un familiar dado de baja: sus tokens dejan de valer aunque no se hayan borrado.
token_baja = entrar("222")
base.dar_de_baja("222")
prueba("dar de baja a un familiar cierra sus sesiones",
       cliente.get("/api/estado", headers=cab(token_baja)).status_code == 401)
prueba("y las del otro familiar siguen valiendo",
       cliente.get("/api/estado", headers=cab(token_marta)).status_code == 200)
base.registrar_familiar("222", nombre="Luis")

# Un 401 cuenta en `limite_token`, por la IP real: es la red de seguridad para que una
# lluvia de `Authorization:` inventadas no se convierta en otra cosa. Se baja el límite
# para no escribir 60 líneas, que es lo que importa: que el límite CUENTE.
maximo_token = api_modulo.limite_token.maximo
api_modulo.limite_token.maximo = 2
try:
    for _ in range(2):
        cliente.get("/api/estado", headers=cab("token-inventado"))
    r = cliente.get("/api/estado", headers=cab("token-inventado"))
    prueba("pasado el límite de 401 por IP, la misma IP responde 429, no 401",
           r.status_code == 429, r.status_code)
    prueba("y una sesión buena desde esa misma IP desbloquea el contador",
           cliente.get("/api/estado", headers=cab(token_marta)).status_code == 200)
finally:
    api_modulo.limite_token.maximo = maximo_token


# =============================================================================
# 2. El canje
# =============================================================================

titulo("2. El canje del código")

codigo = panel_acceso.crear_codigo_acceso(base, "111")
r = cliente.post("/api/acceso", json={"codigo": codigo})
prueba("un código bueno devuelve token, nombre y caducidad",
       r.status_code == 200 and r.json()["token"]
       and r.json()["nombre"] == "Marta" and r.json()["caduca_en"] > time.time(),
       r.text[:120])
prueba("y el token devuelto no es el código del enlace",
       r.json()["token"] != codigo)

r = cliente.post("/api/acceso", json={"codigo": codigo})
prueba("el mismo código otra vez es 401", r.status_code == 401, r.status_code)

# El texto del fallo dice lo mismo para los tres casos, para que no se pueda aprender
# por diferencia qué códigos existen.
texto_gastado = cliente.post("/api/acceso", json={"codigo": codigo}).text
texto_fantasma = cliente.post("/api/acceso", json={"codigo": "ZZZZZZ"}).text
texto_caducado = cliente.post("/api/acceso", json={
    "codigo": panel_acceso.crear_codigo_acceso(base, "111",
                                               ahora=int(time.time()) - 3600)
}).text
prueba("código gastado, inexistente y caducado dicen EXACTAMENTE lo mismo",
       texto_gastado == texto_fantasma == texto_caducado,
       (texto_gastado, texto_fantasma, texto_caducado))
prueba("y el texto no dice cuál de los tres fue", "gastado" not in texto_gastado
       and "caducado" not in texto_gastado, texto_gastado)

# El alfabeto: seis caracteres sin 0, 1, O, I ni L, y se teclea como se quiera.
nuevo = panel_acceso.crear_codigo_acceso(base, "111")
prueba("el código nuevo son 6 caracteres del alfabeto de 31",
       len(nuevo) == panel_acceso.LONGITUD_CODIGO_NUEVO
       and all(c in panel_acceso.ALFABETO_CODIGO for c in nuevo), nuevo)
r = cliente.post("/api/acceso", json={"codigo": nuevo.lower() + "  "})
prueba("una minúscula o un espacio de más no es un fallo",
       r.status_code == 200, r.text[:120])
prueba("y el código con 0/1/O/I/L se rechaza sin llegar a canjear",
       cliente.post("/api/acceso", json={"codigo": "01OILU"}).status_code == 401)

# Pedir otro código invalida el anterior: solo uno vivo por familiar.
codigo_viejo = panel_acceso.crear_codigo_acceso(base, "111")
codigo_nuevo = panel_acceso.crear_codigo_acceso(base, "111")
prueba("pedir /panel otra vez invalida el código anterior",
       cliente.post("/api/acceso", json={"codigo": codigo_viejo}).status_code == 401)
prueba("y el último sigue valiendo",
       cliente.post("/api/acceso", json={"codigo": codigo_nuevo}).status_code == 200)

# Un familiar desactivado no puede abrir la puerta con un código viejo.
codigo_baja = panel_acceso.crear_codigo_acceso(base, "111")
base.dar_de_baja("111")
prueba("un código de un familiar desactivado no entra",
       cliente.post("/api/acceso", json={"codigo": codigo_baja}).status_code == 401)
base.registrar_familiar("111", nombre="Marta")
token_marta = entrar("111")

r = cliente.post("/api/acceso", json={"codigo": "x"})
prueba("un cuerpo mal formado es 422 y no 500", r.status_code == 422, r.status_code)
r = cliente.post("/api/acceso", json={"codigo": panel_acceso.nuevo_codigo_acceso(),
                                      "admin": True})
prueba("un campo de más es 422, no se acepta en silencio",
       r.status_code == 422, r.status_code)

# El límite por IP: 5 fallos en 10 minutos. Se baja el límite para no tener que
# escribir 5 líneas, que es lo que importa: que el límite CUENTE. Se empieza de
# cero para que el contador no arrastre los fallos de las pruebas anteriores.
limite_original = api_modulo.limite_acceso.maximo
api_modulo.limite_acceso.maximo = 3
api_modulo.limite_acceso.anotar_exito("testclient")
try:
    for _ in range(3):
        cliente.post("/api/acceso", json={"codigo": "ZZZZZZ"})
    r = cliente.post("/api/acceso", json={"codigo": "ZZZZZZ"})
    prueba("pasado el límite de fallos por IP es 429, no 401", r.status_code == 429,
           r.status_code)
    codigo_bueno = panel_acceso.crear_codigo_acceso(base, "111")
    prueba("y con el límite encima, un código BUENO tampoco entra",
           cliente.post("/api/acceso", json={"codigo": codigo_bueno}).status_code
           == 429)
finally:
    api_modulo.limite_acceso.maximo = limite_original
    api_modulo.limite_acceso.anotar_exito("testclient")

r = cliente.post("/api/acceso", json={"codigo": panel_acceso.crear_codigo_acceso(base, "111")})
prueba("un acierto borra el contador de fallos de esa IP",
       r.status_code == 200, r.status_code)

# El tope global: el que frena a quien reparte el ataque entre muchas IPs. Se
# sube el límite por IP para que el único que puede dispararse aquí sea el global.
limite_ip_original = api_modulo.limite_acceso.maximo
limite_global_original = api_modulo.limite_codigo_global.maximo
api_modulo.limite_acceso.maximo = 100
api_modulo.limite_codigo_global.anotar_exito(api_modulo.CLAVE_GLOBAL)
api_modulo.limite_codigo_global.maximo = 3
try:
    for _ in range(3):
        cliente.post("/api/acceso", json={"codigo": "ZZZZZZ"})
    r = cliente.post("/api/acceso", json={"codigo": "ZZZZZZ"})
    prueba("pasado el tope GLOBAL de fallos es 429, no 401", r.status_code == 429,
           r.status_code)
    codigo_bueno = panel_acceso.crear_codigo_acceso(base, "111")
    prueba("y con el tope global encima, un código BUENO tampoco entra",
           cliente.post("/api/acceso", json={"codigo": codigo_bueno}).status_code
           == 429)
finally:
    api_modulo.limite_acceso.maximo = limite_ip_original
    api_modulo.limite_codigo_global.maximo = limite_global_original
    api_modulo.limite_codigo_global.anotar_exito(api_modulo.CLAVE_GLOBAL)
    api_modulo.limite_acceso.anotar_exito("testclient")


# =============================================================================
# 3. `/api/yo` y `/api/salir`
# =============================================================================

titulo("3. Quien soy y salir")

r = cliente.get("/api/yo", headers=cab(token_marta))
prueba("/api/yo devuelve el nombre y la caducidad",
       r.status_code == 200 and r.json()["nombre"] == "Marta"
       and r.json()["caduca_dias"] == config.DIAS_TOKEN_PANEL, r.text[:120])
prueba("y NO devuelve el chat_id",
       "chat_id" not in r.text and "111" not in r.text, r.text[:200])

token_salir = entrar("111")
r = cliente.post("/api/salir", headers=cab(token_salir))
prueba("/api/salir responde ok", r.status_code == 200 and r.json()["ok"],
       r.text[:80])
prueba("y revoca SOLO esa sesión: la de Marta en el móvil sigue valiendo",
       cliente.get("/api/estado", headers=cab(token_salir)).status_code == 401
       and cliente.get("/api/estado", headers=cab(token_marta)).status_code == 200)
prueba("/api/salir dos veces es 401 la segunda, no 200 idempotente",
       cliente.post("/api/salir", headers=cab(token_salir)).status_code == 401)


# =============================================================================
# 4. `/api/estado`
# =============================================================================

titulo("4. El estado")

r = cliente.get("/api/estado", headers=cab(token_marta))
inst = r.json()
claves = set(inst)
esperadas = {"servidor_hora", "luz", "sensores", "nodo", "satelite", "satelites",
             "sitio", "inactividad", "modo", "alertas", "familiares", "conexiones",
             "gas_on_adc", "gas_off_adc"}
prueba("/api/estado trae los catorce campos de la instantánea (Fase 4: "
       "`inactividad`, Fase 2: `gas_on_adc`)",
       claves == esperadas, claves.symmetric_difference(esperadas))
prueba("el umbral del gas entra como entero del ADC (Fase 2)",
       isinstance(inst["gas_on_adc"], int)
       and 0 < inst["gas_off_adc"] < inst["gas_on_adc"] < 4096,
       (inst["gas_on_adc"], inst["gas_off_adc"]))
prueba("cada bloque de la casa lleva `recibido_en`, aunque sea null",
       all("recibido_en" in inst[c] for c in ("luz", "sensores", "nodo", "satelite",
                                              "sitio")),
       {c: "recibido_en" in inst[c] for c in inst if isinstance(inst[c], dict)})
prueba("los cuatro estados posibles son los del contrato",
       all(inst[c]["estado"] in ("conectado", "desconectado", "no_instalado",
                                 "sin_datos")
           for c in ("luz", "sensores", "nodo", "satelite", "sitio")),
       {c: inst[c]["estado"] for c in ("luz", "sensores", "nodo", "satelite", "sitio")})
prueba("`sitio` dice de dónde sale", inst["sitio"]["fuente"] == "nodo",
       inst["sitio"])
prueba("`familiares` es un número, no una lista",
       isinstance(inst["familiares"], int), type(inst["familiares"]))
prueba("y no hay nombres de familiares en la respuesta",
       "Marta" not in r.text and "Luis" not in r.text, r.text[:200])
prueba("el modo sale de la variable, sin accents ni mayusculas",
       inst["modo"] in ("instalacion", "demostracion"), inst["modo"])

# Inyectar una lectura por la misma vía que el MQTT y comprobar que sale.
api_modulo.estados.recibir(config.TOPICO_LUZ_ESTADO,
                           '{"estado":"on","origen":"manual"}')
inst = cliente.get("/api/estado", headers=cab(token_marta)).json()
prueba("una lectura inyectada sale en la instantánea",
       inst["luz"]["encendida"] is True and inst["luz"]["estado"] == "conectado",
       inst["luz"])

# Los sensores reales pueden venir sin dato (null) y eso tiene que llegar a la
# interfaz como null, no como 0 ni como ausencia del campo: es lo que distingue
# «Sin sensor» de «Sin datos» en la tarjeta de ambiente (2026-10-19).
api_modulo.estados.recibir(
    config.TOPICO_SENSORES,
    '{"temp_c":null,"hum_pct":null,"gas_adc":null,"presencia":false}')
inst = cliente.get("/api/estado", headers=cab(token_marta)).json()
prueba("un latido con temp, hum y gas en null sale por la API como null",
       inst["sensores"]["temperatura_c"] is None
       and inst["sensores"]["humedad_pct"] is None
       and inst["sensores"]["gas_adc"] is None, inst["sensores"])
prueba("y la presencia del mismo latido se refleja",
       inst["sensores"]["movimiento"] is False, inst["sensores"])

# El origen «arranque» del arranque en frío también pasa por la API.
api_modulo.estados.recibir(config.TOPICO_LUZ_ESTADO,
                           '{"estado":"off","origen":"arranque"}')
inst = cliente.get("/api/estado", headers=cab(token_marta)).json()
prueba("una luz con origen «arranque» sale por la API sin perder el origen",
       inst["luz"]["origen"] == "arranque" and inst["luz"]["encendida"] is False,
       inst["luz"])

# El bloque de la inactividad. Sale de SQLite (la tabla `configuracion` que comparte
# con el orquestador), no de MQTT, y por eso lleva el ajuste y su franja.
inact = inst["inactividad"]
prueba("el bloque de inactividad trae el ajuste, su franja y el último movimiento",
       set(inact) == {"ultimo_movimiento", "tiempo_min", "minimo", "maximo"},
       inact)
prueba("sin nada guardado, el ajuste es el de fábrica y el reloj está sin datos",
       inact["tiempo_min"] == config.TIEMPO_INACTIVIDAD_MIN_DEFECTO
       and inact["ultimo_movimiento"] is None, inact)
prueba("y la franja es la del servidor, no un número escrito en la interfaz",
       inact["minimo"] == config.TIEMPO_INACTIVIDAD_MIN_MINIMO
       and inact["maximo"] == config.TIEMPO_INACTIVIDAD_MIN_MAXIMO, inact)
base.fijar_ultimo_movimiento(1_700_000_000)
inst = cliente.get("/api/estado", headers=cab(token_marta)).json()
prueba("un último movimiento guardado por el orquestador sale en el panel",
       inst["inactividad"]["ultimo_movimiento"] == 1_700_000_000,
       inst["inactividad"])


# =============================================================================
# 4b. `/api/inactividad`: el ajuste en caliente
# =============================================================================

titulo("4b. El ajuste de inactividad")

r = cliente.post("/api/inactividad", json={"tiempo_min": 45}, headers=cab(token_marta))
prueba("un ajuste válido es 200 y devuelve los minutos guardados",
       r.status_code == 200 and r.json() == {"tiempo_min": 45}, r.text[:120])
prueba("y queda escrito en la MISMA base que lee el orquestador",
       base.tiempo_inactividad() == 45, base.tiempo_inactividad())
inst = cliente.get("/api/estado", headers=cab(token_marta)).json()
prueba("y la siguiente instantánea ya lo trae",
       inst["inactividad"]["tiempo_min"] == 45, inst["inactividad"])

# Fuera de la franja: lo rechaza el tipo (Pydantic) antes de tocar la base.
for malo in (1, 721, 0, -5):
    r = cliente.post("/api/inactividad", json={"tiempo_min": malo},
                     headers=cab(token_marta))
    prueba("tiempo_min=%d fuera de la franja es 422" % malo, r.status_code == 422,
           r.status_code)
for malo in ("cuarenta", None, 2.5, True):
    r = cliente.post("/api/inactividad", json={"tiempo_min": malo},
                     headers=cab(token_marta))
    prueba("tiempo_min=%r que no es un entero limpio es 422" % (malo,),
           r.status_code == 422, r.status_code)
r = cliente.post("/api/inactividad", json={"tiempo_min": 30, "otra": 1},
                 headers=cab(token_marta))
prueba("un campo de más en el cuerpo del ajuste es 422", r.status_code == 422,
       r.status_code)
prueba("y ninguno de los rechazos ha tocado el valor guardado",
       base.tiempo_inactividad() == 45, base.tiempo_inactividad())

r = cliente.post("/api/inactividad", json={"tiempo_min": 30})
prueba("el ajuste sin sesión es 401 y no cambia nada",
       r.status_code == 401 and base.tiempo_inactividad() == 45, r.status_code)

# Se deja en el de fábrica, que es como tiene que quedar tras desplegar.
cliente.post("/api/inactividad", json={"tiempo_min": 120}, headers=cab(token_marta))
prueba("vuelto al de fábrica tras las pruebas",
       base.tiempo_inactividad() == 120, base.tiempo_inactividad())


# =============================================================================
# 5. `/api/luz`
# =============================================================================

titulo("5. La luz")

r = cliente.post("/api/luz", json={"estado": "on"}, headers=cab(token_marta))
prueba("una orden válida es 202, no 200", r.status_code == 202, r.status_code)
prueba("y devuelve lo publicado, no el estado de la luz",
       r.json()["publicado"]["estado"] == "on"
       and r.json()["publicado"]["origen"] == "panel"
       and "encendida" not in r.json(), r.text[:160])
prueba("y se publica en `luz/set` con QoS 1 y sin retenido",
       publicador.publicado[-1][0] == "casa/sala/luz/set"
       and publicador.publicado[-1][2] == 1
       and publicador.publicado[-1][3] is False, publicador.publicado[-1])
prueba("el mensaje lleva `ts`, que es lo que evita que el ESP32 lo descarte",
       abs(publicador.publicado[-1][1]["ts"] - time.time()) < 60,
       publicador.publicado[-1][1])
# El `ts` de la respuesta y el del mensaje tienen que ser EL MISMO numero. Si cada uno se
# calculara por su cuenta, bastaria con que el reloj.tickara entre medias para que el
# movil viera un ts y el ESP32 recibiera otro. No romperia nada (P42 solo mira
# antiguedad), pero la respuesta diria algo untrue: no se publico exactamente eso.
prueba("y el `ts` de la respuesta es el MISMO que el del mensaje",
       r.json()["publicado"]["ts"] == publicador.publicado[-1][1]["ts"],
       (r.json()["publicado"]["ts"], publicador.publicado[-1][1]["ts"]))

for malo in ("ON", "1", "true", "toggle", "", "on "):
    r = cliente.post("/api/luz", json={"estado": malo}, headers=cab(token_marta))
    prueba("estado=%r es 422 y no llega al broker" % (malo,), r.status_code == 422,
           r.status_code)
publicados_antes = len(publicador.publicado)
cliente.post("/api/luz", json={"estado": "inventado"}, headers=cab(token_marta))
prueba("ningún estado inválido ha publicado nada",
       len(publicador.publicado) == publicados_antes,
       (publicados_antes, len(publicador.publicado)))

r = cliente.post("/api/luz", json={"estado": "on", "origen": "centralita"},
                 headers=cab(token_marta))
prueba("un campo de más en el cuerpo de la luz es 422",
       r.status_code == 422, r.status_code)
publicados = [p for p in publicador.publicado if p[0] == "casa/sala/luz/set"]
prueba("y el último mensaje publicado NO lleva el origen del cliente",
       publicados[-1][1]["origen"] == "panel", publicados[-1][1])

r = cliente.post("/api/luz", json={"estado": "on"})
prueba("la luz sin sesión es 401 y no publica",
       r.status_code == 401
       and len([p for p in publicador.publicado if p[0] == "casa/sala/luz/set"])
       == len(publicados), r.status_code)

# Sin conexión con el broker la orden no se puede mandar, y se dice con un 503 en vez
# de fingir que sí. Un 202 con la orden «aceptada» cuando no se publicó nada sería la
# peor respuesta posible: el familiar pulsaría el interruptor y no pasaría nada.
api_modulo.mqtt.conectado = False
try:
    r = cliente.post("/api/luz", json={"estado": "off"}, headers=cab(token_marta))
    prueba("sin broker, la luz es 503 y no un 202 de mentira",
           r.status_code == 503, r.status_code)
finally:
    api_modulo.mqtt.conectado = True


# =============================================================================
# 6. `/api/alertas` y atender
# =============================================================================

titulo("6. Las alertas")

id_alerta = "000000-b0001-0001"
base.insertar_alerta(id_alerta, "gas", json.dumps({"gas_adc": 2100}), None)
r = cliente.get("/api/alertas", headers=cab(token_marta))
prueba("/api/alertas devuelve la lista y la hora del servidor",
       r.status_code == 200 and isinstance(r.json()["alertas"], list)
       and "servidor_hora" in r.json(), r.text[:120])
alertas_json = r.json()["alertas"]
prueba("la alerta recién insertada está en la lista",
       any(a["id"] == id_alerta for a in alertas_json), [a["id"] for a in alertas_json])
primera = [a for a in alertas_json if a["id"] == id_alerta][0]
prueba("con el estado DERIVADO y la carga ya parseada",
       primera["estado"] == "pendiente"
       and isinstance(primera["carga"], dict)
       and primera["carga"]["gas_adc"] == 2100, primera)
prueba("y con los contadores de envíos",
       "enviados" in primera and "sin_mandar" in primera, primera)

r = cliente.get("/api/alertas?tipo=ayuda", headers=cab(token_marta))
prueba("filtrar por tipo que no hay devuelve lista vacía",
       r.json()["alertas"] == [], r.json())
r = cliente.get("/api/alertas?tipo=gas", headers=cab(token_marta))
prueba("filtrar por el tipo que sí está devuelve la alerta",
       any(a["id"] == id_alerta for a in r.json()["alertas"]), r.json())

# `desde`: lo que hace que el cliente pregunte cada 5 s sin recargar.
r = cliente.get("/api/alertas?desde=%d" % int(time.time()), headers=cab(token_marta))
prueba("`desde` en el futuro devuelve vacío (nada nuevo)",
       r.json()["alertas"] == [], r.json())
r = cliente.get("/api/alertas?desde=0", headers=cab(token_marta))
prueba("`desde=0` devuelve todo", len(r.json()["alertas"]) >= 1, r.json())

for limite in ("0", "101", "-1", "abc"):
    r = cliente.get("/api/alertas?limite=%s" % limite, headers=cab(token_marta))
    prueba("limite=%s es 422" % limite, r.status_code == 422, r.status_code)

# Atender. Lo importante: la SEGUNDA vez avisa al satélite UNA sola vez.
publicador.publicado = []
r = cliente.post("/api/alertas/%s/atender" % id_alerta, headers=cab(token_marta))
prueba("atender una alerta pendiente es 200 con `fresca: true`",
       r.status_code == 200 and r.json()["fresca"] is True, r.text[:160])
prueba("y devuelve la alerta ya resuelta",
       r.json()["estado"] == "resuelta" and r.json()["resuelta_en"], r.text[:200])
dichas = [p for p in publicador.publicado if p[0] == config.TOPICO_SATELITE_DECIR]
prueba("y avisa al satélite por satelite/decir", len(dichas) == 1, dichas)

publicador.publicado = []
r = cliente.post("/api/alertas/%s/atender" % id_alerta, headers=cab(token_marta))
prueba("atender la MISMA alerta otra vez es 200 con `fresca: false`, no 409",
       r.status_code == 200 and r.json()["fresca"] is False, r.text[:160])
prueba("y NO vuelve a avisar al satélite (lo arregla D-1)",
       [p for p in publicador.publicado if p[0] == config.TOPICO_SATELITE_DECIR] == [],
       publicador.publicado)

r = cliente.post("/api/alertas/no-existe/atender", headers=cab(token_marta))
prueba("atender un id que no existe es 404", r.status_code == 404, r.status_code)
r = cliente.post("/api/alertas/; DROP TABLE alertas; /atender",
                 headers=cab(token_marta))
prueba("un id con basura da 404 y la tabla sigue ahí",
       r.status_code == 404
       and len(cliente.get("/api/alertas", headers=cab(token_marta)).json()["alertas"])
       >= 1, r.status_code)


# =============================================================================
# 6c. Los familiares y el detalle de una alerta (Fase 2, decisiones A.5 y B.3)
# =============================================================================

titulo("6c. Los familiares y el detalle de la alerta")

CHATS_DE_PRUEBA = ("111", "222", "333")


def sin_chat_ids(datos) -> bool:
    """¿Ningún `chat_id` de los de las pruebas aparece en la respuesta?

    Se busca por ESTRUCTURA y no en el texto: los `epoch` de las horas son
    números con 10 cifras y un `r.text` que contuviera «111» sería un acierto
    falso a mitad de una fecha.
    """
    if isinstance(datos, dict):
        return all("chat_id" not in str(clave) and sin_chat_ids(valor)
                   for clave, valor in datos.items())
    if isinstance(datos, list):
        return all(sin_chat_ids(item) for item in datos)
    if isinstance(datos, str):
        return datos not in CHATS_DE_PRUEBA
    return True


# --- `GET /api/familiares`: solo activos, solo nombres, sin chat_id -----------
base.registrar_familiar("333", nombre="Baja")
base.marcar_familiar_inactivo("333")

r = cliente.get("/api/familiares")
prueba("GET /api/familiares sin token es 401",
       r.status_code == 401, r.status_code)
r = cliente.get("/api/familiares", headers=cab(token_marta))
nombres = sorted(f["nombre"] for f in r.json().get("familiares", []))
prueba("y con token devuelve SOLO los familiares activos, con sus nombres",
       r.status_code == 200 and nombres == ["Luis", "Marta"]
       and r.json()["total"] == 2, r.json())
prueba("sin ningún chat_id ni familiar dado de baja en la respuesta",
       sin_chat_ids(r.json()) and "Baja" not in r.text, r.json())

# --- `GET /api/alertas/{id}`: los eventos REALES, sin chat_id -----------------
id_detalle = "000000-b0002-0001"
base.insertar_alerta(id_detalle, "gas", json.dumps({"gas_adc": 2500}),
                     chat_ids=["111", "222"])

r = cliente.get("/api/alertas/%s" % id_detalle)
prueba("el detalle de una alerta sin token es 401",
       r.status_code == 401, r.status_code)
r = cliente.get("/api/alertas/%s" % id_detalle, headers=cab(token_marta))
detalle = r.json()
eventos = detalle.get("eventos", [])
prueba("el detalle trae la alerta y su línea de tiempo",
       r.status_code == 200 and detalle["alerta"]["id"] == id_detalle
       and isinstance(eventos, list), r.text[:200])
prueba("con la hora de creación y un evento por aviso PENDIENTE (hechos reales)",
       eventos[0] == {"tipo": "creada", "en": detalle["alerta"]["creado_en"]}
       and sorted(e["nombre"] for e in eventos[1:]) == ["Luis", "Marta"]
       and all(e["tipo"] == "pendiente" for e in eventos[1:]), eventos)
prueba("y sin ningún chat_id en toda la respuesta",
       sin_chat_ids(detalle), detalle)

# Un aviso que YA salió es «enviada», con su hora real.
base.marcar_enviado(id_detalle, "111")
r = cliente.get("/api/alertas/%s" % id_detalle, headers=cab(token_marta))
eventos = r.json()["eventos"]
# Se busca por tipo y no por posición: la «creada» y la «pendiente» llevan la
# MISMA hora (la del reparto), y con horas iguales el orden depende de cómo
# caigan los segundos, que es una prueba que pasaría un lunes y fallaría otro.
por_tipo = {e["tipo"]: e for e in eventos}
prueba("un envío con `enviado_en` sale como evento «enviada» con su hora",
       set(por_tipo) == {"creada", "enviada", "pendiente"}
       and por_tipo["enviada"]["nombre"] == "Marta"
       and por_tipo["pendiente"]["nombre"] == "Luis"
       and isinstance(por_tipo["enviada"]["en"], int)
       and por_tipo["enviada"]["en"] >= por_tipo["creada"]["en"], eventos)

# Atender desde el panel: queda «Atendida por Marta», con el NOMBRE de la sesión.
r = cliente.post("/api/alertas/%s/atender" % id_detalle, headers=cab(token_marta))
prueba("al atender, la respuesta lleva el NOMBRE en `resuelto_por`",
       r.status_code == 200 and r.json()["resuelto_por"] == "Marta", r.json())
r = cliente.get("/api/alertas/%s" % id_detalle, headers=cab(token_marta))
detalle = r.json()
eventos = detalle["eventos"]
por_tipo = {e["tipo"]: e for e in eventos}
prueba("y el detalle lo enseña como «atendida» con ese nombre",
       set(por_tipo) == {"creada", "enviada", "atendida"}
       and por_tipo["atendida"]["nombre"] == "Marta", eventos)
prueba("el aviso que estaba pendiente se canceló, así que ya no es un evento",
       all(e.get("nombre") != "Luis" for e in eventos), eventos)
prueba("y sigue sin haber ningún chat_id en la respuesta",
       sin_chat_ids(detalle), detalle)

r = cliente.get("/api/alertas/no-existe")
prueba("el detalle de una alerta que no existe es 401 sin token",
       r.status_code == 401, r.status_code)
r = cliente.get("/api/alertas/no-existe", headers=cab(token_marta))
prueba("y 404 con token si la alerta no existe",
       r.status_code == 404, r.status_code)
r = cliente.get("/api/alertas/; DROP TABLE alertas; /", headers=cab(token_marta))
prueba("un id con basura da 404 y la tabla sigue ahí",
       r.status_code == 404
       and len(cliente.get("/api/alertas", headers=cab(token_marta))
               .json()["alertas"]) >= 1, r.status_code)

# (El familiar «Baja» se queda inactivo a propósito: así lo que sigue en este
# fichero ve exactamente los mismos dos activos de antes de esta sección.)


# =============================================================================
# 6b. Los recordatorios
# =============================================================================

titulo("6b. Los recordatorios")

r = cliente.get("/api/recordatorios", headers=cab(token_marta))
prueba("sin recordatorios, la lista va vacía y con la hora del servidor",
       r.status_code == 200 and r.json()["recordatorios"] == []
       and "servidor_hora" in r.json(), r.text[:120])

# Un `diario`. La hora se elige en el pasado del día para que la próxima vez sea
# mañana y el `proximo_en` quede claro; da igual la zona, lo que importa es que sea
# POSTERIOR a ahora.
r = cliente.post("/api/recordatorios", headers=cab(token_marta),
                 json={"texto": "Tomar la pastilla", "hora": "08:00",
                       "repeticion": "diario"})
prueba("crear uno de todos los días es 201", r.status_code == 201, r.text[:200])
creado = r.json()
rec_diario = creado["id"]
prueba("el id lo pone el servidor y es un `rec-...`",
       isinstance(creado.get("id"), str)
       and re.match(r"^rec-[a-f0-9]{16}$", creado["id"]) is not None,
       creado.get("id"))
prueba("devuelve la hora tal cual, la repetición y la próxima vez en el futuro",
       r.json()["hora"] == "08:00" and r.json()["repeticion"] == "diario"
       and r.json()["proximo_en"] > int(time.time()), r.json())
prueba("y sale activo, sin última entrega todavía",
       r.json()["activo"] is True and r.json()["ultima_entrega"] is None, r.json())

r = cliente.get("/api/recordatorios", headers=cab(token_marta))
prueba("el listado ya trae el recordatorio creado",
       [x["id"] for x in r.json()["recordatorios"]] == [rec_diario], r.json())

# `creado_por` es el nombre del familiar, nunca el `chat_id`.
prueba("el recordatorio dice quién lo creó por su nombre y no por su chat_id",
       r.json()["recordatorios"][0]["creado_por"] == "Marta"
       and "111" not in json.dumps(r.json()), r.json()["recordatorios"][0])

# Semanal: se guardan los días ordenados y sin repetir.
r = cliente.post("/api/recordatorios", headers=cab(token_marta),
                 json={"texto": "Sacar la basura", "hora": "20:15",
                       "repeticion": "semanal", "dias": [5, 1, 5]})
prueba("un semanal con días repetidos los guarda ordenados y sin repetir",
       r.status_code == 201 and r.json()["dias"] == [1, 5], r.json())
rec_semanal = r.json()["id"]

# Una sola vez, en el futuro.
fecha_futura = time.strftime("%Y-%m-%d", time.localtime(time.time() + 14 * 86400))
r = cliente.post("/api/recordatorios", headers=cab(token_marta),
                 json={"texto": "Cita del médico", "hora": "10:45",
                       "repeticion": "una_vez", "fecha": fecha_futura})
prueba("una sola vez en el futuro es 201 y guarda la fecha",
       r.status_code == 201 and r.json()["fecha"] == fecha_futura, r.json())

# Ahora los descartes. Todos son 422 y con mensaje; la lista no cambia.
antes = len(cliente.get("/api/recordatorios", headers=cab(token_marta)).json()["recordatorios"])

fecha_pasada = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
descartes = [
    ({"texto": "", "hora": "08:00", "repeticion": "diario"}, "texto vacío"),
    ({"texto": "Con emoji 😀", "hora": "08:00", "repeticion": "diario"}, "emoji"),
    ({"texto": "Con $ signo", "hora": "08:00", "repeticion": "diario"}, "$"),
    ({"texto": "Con < etiqueta", "hora": "08:00", "repeticion": "diario"}, "<"),
    ({"texto": "Hora 25", "hora": "25:00", "repeticion": "diario"}, "hora 25:00"),
    ({"texto": "Hora 08:60", "hora": "08:60", "repeticion": "diario"}, "hora 08:60"),
    ({"texto": "Sin repeticion", "hora": "08:00", "repeticion": "cada_rato"},
     "repetición desconocida"),
    ({"texto": "Una vez sin fecha", "hora": "08:00", "repeticion": "una_vez"},
     "una_vez sin fecha"),
    ({"texto": "Una vez pasada", "hora": "00:01", "repeticion": "una_vez",
      "fecha": fecha_pasada}, "una_vez en el pasado"),
    ({"texto": "Fecha imposible", "hora": "00:01", "repeticion": "una_vez",
      "fecha": "2026-02-31"}, "31 de febrero"),
    ({"texto": "Diario con días", "hora": "08:00", "repeticion": "diario",
      "dias": [1]}, "diario con días"),
    ({"texto": "Semanal sin días", "hora": "08:00", "repeticion": "semanal"},
     "semanal sin días"),
    ({"texto": "Semanal con día 8", "hora": "08:00", "repeticion": "semanal",
      "dias": [8]}, "día 8"),
    ({"texto": "Un campo de más", "hora": "08:00", "repeticion": "diario",
      "color": "rojo"}, "campo de más"),
]
for cuerpo, caso in descartes:
    r = cliente.post("/api/recordatorios", headers=cab(token_marta), json=cuerpo)
    prueba("rechaza %s con 422" % caso, r.status_code == 422,
           "%s -> %s" % (r.status_code, r.text[:100]))

despues = len(cliente.get("/api/recordatorios", headers=cab(token_marta)).json()["recordatorios"])
prueba("ningún descarte creó un recordatorio", antes == despues, (antes, despues))

# Cancelar es idempotente.
r = cliente.post("/api/recordatorios/%s/cancelar" % rec_semanal, headers=cab(token_marta))
prueba("cancelar un recordatorio es 200 con `fresca: true`",
       r.status_code == 200 and r.json() == {"ok": True, "fresca": True}, r.json())
r = cliente.post("/api/recordatorios/%s/cancelar" % rec_semanal, headers=cab(token_marta))
prueba("cancelarlo otra vez es 200 con `fresca: false` y no 409",
       r.status_code == 200 and r.json() == {"ok": True, "fresca": False}, r.json())
r = cliente.get("/api/recordatorios", headers=cab(token_marta))
prueba("y el cancelado ya no sale en el listado",
       rec_semanal not in [x["id"] for x in r.json()["recordatorios"]], r.json())
r = cliente.post("/api/recordatorios/no-existe/cancelar", headers=cab(token_marta))
prueba("cancelar un id que no existe es 404", r.status_code == 404, r.status_code)
r = cliente.post("/api/recordatorios/; DROP TABLE recordatorios; /cancelar",
                 headers=cab(token_marta))
prueba("un id con basura da 404 y la tabla sigue",
       r.status_code == 404
       and len(cliente.get("/api/recordatorios", headers=cab(token_marta)).json()
               ["recordatorios"]) >= 1, r.status_code)


# =============================================================================
# 7. La página del enlace antiguo
# =============================================================================

titulo("7. La página del enlace antiguo")

r = cliente.get("/acceso/abcdefghijklmnop")
prueba("/acceso/<codigo> devuelve HTML", r.status_code == 200
       and "text/html" in r.headers["content-type"], r.status_code)
prueba("la página lleva `Referrer-Policy: no-referrer`",
       r.headers.get("Referrer-Policy") == "no-referrer", dict(r.headers))
prueba("y `X-Robots-Tag: noindex`, para que un buscador no lo indexe",
       "noindex" in r.headers.get("X-Robots-Tag", ""), dict(r.headers))
# El enlace ya no canjea nada: la página es solo un aviso que apunta a `/panel`.
prueba("la página ya NO canjea nada: no hay ni POST ni /api/acceso",
       "/api/acceso" not in r.text and "POST" not in r.text, r.text[:400])
prueba("ni JavaScript", "<script" not in r.text.lower(), r.text[:400])
prueba("el aviso dice que el enlace ya no funciona",
       "ya no funciona" in r.text, r.text[:400])
prueba("y por dónde se pide el código nuevo: /panel",
       "/panel" in r.text, r.text[:400])
prueba("y lleva un enlace a la raíz para entrar con el código",
       'href="/"' in r.text, r.text[:400])

# Un código con caracteres de HTML no se interpola en la página: venga lo que venga,
# la respuesta es la misma y fija, así que no hay nada que inyectar.
r_script = cliente.get("/acceso/%3Cscript%3Ealert(1)")
prueba("un código con `<script>` no se inyecta tal cual en la página",
       "<script>alert(1)" not in r_script.text, r_script.text[-400:])
prueba("y la respuesta es la MISMA que con un código normal",
       r_script.text == r.text, None)
prueba("con una barra en el código tampoco hay página (404, no una inyección)",
       cliente.get("/acceso/%3C%2Fscript%3E").status_code == 404,
       cliente.get("/acceso/%3C%2Fscript%3E").status_code)

r = cliente.get("/acceso/")
prueba("/acceso/ sin código es 404", r.status_code == 404, r.status_code)

# La página no lleva token, así que `no-store` no hace falta por el token, pero un
# aviso de una puerta vieja no debe quedar en un caché de alguien.
r = cliente.get("/acceso/abcdefghijklmnop")
prueba("la página del aviso tampoco se cachea",
       "no-store" in r.headers.get("Cache-Control", ""), dict(r.headers))


# =============================================================================
# 8. Las cabeceras de todas las respuestas
# =============================================================================

titulo("8. Las cabeceras")

r = cliente.get("/api/salud")
prueba("/api/salud va SIN sesión (es el healthcheck de Docker)",
       r.status_code == 200, r.status_code)
salud = r.json()
prueba("y desde un peer que NO es de bucle devuelve solo ok y hora",
       set(salud) == {"ok", "hora"}, sorted(salud))
prueba("pero NO devuelve nada de la casa",
       "luz" not in salud and "alertas" not in salud and "chat" not in r.text.lower(),
       sorted(salud))
# Los `chat_id` de esta base de pruebas son "111" y "222". Con esos números sueltos en
# el JSON haría falta mirar campo por campo, y un "111" aparece antes dentro de la hora
# que en un chat_id real.
prueba("y ningún familiar aparece por su id",
       all(str(v) not in ("111", "222") for v in salud.values()),
       salud)

# El detalle (url, esquema, modo, broker) solo lo ve quien llama desde el bucle SIN
# `X-Forwarded-For`, que es el healthcheck de Docker. Se comprueba con un segundo
# `TestClient` con la IP del cliente puesta a una de bucle: `/api/salud` no necesita el
# lifespan, y este cliente no entra en él a propósito.
loopback = TestClient(api_modulo.app, client=("127.0.0.1", 50000))
detalle = loopback.get("/api/salud").json()
prueba("desde bucle y sin X-Forwarded-For sí hay el detalle del healthcheck",
       set(detalle) == {"ok", "hora", "panel_url", "esquema", "modo", "mqtt"},
       sorted(detalle))
escondido = loopback.get("/api/salud",
                         headers={"X-Forwarded-For": "203.0.113.9"}).json()
prueba("pero desde bucle CON X-Forwarded-For también se esconde: detrás de Funnel "
       "el proxy pone SIEMPRE esa cabecera y el healthcheck de Docker no la lleva",
       set(escondido) == {"ok", "hora"}, sorted(escondido))

# El límite por IP de los 401 y el log usan la IP REAL del cliente, que con Funnel
# llega en `X-Forwarded-For` — y por eso la cabecera solo se confía desde el bucle.
prueba("ip_real confía en X-Forwarded-For solo desde bucle",
       api_modulo.ip_real("127.0.0.1", {"x-forwarded-for": "203.0.113.9"})
       == "203.0.113.9", "ip=%s" % api_modulo.ip_real(
           "127.0.0.1", {"x-forwarded-for": "203.0.113.9"}))
prueba("y toma el primer salto de la lista (el proxy no encola saltos)",
       api_modulo.ip_real("::1", {"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
       == "203.0.113.9", "ip=%s" % api_modulo.ip_real(
           "::1", {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}))
prueba("pero NO confía si el peer no es de bucle (la LAN llega directa)",
       api_modulo.ip_real("10.0.0.5", {"x-forwarded-for": "203.0.113.9"})
       == "10.0.0.5", "ip=%s" % api_modulo.ip_real(
           "10.0.0.5", {"x-forwarded-for": "203.0.113.9"}))
prueba("y sin cabecera no confía en nadie",
       api_modulo.ip_real("127.0.0.1", {}) == "127.0.0.1",
       "ip=%s" % api_modulo.ip_real("127.0.0.1", {}))

r = cliente.get("/api/estado", headers=cab(token_marta))
prueba("la API lleva Cache-Control: no-store (un móvil no debe servir una "
       "instantánea vieja)",
       "no-store" in r.headers.get("Cache-Control", ""), dict(r.headers))
prueba("y X-Content-Type-Options: nosniff",
       r.headers.get("X-Content-Type-Options") == "nosniff", dict(r.headers))
prueba("y X-Frame-Options: DENY (nadie puede meter el panel en un iframe)",
       r.headers.get("X-Frame-Options") == "DENY", dict(r.headers))

r = cliente.get("/")
prueba("el index del build se sirve en la raíz", r.status_code == 200
       and "panel" in r.text, r.status_code)
prueba("y también lleva no-store, para que un cambio se vea al recargar",
       "no-store" in r.headers.get("Cache-Control", ""), dict(r.headers))


# =============================================================================
# 9. El WebSocket
# =============================================================================

titulo("9. El WebSocket")


def ws(fn):
    """Ejecuta una prueba de WebSocket y devuelve lo que ha dejado en `RESULTADO`.

    Estas pruebas NO hacen falta en asyncio. El `websocket_connect` del `TestClient` es
    síncrono: por dentro levanta su propio hilo con un bucle propio, y por eso puede
    usarse desde una función normal. Escribirlas con `async def` habría obligado a
    envolver cada una en `asyncio.run`, y el `with` anidado de `receive_json` ya
    complica bastante la lectura.

    El paso atrás es el resultado y no un `return`: el valor tiene que sobrevivir a la
    salida del `with` de `websocket_connect`, donde la conexión ya está cerrada.
    """
    global RESULTADO
    RESULTADO = None
    fn()
    return RESULTADO


def test_ws_inicial():
    global RESULTADO
    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        RESULTADO = w.receive_json()


primero = ws(test_ws_inicial)
prueba("al conectar se manda la instantánea completa primero",
       primero["tipo"] == "estado" and "luz" in primero["datos"], primero.get("tipo"))
prueba("y tiene los mismos campos que /api/estado",
       set(primero["datos"]) == set(inst),
       sorted(set(primero["datos"]) ^ set(inst)))


def test_ws_evento():
    global RESULTADO
    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        w.receive_json()
        # Se inyecta por la misma vía que el MQTT y se mira que salga por el WS.
        api_modulo.estados.recibir(config.TOPICO_LUZ_ESTADO,
                                   '{"estado":"off","origen":"panel"}')
        RESULTADO = w.receive_json()


evento = ws(test_ws_evento)
prueba("un cambio de la luz llega como evento `luz`",
       evento["tipo"] == "luz" and evento["datos"]["encendida"] is False, evento)


def test_ws_evento_desde_otro_hilo():
    """El evento tiene que llegar aunque lo inyecte el hilo de MQTT, no el del bucle.

    Es el caso real: los callbacks de paho vienen en su propio hilo, y `emitir` los
    pasa al bucle con `call_soon_threadsafe`. Si aquí se inyectara desde el mismo
    hilo, la prueba pasaría aunque `call_soon_threadsafe` no estuviera, que es
    exactamente el error que hace que el panel deje de actualizarse solo y no se entere
    nadie hasta que alguien recargue.
    """
    global RESULTADO
    import threading

    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        w.receive_json()
        hilo = threading.Thread(
            target=lambda: api_modulo.estados.recibir(
                config.TOPICO_LUZ_ESTADO, '{"estado":"on","origen":"otro-hilo"}'))
        hilo.start()
        hilo.join()
        RESULTADO = w.receive_json()


evento_hilo = ws(test_ws_evento_desde_otro_hilo)
prueba("un evento inyectado desde el hilo de MQTT llega igualmente",
       evento_hilo["tipo"] == "luz"
       and evento_hilo["datos"]["origen"] == "otro-hilo", evento_hilo)


def test_ws_latido():
    global RESULTADO
    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        w.receive_json()
        # Se espera el latido de verdad, con margen para una máquina lenta. Es el único
        # punto de estas pruebas que consume tiempo, y se hace porque un latido que
        # sale con el `ts` equivocado, o que no sale nunca, no se vería de otra forma.
        RESULTADO = w.receive_json()


latido = ws(test_ws_latido)
prueba("el servidor manda `latido` con su ts",
       latido["tipo"] == "latido" and latido["datos"]["ts"] > 0, latido)
prueba("y el `latido` no lleva la instantánea dentro (solo su ts)",
       set(latido["datos"]) == {"ts"}, latido)


def test_ws_pong_mal_formado():
    global RESULTADO
    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        w.receive_json()
        # Cosas que no son un `pong` válido: otro tipo, un JSON roto, y texto plano. Ninguna
        # debe tumbar la conexión, porque lo que llegue del cliente es del cliente.
        w.send_text(json.dumps({"tipo": "no-es-pong"}))
        w.send_text("{esto no es json")
        w.send_text("hola")
        w.send_text(json.dumps({"tipo": "pong"}))
        api_modulo.estados.recibir(config.TOPICO_LUZ_ESTADO,
                                   '{"estado":"on","origen":"panel"}')
        RESULTADO = w.receive_json()


tras_pong = ws(test_ws_pong_mal_formado)
prueba("un mensaje mal formado no rompe el WebSocket",
       tras_pong["tipo"] in ("luz", "latido"), tras_pong)


def test_ws_sin_sesion():
    global RESULTADO
    from starlette.websockets import WebSocketDisconnect

    try:
        with cliente.websocket_connect("/api/vivo?token=inventado") as w:
            w.receive_json()
            RESULTADO = ("abierto", None)
    except WebSocketDisconnect as cierre:
        RESULTADO = ("cerrado", cierre.code)


estado_cierre, codigo_cierre = ws(test_ws_sin_sesion)
prueba("sin sesión válida, el WebSocket no se abre",
       estado_cierre == "cerrado", estado_cierre)
prueba("y cierra con 1008 (policy violation)",
       codigo_cierre == 1008, codigo_cierre)


def test_ws_sin_token():
    global RESULTADO
    from starlette.websockets import WebSocketDisconnect

    try:
        with cliente.websocket_connect("/api/vivo") as w:
            w.receive_json()
            RESULTADO = ("abierto", None)
    except WebSocketDisconnect as cierre:
        RESULTADO = ("cerrado", cierre.code)


estado_cierre, codigo_cierre = ws(test_ws_sin_token)
prueba("sin token en la query, también cierra con 1008",
       estado_cierre == "cerrado" and codigo_cierre == 1008,
       (estado_cierre, codigo_cierre))


def test_ws_1013():
    """El cierre 1013, con la marca puesta a mano.

    Este es el caso facil, y se deja aparte porque el de abajo no lo tapa. Poner la
    marca y mandar un evento es el camino corto: la cola tiene sitio, el evento se saca,
    y `_eventos` ve la marca y cierra.
    """
    global RESULTADO
    from starlette.websockets import WebSocketDisconnect

    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        w.receive_json()
        oyente = api_modulo.estados._oyentes[-1]
        oyente.saturado = True
        api_modulo.estados.recibir(config.TOPICO_LUZ_ESTADO,
                                   '{"estado":"on","origen":"panel"}')
        try:
            while True:
                w.receive_json()
            RESULTADO = ("no-cerrado", None)
        except WebSocketDisconnect as cierre:
            RESULTADO = ("cerrado", cierre.code)


estado_cierre, codigo_cierre = ws(test_ws_1013)
prueba("con la cola marcada como saturada, cierra con 1013",
       estado_cierre == "cerrado" and codigo_cierre == 1013,
       (estado_cierre, codigo_cierre))


# Y que hay una tarea que se encarga de esto desde fuera. Es la parte que no se puede
# probar con el `TestClient` (su búfer no se llena, como esta misma cabecera explica) y
# que en el servidor real es la que cierra: si no estuviera en la lista de tareas, el
# cierre por saturación no llegaría nunca al `asyncio.wait`.
def test_ws_1013_por_el_vigilante():
    """El cierre 1013 con la marca puesta, pero sin pasar por `cola.get()`.

    Es el caso del cliente real atascado: `_eventos` esta bloqueado en `send_text`
    esperando a que el socket drene, asi que no vuelve a la cola a mirar la marca. La
    unica que puede cerrar es `_vigilar_saturacion`, pendiente del `asyncio.Event`.
    """
    global RESULTADO
    from starlette.websockets import WebSocketDisconnect

    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        w.receive_json()
        # Se pone el aviso a mano, que es lo que haria `poner()` al llenarse. Se usa el
        # `Event` y no la marca a proposito: la marca sola no despierta a nadie, y esto
        # es justo lo que distingue a las dos cosas.
        api_modulo.estados._oyentes[-1].saturado_espera.set()
        try:
            w.receive_json()
            RESULTADO = ("no-cerrado", None)
        except WebSocketDisconnect as cierre:
            RESULTADO = ("cerrado", cierre.code)


estado_vigilante, codigo_vigilante = ws(test_ws_1013_por_el_vigilante)
prueba("el cierre por saturación también llega por el vigilante, no solo por la cola",
       (estado_vigilante, codigo_vigilante) == ("cerrado", 1013),
       (estado_vigilante, codigo_vigilante))

# La cola LLENA de verdad, en vez de la marca puesta a mano, NO se prueba aqui y no
# porque no se pueda, sino porque con este cliente no ocurre. `TestClient` de Starlette
# guarda en un bufer sin limite todo lo que el servidor escribe, asi que `send_text` no
# se bloquea nunca: el bucle `_eventos` consume la cola a ritmo de reloj y esta no llega
# a llenarse ni de lejos. Por mucho que se manden `COLA_EVENTOS_WS + 5` eventos, la
# marca `saturado` se queda en `False` y no hay nada que cerrar. Seria una prueba que
# pasaria por el motivo equivocado.
#
# La cola llena de verdad esta en `pruebas_estado.py`, que llena un `Oyente` sin nadie
# leyendo y comprueba las dos cosas que importan: que `poner()` marca la marca y deja
# el testigo que despierta a quien la lee. Aqui, en cambio, esta la otra mitad del
# camino: que una vez marcada, la ruta cierra con 1013 y no con un error.


def test_ws_conexiones():
    global RESULTADO
    antes = api_modulo.estados.conexiones()
    with cliente.websocket_connect("/api/vivo?token=%s" % token_marta) as w:
        w.receive_json()
        dentro = api_modulo.estados.conexiones()
    # Tras el `with` el `finally` de la ruta ya ha dado de baja al oyente, pero se
    # mira de todas formas: si el cierre no lo quitara, `conexiones` subiría para
    # siempre con cada recarga y el panel llegaría a thousands de conexiones fantasma.
    RESULTADO = (antes, dentro, api_modulo.estados.conexiones())


antes, dentro, despues = ws(test_ws_conexiones)
prueba("`conexiones` sube con el WebSocket abierto",
       dentro == antes + 1, (antes, dentro))
prueba("y vuelve a su valor al cerrarse",
       despues == antes, (antes, despues))

# =============================================================================
# 10. El token no sale en el log
# =============================================================================
# `access_log=False` NO basta para el WebSocket: el mensaje de apertura lo escribe
# uvicorn en `websocket.accept` sin mirar ese parámetro, y lleva la query entera, que
# en este panel es donde va el token. Sin el filtro de `_QuitaTokens` la sesión de un
# familiar queda escrita en `docker logs`, que es un archivo que se lee y se copia.

titulo("10. El token no sale en el log")

import io  # noqa: E402
import logging  # noqa: E402


def probar_filtro(mensaje, argumentos=()):
    """Pasa un registro por el filtro y devuelve lo que se escribiría."""
    registro = logging.LogRecord("uvicorn.error", logging.INFO, "f", 1, mensaje,
                                 argumentos, None)
    api_modulo._QuitaTokens().filter(registro)
    return registro.getMessage()


# El formato REAL de uvicorn, con la query del WebSocket y el token dentro.
linea_uvicorn = ('%s - "WebSocket %s" [accepted]',
                 ("127.0.0.1:51748", "/api/vivo?token=abc123SEGURO"))
filtrada = probar_filtro(*linea_uvicorn)
prueba("el mensaje de apertura de uvicorn no deja el token",
       "abc123SEGURO" not in filtrada, filtrada)
prueba("pero sí deja ver que hubo una conexión",
       "WebSocket" in filtrada and "[accepted]" in filtrada, filtrada)
prueba("y el token sale como `[quitado]`, para que se vea que ahí había algo",
       "token=[quitado]" in filtrada, filtrada)

# Y que no rompa los otros mensajes, que son casi todos de la forma `ruta?param=valor`.
prueba("un token en un mensaje suelto también se quita",
       "SEGURO" not in probar_filtro("con token=SEGURO en medio"),
       probar_filtro("con token=SEGURO en medio"))
prueba("una ruta con otros parámetros no se toca",
       probar_filtro("127.0.0.1 - \"GET /api/estado?campo=valor HTTP/1.1\" 200")
       == "127.0.0.1 - \"GET /api/estado?campo=valor HTTP/1.1\" 200",
       probar_filtro("127.0.0.1 - \"GET /api/estado?campo=valor HTTP/1.1\" 200"))
prueba("con dos parámetros, solo se quita el del token",
       probar_filtro("/api/vivo?otro=1&token=SEGURO&mas=2")
       == "/api/vivo?otro=1&token=[quitado]&mas=2",
       probar_filtro("/api/vivo?otro=1&token=SEGURO&mas=2"))

# Y el filtro tiene que estar PUESTO de verdad, no solo funcionar en la unidad. Se
# llama a `configurar_log()` porque es lo que hace `api.py` al arrancar de verdad (solo
# bajo `__main__`, y aquí el módulo se importa), y no tiene sentido comprobar que el
# filtro está en una lista si no se ha llamado nunca a la función que lo pone.
api_modulo.configurar_log()
filtros = logging.getLogger("uvicorn.error").filters
prueba("el filtro está instalado en `uvicorn.error`",
       any(isinstance(f, api_modulo._QuitaTokens) for f in filtros), filtros)
prueba("y NO está en `uvicorn` entero, que perdería los errores de arranque",
       not any(isinstance(f, api_modulo._QuitaTokens)
               for f in logging.getLogger("uvicorn").filters),
       logging.getLogger("uvicorn").filters)

# Y lo que de verdad importa: el token de ESTA sesión no aparece en nada de lo que se ha
# registrado durante todas las pruebas de arriba.
prueba("y el token de las pruebas no aparece en lo registrado",
       token_marta not in (probar_filtro("token=" + token_marta)
                           .replace("[quitado]", "")),
       "el token no debe quedar en ningun mensaje")

# Se cierra el lifespan, que es lo que cierra la base del `finally` de `ciclo`. Sin
# esto el proceso acaba igual (el fichero SQLite es temporal y se borra), pero el
# `TestClient` avisa con un `ResourceWarning` si se le deja el portal abierto.
cliente.__exit__(None, None, None)

print("\n" + "=" * 66)
print("comprobaciones: %d" % COMPROBACIONES[0])
print("fallos: %d" % len(FALLOS))
for nombre in FALLOS:
    print("  - " + nombre)
sys.exit(1 if FALLOS else 0)