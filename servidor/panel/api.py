"""La API del panel web: las rutas, el WebSocket y el arranque.

Es un servicio aparte del orquestador (apartado 2.1 de la propuesta) que comparte el
código de `orquestador/` porque comparte la base de datos y los nombres de los tópicos.
No importa `telegram` ni `bot`, y por eso puede arrancar en un contenedor sin Telegram
instalado.

## Por qué el token va en la cabecera y no en una cookie

Porque con cabecera no hay CSRF que pensar (apartado 3.4). Con cookie, cualquier
página de Internet podría pedir al navegador que mande la sesión del panel a esta API, y
el resultado sería que un familiar que tiene el panel abierto en otra pestaña le habría
dado su sesión a quien puso esa página.

## Por qué el WebSocket lleva el token en la query

Porque el navegador no puede mandar cabeceras al abrir un WebSocket (apartado 3.5).
Como eso mete el token en cualquier log que registre la URL, el servicio arranca con
`access_log=False` y lleva su propio log: accesos, 401, canjes y altas y bajas, sin
tokens ni códigos. `Sec-WebSocket-Protocol` existe y `WebSocketChannel.connect` la
admite, pero mete el token en una cabecera que algunos proxies tocan, y se deja
anotada sin usar.

## Por qué `POST /api/luz` responde 202

Porque lo que se acepta es la orden PUBLICADA, no un relé movido. El relé lo mueve el
ESP32 y el panel se entera cuando llegue el retenido `luz/estado` (apartado 4.1). Un
200 con la luz ya encendida en la respuesta sería mentir sobre lo que ha pasado, y el
estado real todavía no lo sabe nadie.

## Por qué el arranque no usa `--host`

Porque `PANEL_DIRECCIONES` es una LISTA y `network_mode: host` no admite varios
`--host` (apartado 6.2). `principal()` abre un socket por dirección y se los pasa a
`uvicorn.run(sockets=...)`, que es la única forma de escuchar en varias sin abrir
`0.0.0.0`. A esos sockets se les añade siempre `127.0.0.1`, que es donde va el
healthcheck: si Tailscale estuviera caído, la dirección del panel no resolvería y el
contenedor quedaría «unhealthy» sin motivo (apartado 6.3).
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import socket
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import db
import panel_acceso
import seguridad
import estado as estado_modulo
from estado import ClienteEstado, EstadoSala

registro = logging.getLogger("panel-api")

# Dónde está el build de Flutter, montado en solo lectura (apartado 6.3).
RAIZ_WEB = os.environ.get("PANEL_RAIZ_WEB", "/srv/web")

estados = EstadoSala()
limite_acceso = seguridad.LimiteAccesos()
limite_token = seguridad.LimiteAccesos(config.INTENTOS_TOKEN_POR_IP,
                                       config.SEGUNDOS_LIMITE_TOKEN)
base: db.BaseDatos = None
mqtt: ClienteEstado = None


def ip_real(peer: str, cabeceras) -> str:
    """La IP del cliente real, para los límites y el log.

    Con Funnel el proxy de tailscaled (en este mismo host) reenvía al panel por
    `127.0.0.1` y SOBREESCRIBE `X-Forwarded-For` con la IP real del que pide
    (`Header.Set` en el código de Tailscale). Por eso se confía en esa cabecera
    solo cuando el peer es de bucle: cualquiera que entre directo a la LAN
    habla desde una IP que no es de bucle, y su «X-Forwarded-For» inventado se
    ignora. Sin esto, un límite por IP contaría a todo el mundo como «127.0.0.1».
    """
    if peer in ("127.0.0.1", "::1"):
        lista = (cabeceras or {}).get("x-forwarded-for", "")
        if lista:
            return lista.split(",")[0].strip()
    return peer or "desconocida"


# --- Los cuerpos de las peticiones -------------------------------------------


class Canje(BaseModel):
    """El cuerpo de `POST /api/acceso`: solo el código.

    `extra="forbid"` para que un campo de más no pase en silencio. Con `codigo: str` a
    secas, `{"codigo": "...", "admin": true}` se aceptaría e ignoraría lo de más; así
    un error en la página es un 422 que se ve en el log y no una sesión que no funciona.
    """

    model_config = {"extra": "forbid"}

    codigo: str = Field(min_length=4, max_length=128)


class OrdenLuz(BaseModel):
    """El cuerpo de `POST /api/luz`: solo `on` y `off`, nada de valores libres.

    Es lo que impide que un campo mal puesto acabe en un tópico MQTT (apartado 4.8). Y
    lo que se publica lo construye `ClienteEstado.publicar_luz()`, con su `ts` y su
    `origen`, así que ni siquiera un campo inventado podría pasar de aquí.
    """

    model_config = {"extra": "forbid"}

    estado: str = Field(pattern="^(on|off)$")


# --- El ciclo de vida ---------------------------------------------------------


@contextlib.asynccontextmanager
async def ciclo(app: FastAPI):
    """Abre la base, el MQTT y el WebSocket, y los cierra en orden.

    La base se abre ANTES que el MQTT y se cierra DESPUÉS, que es el mismo criterio que
    el orquestador: el MQTT entrega datos que ya están guardados, no al revés, y con el
    orden contrario un corte de luz dejaría al cliente conectado con la base cerrada.
    """
    global base, mqtt
    base = db.BaseDatos(config.RUTA_DB)
    registro.info("base de datos lista en %s", base.ruta)

    if os.environ.get("PANEL_SIN_MQTT_EN_PRUEBAS") == "1":
        # Solo para las pruebas de la API. El `TestClient` de FastAPI arranca el
        # lifespan, y sin esta puerta intentaría conectar con un broker de verdad: en la
        # máquina de pruebas no hay ninguno, y un `gaierror` al resolver el nombre
        # abortaría todas las pruebas antes de la primera.
        #
        # La variable es de entorno y no un parámetro porque al lifespan lo llama
        # FastAPI, no el código de las pruebas, así que no hay forma de pasarle nada.
        # Y NO se pone un `mqtt` falso aquí: las pruebas inyectan el suyo después de
        # importar el módulo, y un doble puesto en el lifespan se quedaría debajo del
        # suyo sin avisar.
        registro.info("PANEL_SIN_MQTT_EN_PRUEBAS=1: no se conecta al broker")
    else:
        mqtt = ClienteEstado(estados)
        mqtt.conectar()
    registro.info("modo del sitio: %s", config.MODO_SITIO)

    estados.scrupular(asyncio.get_running_loop())

    yield

    registro.info("cerrando el panel")
    if mqtt is not None:
        mqtt.desconectar()
    if base is not None:
        base.cerrar()


app = FastAPI(title="Panel del asistente", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=ciclo)


@app.middleware("http")
async def cabeceras(request: Request, llamar):
    """`no-store` en todo, y las de seguridad en todas partes (apartado 5.4).

    `Cache-Control: no-store` es lo que evita que un móvil sirva una instantánea vieja
    de su caché (apartado 4.8). `Referrer-Policy: no-referrer` va ahora en TODAS las
    respuestas y no solo en la del canje: el código de acceso sigue en la ruta, y un
    `Referer` se lo llevaría a cualquier recurso que se cargara desde ahí. `Permissions-
    Policy` corta el micrófono y la cámara (el panel no los usa y sí lo haría un código
    que se colara), y el CSP es mínimo porque el resto lo pone Flutter en el propio
    `index.html`.

    `HSTS` solo cuando la petición ya llegó por HTTPS: el flag viene de `X-Forwarded-
    Proto`, que con Funnel lo escribe el proxy de tailscale. Por HTTP puro (LAN) no se
    anuncia, porque entonces un navegador que entró por HTTP hace tiempo se quedaría sin
    poder volver a entrar sin pasar una vez por HTTPS.
    """
    respuesta = await llamar(request)
    respuesta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["X-Frame-Options"] = "DENY"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    respuesta.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()")
    respuesta.headers["Content-Security-Policy"] = (
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if request.headers.get("x-forwarded-proto", "").lower() == "https":
        respuesta.headers["Strict-Transport-Security"] = "max-age=31536000"
    return respuesta


# --- La sesión ---------------------------------------------------------------


def sesion_requerida(request: Request) -> dict:
    """La sesión de quien llama, o 401. Es la dependencia de todas las rutas de casa.

    La comprobación es la de `db.sesion_panel`, que mira las cuatro cosas a la vez: que
    el hash exista, que no esté revocado, que no haya caducado y que el familiar siga
    registrado. La cuarta es la que hace que `dar_de_baja()` cierre el panel, porque sus
    tokens se borran y, si ese borrado se quedara sin hacer, esta comprobación sigue
    cerrando la puerta.

    `seguridad.sesion_de` anota además el último uso, como mucho una vez por hora.

    Un 401 cuenta en `limite_token` (por la IP real): es la red de seguridad para que
    una lluvia de `Authorization:` inventadas no se convierta en otra cosa, y va
    SEPARADA del límite de canje para que las dos defensas no se gasten una a otra.
    """
    ip = ip_real(request.client.host if request.client else "", request.headers)
    token = seguridad.token_de_cabecera(request.headers.get("authorization", ""))
    fila = seguridad.sesion_de(base, token)
    if fila is None:
        if not limite_token.permitido(ip):
            registro.warning("401 bloqueado por limite desde %s", ip)
            raise HTTPException(429, "Se han hecho demasiados intentos. Espere "
                                     "unos minutos y vuelva a intentarlo.")
        limite_token.anotar_fallo(ip)
        registro.warning("401 en %s desde %s", request.url.path, ip)
        raise HTTPException(401, "Su sesión no es válida. Vuelva a entrar con "
                                 "un enlace nuevo.")
    limite_token.anotar_exito(ip)
    return fila


def _nombre_de(sesion: dict) -> str:
    """El nombre del familiar de la sesión, para el saludo y para el log de «atendida»."""
    return base.nombre_de(sesion["chat_id"])


# --- La salud ----------------------------------------------------------------


@app.get("/api/salud")
async def salud(request: Request):
    """El healthcheck. Sin autenticación y sin datos de la casa (apartado 4.1).

    Va sin auth a propósito: lo comprueba Docker, que no tiene token. Y no lleva
    `chat_id` ni token ni nada de la casa, porque un endpoint sin sesión es un endpoint
    público, y lo público se limita a lo que no importa.

    Desde la Etapa 2 lo abre cualquiera por Funnel, y por eso el detalle (url, esquema,
    modo, estado del broker) solo lo ve quien llama desde el bucle SIN `X-Forwarded-For`,
    que es el healthcheck de Docker. Con Funnel delante, detrás viene siempre esa
    cabecera y el detalle no sale: `ok` y `hora` alcanzan para decidir si el contenedor
    sigue.
    """
    peer = request.client.host if request.client else ""
    detalle = peer in ("127.0.0.1", "::1") and not request.headers.get(
        "x-forwarded-for")
    respuesta = {"ok": True, "hora": int(time.time())}
    if detalle:
        respuesta.update({
            "panel_url": config.PANEL_URL,
            "esquema": db.VERSION_ESQUEMA,
            "modo": config.MODO_SITIO,
            "mqtt": bool(mqtt and mqtt.conectado),
        })
    return respuesta


# --- El acceso ---------------------------------------------------------------


@app.post("/api/acceso")
async def acceso(cuerpo: Canje, request: Request):
    """Canjea el código de un solo uso por un token de sesión.

    Es un POST y no un GET porque una vista previa de Telegram, un escáner del navegador
    o un antivirus siguen los enlaces, y con un GET cualquiera de ellos gastaría el
    código antes de que el familiar haya hecho nada (apartado 3.1).

    El fallo responde 401 con el MISMO texto para un código que no existe, uno caducado
    y uno ya gastado, y no dice cuál. Un «código no válido» seguido de «código ya usado»
    en el mismo mensaje permite ir probando y aprender de los aciertos.
    """
    ip = ip_real(request.client.host if request.client else "", request.headers)

    if not limite_acceso.permitido(ip):
        registro.warning("acceso bloqueado por limite desde %s", ip)
        raise HTTPException(429, "Se han hecho demasiados intentos. Espere "
                                 "unos minutos y vuelva a intentarlo.")

    exito, fila = panel_acceso.canjear(base, cuerpo.codigo.strip())
    if not exito:
        limite_acceso.anotar_fallo(ip)
        registro.warning("canje fallido desde %s", ip)
        raise HTTPException(401, "Este enlace ya no sirve. Escriba /panel al "
                                 "bot de la casa para pedir uno nuevo.")

    limite_acceso.anotar_exito(ip)
    token, caduca_en = panel_acceso.crear_token(base, fila["chat_id"])
    registro.info("canje correcto: %s entra; caduca en %d dias",
                  db.enmascarar_chat(fila["chat_id"]), config.DIAS_TOKEN_PANEL)
    return {"token": token, "nombre": base.nombre_de(fila["chat_id"]),
            "caduca_en": caduca_en}


@app.get("/api/yo")
async def yo(sesion: dict = Depends(sesion_requerida)):
    """El saludo de la cabecera: nombre y cuándo caduca.

    El nombre sale de `familiares`, no del token, para que cambiarlo en la base se vea
    sin tener que volver a hacer `/panel`. El `chat_id` no sale de aquí: la interfaz no
    lo necesita y no tiene por qué tenerlo.
    """
    return {"nombre": _nombre_de(sesion), "caduca_en": sesion["caduca_en"],
            "caduca_dias": config.DIAS_TOKEN_PANEL}


@app.post("/api/salir")
async def salir(sesion: dict = Depends(sesion_requerida)):
    """Revoca el token actual (apartado 3.4).

    Revoca SOLO este token y no los demás del familiar: si una misma persona tiene el
    panel abierto en el móvil y en el portátil, cerrar sesión en uno no debería cerrar
    el otro. Para cerrar todos los de una persona está
    `db.revocar_token_panel(chat_id)`, que es la operación del operador.
    """
    panel_acceso.revocar(base, sesion["token_hash"])
    registro.info("%s ha cerrado sesion", db.enmascarar_chat(sesion["chat_id"]))
    return {"ok": True}


# --- El estado ---------------------------------------------------------------


@app.get("/api/estado")
async def estado(sesion: dict = Depends(sesion_requerida)):
    """La instantánea completa (apartado 4.2).

    Viene de `EstadoSala`, que la tiene en memoria. Las alertas y el número de
    familiares salen de SQLite, porque no hay forma de tenerlos al día por MQTT: el
    panel no tiene permiso de lectura en `casa/sala/alerta/#` y no lo necesita (4.6).
    """
    del sesion
    return estados.instantanea(base)


# --- Las alertas -------------------------------------------------------------


def _alerta_json(fila) -> dict:
    """Una alerta en la forma de la API, con el estado DERIVADO.

    El estado es el mismo `CASE` derivado que ve `/alertas` del bot, y por eso lo
    calcula `db` y no esta función: si el botón de Telegram y el del panel pusieran el
    estado con reglas propias, los dos podrían decir cosas distintas de la misma
    alerta.

    `resuelto_en` se devuelve como `resuelta_en` a propósito: la columna se llama así
    porque el verbo del bot es «resolver», y la API habla de alertas que ya están
    resueltas. El nombre de la clave lo pone aquí y no en `db`, donde la columna es de
    la base y su nombre no tiene por qué coincidir con el del JSON.
    """
    carga = json.loads(fila["carga_json"]) if fila["carga_json"] else {}
    return {
        "id": fila["id"],
        "tipo": fila["tipo"],
        "estado": fila["estado"],
        "creado_en": fila["creado_en"],
        "carga": carga,
        "resuelta_en": fila["resuelto_en"],
        "descartada_en": fila["descartada_en"],
        "enviados": fila["enviados"],
        "sin_mandar": fila["sin_mandar"],
    }


@app.get("/api/alertas")
async def alertas(sesion: dict = Depends(sesion_requerida),
                  limite: int = Query(20, ge=1, le=100),
                  tipo: str = Query("", max_length=16),
                  desde: int = Query(0, ge=0)):
    """El historial de alertas, lo más nuevo primero.

    `desde` es lo que usa el cliente para preguntar cada 5 s «qué hay de nuevo» (4.6):
    lleva índice por `creado_en`, así que son peticiones diminutas, y es lo que hace
    que una alerta se vea sin recargar la página (P08).
    """
    del sesion
    filas = base.listar_recientes(limite=limite, tipo=tipo or None)
    if desde:
        filas = [f for f in filas if f["creado_en"] > desde]
    return {"alertas": [_alerta_json(f) for f in filas],
            "servidor_hora": int(time.time())}


@app.post("/api/alertas/{id_alerta}/atender")
async def atender(id_alerta: str, sesion: dict = Depends(sesion_requerida)):
    """Resuelve una alerta. Idempotente (apartado 4.7).

    Devuelve 200 con `"fresca": false` si ya estaba resuelta, y no un 409: al que
    pulsa le da igual si la resolvió desde aquí o desde el botón de Telegram, y un 409
    obligaría al cliente a distinguir dos casos que en pantalla se ven igual. Lo que sí
    importa es que `alertas.resolver` avise al satélite SOLO si estaba sin resolver,
    para que resolver dos veces no haga hablar a la casa dos veces.
    """
    import alertas as alertas_modulo

    if not alertas_modulo.id_valido(id_alerta):
        raise HTTPException(404, "No se encontró esa alerta.")

    fila, fresca = alertas_modulo.resolver(
        base, mqtt.publicar if mqtt else None, id_alerta,
        quien=_nombre_de(sesion) or db.enmascarar_chat(sesion["chat_id"]),
        origen="el panel",
    )
    if fila is None:
        raise HTTPException(404, "No se encontró esa alerta.")

    respuesta = _alerta_json(_con_estado(fila))
    respuesta["fresca"] = fresca
    return respuesta


def _con_estado(fila) -> dict:
    """La fila de `obtener_alerta`, que es cruda, con el estado derivado y los contadores.

    `db.obtener_alerta()` es un `SELECT *` y por eso no trae el `estado` derivado ni
    los dos contadores que sí trae `listar_recientes`. Se buscan ahí y se combinan con
    la fila cruda para que `_alerta_json` tenga un solo camino: con dos, la respuesta
    de «atender» acabaría difiriendo de la del listado en un campo que el cliente ve.
    """
    completas = [f for f in base.listar_recientes(limite=100) if f["id"] == fila["id"]]
    if not completas:
        # La alerta existe pero no sale en las 100 más recientes: se responde con lo
        # que hay y el estado derivado de `marcar_resuelta`, que ya se sabe porque
        # `resolver` la acaba de marcar. Un 404 aquí sería mentira.
        return {"id": fila["id"], "tipo": fila["tipo"], "estado": "resuelta",
                "creado_en": fila["creado_en"], "carga_json": fila["carga_json"],
                "resuelto_en": fila["resuelto_en"],
                "descartada_en": fila["descartada_en"], "enviados": 0, "sin_mandar": 0}
    return dict(completas[0])


# --- La luz ------------------------------------------------------------------


@app.post("/api/luz", status_code=202)
async def luz(cuerpo: OrdenLuz, sesion: dict = Depends(sesion_requerida)):
    """Publica la orden de luz. 202, porque lo que se acepta es la orden (4.1).

    No devuelve el estado de la luz: devuelve lo que se ha publicado. El relé lo mueve
    el ESP32 y el panel lo sabe cuando llegue el retenido `luz/estado`, que llega por
    el WebSocket como evento `luz`. Devolver la luz ya encendida sería mentir.
    """
    del sesion
    if mqtt is None or not mqtt.conectado:
        raise HTTPException(503, "El panel no puede comunicarse con la casa en "
                                 "este momento. Inténtelo de nuevo en unos minutos.")
    ts = int(time.time())
    try:
        # El `ts` se pasa, y no se deja que lo ponga `publicar_luz`, para que el que
        # vuelve en la respuesta sea exactamente el que ha ido por MQTT.
        mqtt.publicar_luz(cuerpo.estado, ts=ts)
    except Exception as error:  # noqa: BLE001
        registro.error("no se pudo publicar la orden de luz: %s",
                       _sin_secretos(error))
        raise HTTPException(502, "No se pudo enviar la orden. Inténtelo de nuevo.")
    return {
        "aceptado": True,
        "publicado": {"estado": cuerpo.estado, "origen": "panel", "ts": ts},
        "nota": "la orden está publicada; el estado real llega por el WebSocket",
    }


def _sin_secretos(error) -> str:
    """El texto de un error, sin secretos.

    El `except` va en la respuesta de la API, y un error de paho puede llevar dentro
    el usuario y la contraseña del broker. Se filtra antes de escribirlo en el log, no
    después: un log es un sitio del que se puede leer mucho después.
    """
    try:
        import sanear

        return sanear.sanear(error)
    except Exception:  # noqa: BLE001
        return "error sin detalle"


# --- El WebSocket ------------------------------------------------------------


@app.websocket("/api/vivo")
async def vivo(ws: WebSocket):
    """Instantánea inicial y, a partir de ahí, solo cambios (apartado 4.3).

    El token va en la query porque el navegador no puede mandar cabeceras aquí, y por
    eso el servicio arranca sin log de accesos: con él puesto, el token entero estaría
    en un archivo del contenedor.

    El cierre 1013 («recargando») es para cuando la cola del cliente se llena, que pasa
    cuando alguien deja el panel abierto en un móvil con la pantalla apagada. No es un
    error: al reconectar le llega la instantánea entera, que es justo lo que no se puede
    garantizar con una lista de parches.
    """
    sesion = seguridad.sesion_de(base, ws.query_params.get("token", ""))
    if sesion is None:
        # La IP real, igual que en las rutas HTTP (el contenedor recibe el 401 en el
        # WebSocket con la misma lógica `ip_real`): la IP de Funnel llega en la cabecera
        # y el fallo cuenta en el mismo `limite_token` que los 401 HTTP. Aquí el límite
        # no puede responder un 429 (en un WebSocket no hay), y no hace falta: no aceptar
        # la sesión ya es cerrar la puerta.
        limite_token.anotar_fallo(ip_real(ws.client.host if ws.client else "",
                                          ws.headers))
        registro.warning("websocket sin sesion valida desde %s",
                         ip_real(ws.client.host if ws.client else "", ws.headers))
        # Cerrar SIN haber hecho `accept` no manda nada: el WebSocket tiene que
        # aceptarse para poder escribir un close. Con `accept` primero, el 1008
        # llega al cliente y el navegador no se queda esperando hasta que agote su
        # propio temporizador.
        await ws.accept()
        await ws.close(code=1008, reason="sesion no valida")
        return

    await ws.accept()
    limite_token.anotar_exito(ip_real(ws.client.host if ws.client else "",
                                      ws.headers))
    registro.info("%s abre el websocket", db.enmascarar_chat(sesion["chat_id"]))

    oyente = estados.oyente_nuevo()
    tareas = []
    try:
        await ws.send_text(json.dumps(
            {"tipo": "estado", "datos": estados.instantanea(base)},
            separators=(",", ":")))
        # Cuatro tareas porque hay cuatro cosas que pasan a la vez: llega un `pong`, hay
        # que mandar el latido, hay que sacar eventos de la cola, y hay que estar
        # atento a que la cola se llene. `asyncio.wait` con `FIRST_COMPLETED` devuelve
        # en cuanto la primera acaba, y normalmente la que acaba es `_leer`: cuando el
        # cliente se va, el `receive()` devuelve `websocket.disconnect` y esa tarea
        # termina. Las otras tres no terminan solas (salvo `_vigilar_saturacion`, que
        # termina justo cuando toca cerrar), así que sin `_leer` en la lista el `wait`
        # se quedaría colgado hasta el siguiente latido, con el socket muerto y sin
        # cerrar. Y `_vigilar_saturacion` está en la lista por lo mismo que las demás:
        # si no, el cierre por saturación nunca llegaría al `wait` y la conexión se
        # quedaría colgada hasta el siguiente latido o hasta que el cliente se fuera.
        tareas = [asyncio.create_task(_leer(ws, oyente)),
                  asyncio.create_task(_latido(ws, oyente)),
                  asyncio.create_task(_eventos(ws, oyente)),
                  asyncio.create_task(_vigilar_saturacion(ws, oyente))]
        terminado, _ = await asyncio.wait(
            tareas, return_when=asyncio.FIRST_COMPLETED)
        for tarea in terminado:
            with contextlib.suppress(Exception):
                tarea.result()
    finally:
        for tarea in tareas:
            tarea.cancel()
        estados.oyente_baja(oyente)
        registro.info("%s cierra el websocket", db.enmascarar_chat(sesion["chat_id"]))


async def _leer(ws: WebSocket, oyente) -> None:
    """Lee del cliente y anota el `pong`. Vuelve cuando el socket se cae.

    El `ping` del cliente se ignora a propósito: el protocolo WebSocket ya lo contesta a
    nivel de transporte, y el `pong` que cuenta para el reloj es el del servidor, que es
    un mensaje de aplicación (`{"tipo": "pong"}`).

    El `receive_text()` devuelve un dict vacío cuando el socket se cierra, no `None`,
    y ahí hay que volver. Por eso se comprueba la carga en vez de mirar si el texto es
    `None`: con `TextMessage` vacío, seguir leyendo daría error de deserialización en
    vez de un cierre limpio.
    """
    while True:
        mensaje = await ws.receive()
        if mensaje.get("type") == "websocket.disconnect":
            return
        texto = mensaje.get("text")
        if texto is None:
            continue
        if _es_pong(texto):
            oyente.ultimo_pong = time.monotonic()


def _es_pong(mensaje) -> bool:
    try:
        carga = json.loads(mensaje)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(carga, dict) and carga.get("tipo") == "pong"


async def _latido(ws: WebSocket, oyente) -> None:
    """Un `latido` cada 20 s, y cierre a los 45 s sin `pong` (apartado 4.3).

    El reloj lo reinicia `_leer` con cada `pong` y no cada envío, porque lo que se
    mide es cuánto lleva el móvil sin contestar, que es lo que dice si sigue delante.
    Un móvil con la pantalla apagada deja de contestar sin avisar, y por eso el cierre
    tiene que existir: sin él, esa conexión se quedaría «conectada» para siempre,
    mandando latidos a nadie y ocupando una de las plazas de `conexiones`.
    """
    while True:
        await asyncio.sleep(config.SEGUNDOS_LATIDO_WS)
        if time.monotonic() - oyente.ultimo_pong > config.SEGUNDOS_PONG_WS:
            registro.warning("45 s sin pong; se cierra el websocket con 1001")
            await ws.close(code=1001, reason="sin pong")
            return
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps(
                {"tipo": "latido", "datos": {"ts": int(time.time())}},
                separators=(",", ":")))


async def _eventos(ws: WebSocket, oyente) -> None:
    """Saca eventos de la cola y los manda, hasta que se llene.

    La cola se llena si este bucle no llega a correr, porque `emitir` no bloquea: pone el
    evento y sigue.

    Ni la marca ni el testigo bastan por si solos, y por eso hay dos cosas. Cuando este
    bucle esta esperando en `cola.get()`, el testigo lo despierta y cierra; cuando esta
    bloqueado en `send_text` (que es lo que pasa con el cliente de verdad que ha dejado
    de leer), no hay a quien mirar, y el cierre lo hace `_vigilar_saturacion` desde
    fuera. Las dos cosas juntas cubren los dos sitios en los que este bucle puede estar
    parado esperando al cliente.

    El testigo se distingue por ser `None`, y no solo por mirar `oyente.saturado`, porque
    la marca la pone tambien quien llena la cola y el testigo es lo que garantiza que
    ahora mismo hay algo que sacar de la cola. Si se mandara a la ligera, el `send_text`
    intentaria serializar un `None` y soltaria `"null"` por el WebSocket, que es un
    mensaje que el movil no espera.
    """
    while True:
        evento = await oyente.cola.get()
        if oyente.saturado or evento is estado_modulo.TESTIGO_SATURADO:
            await ws.close(code=1013, reason="recargando")
            return
        await ws.send_text(json.dumps(evento, separators=(",", ":")))


async def _vigilar_saturacion(ws: WebSocket, oyente) -> None:
    """Espera a que la cola se llene y cierra con 1013. Es lo que de verdad cierra.

    Está aparte de `_eventos` a propósito, que es donde se decidió que el cierre no puede
    depender solo de quien escribe. `_eventos` puede estar bloqueado en `send_text`
    esperando a que el socket del cliente drene, y un cliente que no lee no drena nunca:
    se queda ahí indefinidamente aunque la marca esté puesta. Con el aviso en un
    `asyncio.Event`, esta tarea se entera pase lo que pase, y cerrar el socket desde
    aquí además desbloquea el `send_text` de `_eventos` (a un `close()` de
    `websockets` le sigue un error en el envío pendiente), así que las dos se desbloquean
    mutuamente sin que ninguna tenga que esperar un tiempo fijo.
    """
    await oyente.saturado_espera.wait()
    await ws.close(code=1013, reason="recargando")


# --- La página de canje ------------------------------------------------------

PAGINA_CANJE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex, nofollow">
<title>Entrar al panel</title>
<style>
  :root { color-scheme: light; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font-family: system-ui, -apple-system, sans-serif; background:#F4F5F2;
         color:#1B1F1E; }
  main { max-width:26rem; padding:2rem; text-align:center; }
  h1 { font-size:1.3rem; margin:0 0 1rem; font-weight:600; }
  p { line-height:1.5; color:#4A524F; }
  .mal { color:#9F1D1D; }
  .bien { color:#0E5E5A; }
  .barra { height:.35rem; background:#DADFDB; border-radius:99px; overflow:hidden; }
  .barra > i { display:block; height:100%; width:0; background:#0E5E5A;
               transition:width .3s; }
</style>
</head>
<body>
<main>
  <h1 id="titulo">Entrando al panel</h1>
  <div class="barra"><i id="progreso"></i></div>
  <p id="texto">Un momento, por favor.</p>
</main>
<script>
var CODIGO = /*CODIGO*/null;
var titulo = document.getElementById('titulo');
var texto = document.getElementById('texto');
var progreso = document.getElementById('progreso');

function fallo(mensaje, tituloNuevo) {
  titulo.textContent = tituloNuevo || 'Este enlace ya no sirve';
  texto.textContent = mensaje;
  texto.className = 'mal';
  progreso.style.width = '100%';
  progreso.style.background = '#9F1D1D';
}

// ---------------------------------------------------------------------------
// CLAVES DEL ALMACÉN: «flutter.» + valor en JSON. NO QUITAR EL PREFIJO.
//
// Este HTML no es el que lee la sesión: la lee el panel Flutter, que usa
// `shared_preferences` (lib/sesion.dart). En web, `shared_preferences` es una
// capa sobre `localStorage` que hace dos cosas antes de escribir:
//
//   1. Anteponer el prefijo «flutter.» a la clave, y
//   2. guardar el valor con `JSON.stringify`, es decir, una cadena entre
//      comillas dobles.
//
// Al leer hace lo contrario (`JSON.parse` y quita el prefijo). Así que si aquí
// se guardase `localStorage.setItem('panel_token', token)`, la página saltaría
// a `/`, Flutter leería `flutter.panel_token` (que no existe), no encontraría
// sesión y mostraría la pantalla de acceso con «este enlace ya se usó»: el
// código se habría gastado y la sesión existiría debajo de otra clave.
//
// OJO con «arreglar» esto quitando el prefijo o el JSON: es el síntoma exacto
// del fallo del 2026-10-06. Las claves «panel_token» y «panel_nombre» son las
// de `Sesion._claveToken` / `Sesion._claveNombre` en `lib/sesion.dart`; si allí
// cambian, cambian aquí, en los dos sitios a la vez.
// ---------------------------------------------------------------------------
var CLAVE_TOKEN = 'flutter.panel_token';
var CLAVE_NOMBRE = 'flutter.panel_nombre';

function guardarSesion(token, nombre) {
  localStorage.setItem(CLAVE_TOKEN, JSON.stringify(token));
  localStorage.setItem(CLAVE_NOMBRE, JSON.stringify(nombre || ''));
}

// `try/catch` porque `JSON.parse` lanza sobre cualquier texto que no sea JSON
// válido, y una clave con el mismo nombre puede haberla escrito otra cosa (otro
// servicio en el mismo origen, o una versión vieja de esta página). Una sesión
// ilegible vale lo mismo que ninguna: se canjea el código.
function sesionPrevia() {
  try {
    var valor = JSON.parse(localStorage.getItem(CLAVE_TOKEN));
    return (typeof valor === 'string' && valor) ? valor : null;
  } catch (e) {
    return null;
  }
}

function abrirPanel() {
  titulo.textContent = 'Listo';
  texto.textContent = 'Abriendo el panel';
  texto.className = 'bien';
  progreso.style.width = '100%';
  // El código sale de la barra de direcciones en cuanto salta a `/`, y con él se va
  // de los historiales del navegador y de la vista previa del enlace.
  location.replace('/');
}

function canjear() {
  fetch('/api/acceso', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    // Sin `credentials`: la API no usa cookie, así que mandarla solo añadiría ruido.
    body: JSON.stringify({codigo: CODIGO})
  }).then(function (r) {
    progreso.style.width = '80%';
    if (!r.ok) {
      fallo('Este enlace ya se usó. Escriba /panel al bot de la casa y pida un enlace nuevo.');
      return null;
    }
    return r.json();
  }).then(function (d) {
    if (!d) { return; }
    // El token se guarda en `localStorage`, que es POR ORIGEN: por eso esta página
    // canjea en el origen donde está abierta y no en la dirección que traía el enlace.
    // Es lo que hace que pegar un enlace de `villaca` en un móvil que abre el panel por
    // `192.168.1.169` funcione sin pedir `/panel` dos veces.
    guardarSesion(d.token, d.nombre);
    abrirPanel();
  }).catch(function () {
    fallo('No se pudo contactar con el servidor. Compruebe la conexión e inténtelo de nuevo.');
  });
}

// Si este navegador ya tiene una sesión válida, se entra sin gastar el código: así
// pegar un enlace viejo en el móvil de la persona funciona igual, y quien no tenga
// sesión sigue viendo «ya no sirve». El «un solo uso» no pierde nada por esto.
//
// Un fallo de RED al comprobar esa sesión NO gasta el código. El código es de un
// solo uso, y quemarlo porque Tailscale titubeó un segundo deja al familiar sin
// enlace y con un «ya se usó» que no es verdad: por eso se reintenta una vez y,
// si sigue sin responder, se avisa sin canjear nada.
function comprobarSesion(intentos) {
  fetch('/api/yo', {
    headers: {'Authorization': 'Bearer ' + tokenPrevio}
  }).then(function (r) {
    if (r.ok) { abrirPanel(); } else { canjear(); }
  }).catch(function () {
    if (intentos > 0) {
      setTimeout(function () { comprobarSesion(intentos - 1); }, 1500);
    } else {
      fallo('No se pudo comprobar la sesión. Compruebe la conexión y vuelva a '
            + 'abrir el enlace; el código no se ha gastado.',
            'No se pudo entrar');
    }
  });
}

var tokenPrevio = sesionPrevia();
if (tokenPrevio) {
  comprobarSesion(1);
} else {
  canjear();
}
</script>
</body>
</html>
"""


def _js_seguro(texto: str) -> str:
    r"""Un texto de Python como literal de JavaScript, seguro dentro de un `<script>`.

    `json.dumps` solo escapa lo que no es ASCII, y deja los `<`, `>` y `&` como están.
    Para un valor que va a un `localStorage` o a un cuerpo JSON eso da igual, pero este
    va dentro de un `<script>` del HTML que devuelve la respuesta: ahí un
    `</script>` en el dato cierra la etiqueta y lo que venga después se ejecuta. Un
    `<script>alert(1)</script>` en el campo de la URL se ejecutaría en el navegador de
    quien lo abriera, y quien lo abre es el que ve el enlace.

    Por eso se escapan a mano los tres que rompen el HTML, y también U+2028 y U+2029,
    que son separadores de línea válidos en un literal de JavaScript pero no en
    JavaScript de verdad: `json.dumps` los deja pasar y el navegador los cuenta como
    fin de línea, que rompe el `var CODIGO = ...` por la mitad.

    El resultado sigue siendo un literal de JavaScript válido, porque `\uXXXX` lo es
    dentro de una cadena. Es lo que hacen las librerías de plantillas que se toman en
    serio esto.
    """
    escapado = json.dumps(texto)
    for caracter, reemplazo in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                                 ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        escapado = escapado.replace(caracter, reemplazo)
    return escapado


@app.get("/acceso/{codigo}", response_class=HTMLResponse)
async def pagina_acceso(codigo: str):
    """La página de canje: HTML mínimo, sin Flutter (apartado 3.1).

    Va declarada ANTES que el montaje de los ficheros estáticos a propósito. Con el
    `StaticFiles` montado en `/`, una ruta como `/acceso/abc` acabaría resoliéndose
    contra el build y el código se leería en el cliente en vez de canjearse aquí.
    """
    codigo = codigo.strip("/")
    if not codigo or "/" in codigo or len(codigo) > 128:
        raise HTTPException(404, "El enlace de acceso no es válido.")
    return HTMLResponse(PAGINA_CANJE.replace("/*CODIGO*/null", _js_seguro(codigo)),
                        headers={"Referrer-Policy": "no-referrer",
                                 "X-Robots-Tag": "noindex, nofollow"})


# --- El panel compilado ------------------------------------------------------

app.mount("/", StaticFiles(directory=RAIZ_WEB, html=True), name="web")


# --- El arranque -------------------------------------------------------------


def principal() -> None:
    """Abre un socket por dirección y se los pasa a uvicorn.

    Es la única forma de escuchar en varias direcciones con `network_mode: host`
    (apartado 6.2), porque `--host` solo admite una. Se añade siempre `127.0.0.1` al
    final aunque no esté en `PANEL_DIRECCIONES`, porque es donde va el healthcheck y no
    debe depender de que Tailscale esté en pie (apartado 6.3).

    Si una dirección no abre, se avisa y se sigue con las demás: `PANEL_DIRECCIONES` se
    escribe a mano en `.env` y un error de dedo no puede dejar al panel sin todo.
    """
    import uvicorn

    sockets = []
    direcciones = list(config.PANEL_DIRECCIONES)
    if "127.0.0.1" not in direcciones:
        direcciones.append("127.0.0.1")

    for direccion in direcciones:
        nuevo = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        nuevo.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            nuevo.bind((direccion, config.PANEL_PUERTO))
        except OSError as error:
            registro.error("no se pudo escuchar en %s:%d (%s); se sigue sin esa "
                           "direccion", direccion, config.PANEL_PUERTO, error)
            nuevo.close()
            continue
        sockets.append(nuevo)
        registro.info("escuchando en %s:%d", direccion, config.PANEL_PUERTO)

    if not sockets:
        registro.error("ninguna direccion pudo abrirse; el panel no arranca")
        raise SystemExit(1)

    registro.info("panel en modo %s; url declarada %s", config.MODO_SITIO,
                  config.PANEL_URL or "(vacia)")
    registro.info("sin log de accesos a proposito: el token del websocket viaja en la "
                  "query y no debe acabar en un archivo")

    # `Server.serve()` y no `uvicorn.run()`, porque `run()` no tiene forma de recibir
    # sockets ya abiertos: solo acepta `host`/`port`, que abren UN socket en 0.0.0.0.
    # Aquí los sockets abren a mano para poder distinguir direcciones, y en cuanto uno
    # falla (una IP de Tailscale que ya no existe, el puerto ocupado en la LAN) se sigue
    # con los que sí hayan abierto. Con `run()` bastaría con que un bind fallara para
    # que no hubiera panel.
    configuracion = uvicorn.Config(app, log_config=None, access_log=False,
                                   timeout_graceful_shutdown=10)
    # `serve()` es una corrutina: `run()` es la que monta el bucle por dentro, y aquí no
    # se puede usar porque no acepta los sockets. Se abre un bucle nuevo con `asyncio.run`
    # y es el MISMO que ve el lifespan, porque `scrupular()` guarda el bucle que hay en
    # ese momento. Por eso `scrupular` va dentro del lifespan y no aquí antes de arrancar.
    servidor = uvicorn.Server(configuracion)
    asyncio.run(servidor.serve(sockets=sockets))


class _QuitaTokens(logging.Filter):
    """Sustituye el valor de `token=...` por `[token]` en todo lo que se registra.

    Hace falta porque `access_log=False` NO basta para el WebSocket. Ese parámetro
    apaga el log de accesos HTTP de uvicorn, pero el mensaje de apertura de un WebSocket
    sale por otra parte: en `websocket.accept`, uvicorn escribe

        127.0.0.1:51748 - "WebSocket /api/vivo?token=<43 caracteres>" [accepted]

    sin mirar `access_log`. Y como en este panel el token va en la query (no hay cookie
    ni cabecera en un WebSocket), ahí queda la sesión de un familiar escrita en claro en
    `docker logs`, que es un archivo que se lee, se copia y suele vivir meses.

    Se filtra por el logger `uvicorn.error`, que es por donde sale ese mensaje, y no
    tocando el de uvicorn entero: los errores de ese logger son los únicos que
    importan si algo va mal, y no se pueden perder por filtrar de más.

    El patrón es `token=<lo que no tenga un `&` ni espacios>`, que es lo bastante
    estrecho para no romper los otros mensajes de uvicorn, que son casi todos
    `ruta?parametro=valor`.
    """

    PATRON = re.compile(r"token=[^\s&\"]+")

    def filter(self, registro: logging.LogRecord) -> bool:
        # `getMessage()` y no `record.msg`: aquí los mensajes de uvicorn ya vienen
        # formateados con los parámetros puestos, porque los escribe con `%s` y `%`
        # directamente. Filtrar el `msg` dejaría el token intacto en el `args`.
        mensaje = registro.getMessage()
        if "token=" not in mensaje:
            return True
        registro.msg = self.PATRON.sub("token=[quitado]", mensaje)
        registro.args = ()
        return True


def configurar_log() -> None:
    """El log, igual que el del orquestador.

    Sin `telegram` que silenciar: este proceso no lo importa, y por eso no puede
    filtrar un token de esa manera. El token de aquí sí, y con un filtro propio
    (`_QuitaTokens`), porque en un WebSocket no hay ni cookie ni cabecera que filtrar.
    """
    niveles = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
               "AVISO": logging.WARNING, "WARNING": logging.WARNING,
               "ERROR": logging.ERROR}
    nivel = niveles.get(config.NIVEL_LOG, logging.INFO)
    logging.basicConfig(level=nivel,
                        format="%(asctime)s %(levelname)-7s %(name)-10s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("paho").setLevel(max(nivel, logging.WARNING))
    # `uvicorn.error` y no `uvicorn`: el mensaje con la query va por `uvicorn.error`, y
    # bajando a `uvicorn` se perderían los errores de arranque, que son justo los que
    # hay que ver cuando el contenedor no levanta.
    logging.getLogger("uvicorn.error").addFilter(_QuitaTokens())


if __name__ == "__main__":
    configurar_log()
    principal()