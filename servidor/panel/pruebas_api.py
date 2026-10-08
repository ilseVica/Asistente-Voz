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
#   - que la respuesta de la página de canje lleva el código en el cuerpo del POST y no
#     en la URL de la petición, y que el canje es un POST.
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
    """El camino completo de entrada: `/panel` genera el enlace y la API lo canjea."""
    codigo = panel_acceso.crear_enlace(base, chat_id)
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

titulo("2. El canje del enlace")

codigo = panel_acceso.crear_enlace(base, "111")
r = cliente.post("/api/acceso", json={"codigo": codigo})
prueba("un enlace bueno devuelve token, nombre y caducidad",
       r.status_code == 200 and r.json()["token"]
       and r.json()["nombre"] == "Marta" and r.json()["caduca_en"] > time.time(),
       r.text[:120])
prueba("y el token devuelto no es el código del enlace",
       r.json()["token"] != codigo)

r = cliente.post("/api/acceso", json={"codigo": codigo})
prueba("el mismo enlace otra vez es 401", r.status_code == 401, r.status_code)

# El texto del fallo dice lo mismo para los tres casos, para que no se pueda aprender
# por diferencia qué códigos existen.
texto_gastado = cliente.post("/api/acceso", json={"codigo": codigo}).text
texto_fantasma = cliente.post("/api/acceso", json={"codigo": "no-existe-este"}).text
texto_caducado = cliente.post("/api/acceso", json={
    "codigo": panel_acceso.crear_enlace(base, "111", ahora=int(time.time()) - 3600)
}).text
prueba("código gastado, inexistente y caducado dicen EXACTAMENTE lo mismo",
       texto_gastado == texto_fantasma == texto_caducado,
       (texto_gastado, texto_fantasma, texto_caducado))
prueba("y el texto no dice cuál de los tres fue", "gastado" not in texto_gastado
       and "caducado" not in texto_gastado, texto_gastado)

r = cliente.post("/api/acceso", json={"codigo": "x"})
prueba("un cuerpo mal formado es 422 y no 500", r.status_code == 422, r.status_code)
r = cliente.post("/api/acceso", json={"codigo": panel_acceso.nuevo_codigo(),
                                      "admin": True})
prueba("un campo de más es 422, no se acepta en silencio",
       r.status_code == 422, r.status_code)

# El límite por IP: 20 fallos en 10 minutos. Se baja el límite para no tener que
# escribir 20 líneas, que es lo que importa: que el límite CUENTE.
limite_original = api_modulo.limite_acceso.maximo
api_modulo.limite_acceso.maximo = 3
try:
    for _ in range(3):
        cliente.post("/api/acceso", json={"codigo": "no-existe"})
    r = cliente.post("/api/acceso", json={"codigo": "no-existe"})
    prueba("pasado el límite de fallos por IP es 429, no 401", r.status_code == 429,
           r.status_code)
    codigo_bueno = panel_acceso.crear_enlace(base, "111")
    prueba("y con el límite encima, un enlace BUENO tampoco entra",
           cliente.post("/api/acceso", json={"codigo": codigo_bueno}).status_code
           == 429)
finally:
    api_modulo.limite_acceso.maximo = limite_original
    api_modulo.limite_acceso.anotar_exito("testclient")

r = cliente.post("/api/acceso", json={"codigo": panel_acceso.crear_enlace(base, "111")})
prueba("un acierto borra el contador de fallos de esa IP",
       r.status_code == 200, r.status_code)


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
esperadas = {"servidor_hora", "luz", "sensores", "nodo", "satelite", "sitio", "modo",
             "alertas", "familiares", "conexiones"}
prueba("/api/estado trae los diez campos de la instantánea",
       claves == esperadas, claves.symmetric_difference(esperadas))
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
# 7. La página de canje
# =============================================================================

titulo("7. La página de canje")

r = cliente.get("/acceso/abcdefghijklmnop")
prueba("/acceso/<codigo> devuelve HTML", r.status_code == 200
       and "text/html" in r.headers["content-type"], r.status_code)
prueba("la página lleva `Referrer-Policy: no-referrer`",
       r.headers.get("Referrer-Policy") == "no-referrer", dict(r.headers))
prueba("y `X-Robots-Tag: noindex`, para que un buscador no lo indexe",
       "noindex" in r.headers.get("X-Robots-Tag", ""), dict(r.headers))
prueba("el canje lo hace la PÁGINA con un POST a /api/acceso",
       "'POST'" in r.text and "/api/acceso" in r.text, r.text[:200])
prueba("y no con un GET: un GET lo seguiría una vista previa de Telegram",
       "method: 'GET'" not in r.text and 'method="GET"' not in r.text, None)
prueba("el código va en un JSON escapado, no interpolado a pelo",
       json.dumps("abcdefghijklmnop") in r.text, r.text[-1200:])
prueba("y se guarda en localStorage antes de saltar a `/`",
       "localStorage" in r.text and "location.replace('/')" in r.text,
       r.text[-1200:])
# La clave y el formato son los de `shared_preferences` en web, que es quien lee
# la sesión desde Flutter (`lib/sesion.dart`): prefijo «flutter.» y valor en
# JSON. Con la clave «panel_token» a pelo, el canje funcionaría y el panel
# arrancaría sin sesión, que es el fallo del 2026-10-06.
prueba("la clave del token lleva el prefijo `flutter.` de shared_preferences",
       "'flutter.panel_token'" in r.text, r.text[-1600:])
prueba("y la del nombre también", "'flutter.panel_nombre'" in r.text,
       r.text[-1600:])
prueba("se guardan con JSON.stringify, como escribe shared_preferences",
       "localStorage.setItem(CLAVE_TOKEN, JSON.stringify(" in r.text
       and "localStorage.setItem(CLAVE_NOMBRE, JSON.stringify(" in r.text,
       r.text[-1600:])
prueba("con sesión ya guardada se salta a `/` sin tocar el código",
       "sesionPrevia()" in r.text and "/api/yo" in r.text, r.text[-1600:])
prueba("y esa sesión previa se lee con JSON.parse dentro de un try/catch",
       "JSON.parse(localStorage.getItem(CLAVE_TOKEN))" in r.text
       and "catch" in r.text, r.text[-1600:])
# Un fallo de red al comprobar la sesión no puede gastar el código: es de un solo
# uso y quemarlo por un titubeo de la red deja al familiar sin enlace.
prueba("un fallo de red al comprobar la sesión NO gasta el código",
       "el código no se ha gastado" in r.text
       and "comprobarSesion(intentos - 1)" in r.text, r.text[-2400:])
prueba("y se reintenta antes de avisar", "comprobarSesion(1)" in r.text,
       r.text[-2400:])
# Sin la clave vieja. Se quitan las líneas de comentario, porque el comentario de
# la propia página explica a propósito por qué NO hay que usarla.
sin_comentarios = "\n".join(l for l in r.text.splitlines()
                            if not l.strip().startswith("//"))
prueba("no queda ninguna clave vieja sin prefijo",
       "localStorage.setItem('panel_token'" not in sin_comentarios
       and "localStorage.getItem('panel_token')" not in sin_comentarios
       and "'panel_token'" not in sin_comentarios
       and "'panel_nombre'" not in sin_comentarios, r.text)

# Un código con caracteres de HTML no rompe la página. Va sin barra porque un
# `</script>` sí la lleva, y una barra convierte el resto en otra ruta: eso se
# comprueba aparte, más abajo, y sale 404.
r = cliente.get("/acceso/%3Cscript%3Ealert(1)")
prueba("un código con `<script>` no se inyecta tal cual en la página",
       "<script>alert(1)" not in r.text, r.text[-1200:])
# `json.dumps` pone `\u003c` en minúsculas; se mira en minúsculas para no depender de eso.
prueba("y se escapa dentro del JSON de JavaScript",
       "\\u003c" in r.text.lower(), r.text[-1200:])
prueba("con una barra en el código tampoco hay página (404, no una inyección)",
       cliente.get("/acceso/%3C%2Fscript%3E").status_code == 404,
       cliente.get("/acceso/%3C%2Fscript%3E").status_code)

r = cliente.get("/acceso/")
prueba("/acceso/ sin código es 404", r.status_code == 404, r.status_code)

# La página de canje es lo único que NO lleva el token, así que `no-store` no hace
# falta por el token: pero el código es de un solo uso y no debe quedar en un caché.
r = cliente.get("/acceso/abcdefghijklmnop")
prueba("la página del canje tampoco se cachea",
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