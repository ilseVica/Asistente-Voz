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
import secrets
import socket
import time
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import alertas as alertas_modulo
import config
import db
import panel_acceso
import recordatorios
import seguridad
import estado as estado_modulo
from estado import ClienteEstado, EstadoSala

registro = logging.getLogger("panel-api")

# Dónde está el build de Flutter, montado en solo lectura (apartado 6.3).
RAIZ_WEB = os.environ.get("PANEL_RAIZ_WEB", "/srv/web")

estados = EstadoSala()
# Dos topes para el canje del código, los dos en memoria (apartado 3.1): por IP real
# y global. El de por IP frena a una casa; el global frena a quien reparte el ataque
# entre muchas IPs. Se cuentan FALLOS, no intentos.
limite_acceso = seguridad.LimiteAccesos(config.INTENTOS_CODIGO_POR_IP,
                                        config.SEGUNDOS_LIMITE_CODIGO)
limite_codigo_global = seguridad.LimiteAccesos(config.FALLOS_CODIGO_GLOBALES,
                                               config.SEGUNDOS_LIMITE_CODIGO_GLOBAL)
limite_token = seguridad.LimiteAccesos(config.INTENTOS_TOKEN_POR_IP,
                                       config.SEGUNDOS_LIMITE_TOKEN)
# La clave con la que `LimiteAccesos` cuenta el tope global. No es una IP; es un
# nombre fijo que solo usa ese contador.
CLAVE_GLOBAL = "global"
base: db.BaseDatos = None
mqtt: ClienteEstado = None
_hablar_hasta: float = 0.0
_hablar_lock = __import__('threading').Lock()
_ultimo_id_conversacion: int = 0


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


class NuevoRecordatorio(BaseModel):
    """El cuerpo de `POST /api/recordatorios`.

    `extra="forbid"` como los demás: un campo de más es un 422 y no algo que se
    ignora en silencio. Los campos condicionales —`fecha` si es «una sola vez»,
    `dias` si es «ciertos días»— se validan en la ruta y no en el tipo, porque
    dependen de `repeticion`: cruzar campos en Pydantic pide un validador y el
    mensaje saldría genérico. A mano se puede dar un 422 de usted y concreto.
    """

    model_config = {"extra": "forbid"}

    texto: str = Field(min_length=1, max_length=120)
    hora: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    repeticion: str = Field(pattern="^(una_vez|diario|semanal)$")
    fecha: str | None = None
    dias: list[int] | None = None


class TiempoInactividad(BaseModel):
    """El cuerpo de `POST /api/inactividad`: los minutos sin movimiento antes de avisar.

    El rango va en el tipo (`ge`/`le`) y no en la ruta, y sale de las MISMAS
    constantes que usa el orquestador para validar: así el 422 del panel y el
    `ValueError` de `db` no pueden discrepar, porque son el mismo número. Un
    `extra="forbid"`, como todos, para que un campo de más no pase en silencio.
    """

    model_config = {"extra": "forbid"}

    tiempo_min: int = Field(ge=config.TIEMPO_INACTIVIDAD_MIN_MINIMO,
                            le=config.TIEMPO_INACTIVIDAD_MIN_MAXIMO)


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

    global _ultimo_id_conversacion
    try:
        fila = base.conexion.execute('SELECT MAX(id) AS m FROM conversacion').fetchone()
        _ultimo_id_conversacion = int(fila['m'] or 0)
    except Exception:
        _ultimo_id_conversacion = 0
    tarea_conv = asyncio.create_task(_poll_conversacion())

    yield

    tarea_conv.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarea_conv

    registro.info("cerrando el panel")
    if mqtt is not None:
        mqtt.desconectar()
    if base is not None:
        base.cerrar()


async def _poll_conversacion() -> None:
    global _ultimo_id_conversacion
    while True:
        try:
            if base is not None:
                filas = base.conversacion_desde(_ultimo_id_conversacion)
                for fila in filas:
                    entrada = _entrada_conversacion_json(fila, base)
                    estados.emitir({"tipo": "conversacion",
                                    "datos": {"entrada": entrada}})
                    if fila["id"] > _ultimo_id_conversacion:
                        _ultimo_id_conversacion = fila["id"]
        except Exception as error:  # noqa: BLE001
            registro.debug("poll conversacion: %s", error)
        await asyncio.sleep(2)


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
    """Canjea el código de acceso de 6 caracteres por un token de sesión.

    Es un POST y no un GET porque una vista previa de Telegram, un escáner del navegador
    o un antivirus siguen los enlaces, y con un GET cualquiera de ellos gastaría el
    código antes de que el familiar haya hecho nada (apartado 3.1).

    Dos límites, los dos de FALLOS y los dos en memoria: 5 por IP real cada 10 minutos y
    50 globales cada 10 minutos. El primero frena a quien prueba desde una casa; el
    segundo, a quien reparte el ataque entre muchas IPs, que es el que el primero no ve.
    Un canje correcto no gasta cupo.

    El fallo responde 401 con el MISMO texto para un código que no existe, uno caducado,
    uno ya gastado y uno de un familiar desactivado, y no dice cuál. Un «código no
    válido» seguido de «código ya usado» en el mismo mensaje permite ir probando y
    aprender de los aciertos.
    """
    ip = ip_real(request.client.host if request.client else "", request.headers)

    if not limite_codigo_global.permitido(CLAVE_GLOBAL):
        registro.warning("acceso bloqueado por el tope global")
        raise HTTPException(429, "Se han hecho demasiados intentos. Espere "
                                 "unos minutos y vuelva a intentarlo.")
    if not limite_acceso.permitido(ip):
        registro.warning("acceso bloqueado por limite desde %s", ip)
        raise HTTPException(429, "Se han hecho demasiados intentos. Espere "
                                 "unos minutos y vuelva a intentarlo.")

    codigo = cuerpo.codigo.strip()
    if len(codigo) >= 22:
        exito, fila = panel_acceso.tomar_codigo_invitado(base, codigo)
    else:
        exito, fila = panel_acceso.tomar_codigo_acceso(base, codigo)
    if not exito:
        limite_acceso.anotar_fallo(ip)
        limite_codigo_global.anotar_fallo(CLAVE_GLOBAL)
        registro.warning("canje fallido desde %s", ip)
        raise HTTPException(401, "Ese código no vale. Escríbale /panel al bot de "
                                 "la casa para pedir uno nuevo.")

    # Un familiar desactivado puede tener un código vivo generado antes de que se le
    # diera de baja. El token que se creara con él ya no valdría (la puerta del
    # panel mira `activo`), pero tampoco debe llegar a crearse: aquí se corta el
    # canje y se responde igual que a un código que no vale, para no revelar que
    # el familiar existe y está desactivado.
    if not base.familiar_activo(fila["chat_id"]):
        limite_acceso.anotar_fallo(ip)
        limite_codigo_global.anotar_fallo(CLAVE_GLOBAL)
        registro.warning("canje de un familiar no activo desde %s", ip)
        raise HTTPException(401, "Ese código no vale. Escríbale /panel al bot de "
                                 "la casa para pedir uno nuevo.")

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
            "caduca_dias": config.DIAS_TOKEN_PANEL,
            "admin": str(sesion["chat_id"]) in (config.PANEL_ADMINS or [])}


@app.get("/api/familiares")
async def familiares(sesion: dict = Depends(sesion_requerida)):
    """Los nombres de la casa, para la pantalla «Familiares» (decisión A.5).

    SOLO los activos, que son los que de verdad reciben alertas: enseñar a alguien
    que ya se dio de baja sería mentirle sobre quién se entera de una emergencia.

    El `chat_id` no sale de aquí, igual que en `/api/yo`: la pantalla de familia
    enseña nombres, y un número de chat no le significa nada a nadie. El que no
    tenga nombre guardado sale con cadena vacía y la interfaz lo pinta «Familiar»,
    que es la verdad sin inventarle apodo a nadie.
    """
    del sesion
    filas = base.familiares_activos()
    items = [{"nombre": (fila["nombre"] or "").strip(),
              "tiene_telegram": bool(fila["tiene_telegram"])}
             for fila in filas]
    return {"total": len(items), "familiares": items}



def _es_admin(sesion: dict) -> bool:
    return str(sesion["chat_id"]) in (config.PANEL_ADMINS or [])


def _exigir_admin(sesion: dict) -> None:
    if not _es_admin(sesion):
        raise HTTPException(403, "Solo un administrador puede hacer esto.")


class NuevoFamiliar(BaseModel):
    model_config = {"extra": "forbid"}

    nombre: str = Field(min_length=1, max_length=40)
    con_telegram: bool = True


@app.post("/api/familiares")
async def agregar_familiar(cuerpo: NuevoFamiliar,
                          sesion: dict = Depends(sesion_requerida)):
    _exigir_admin(sesion)
    nombre = cuerpo.nombre.strip()
    if not (1 <= len(nombre) <= 40):
        raise HTTPException(422, "El nombre debe tener entre 1 y 40 caracteres.")
    admin = _nombre_de(sesion) or "Ilse"
    if cuerpo.con_telegram:
        import registro as registro_modulo
        codigo = registro_modulo.generar_codigo(
            registro_modulo._ruta_por_defecto(), minutos=24 * 60)
        base.crear_aviso_conversacion(
            admin, sesion["chat_id"], "panel",
            "%s agregó a %s" % (admin, nombre))
        return {"ok": True, "tipo": "telegram", "nombre": nombre,
                "codigo": codigo,
                "instruccion": "Envíe al bot: /registro %s" % codigo}
    # Sin Telegram: familiar invitado.
    import uuid
    chat_id = "invitado:" + uuid.uuid4().hex
    base.registrar_familiar(chat_id, nombre=nombre, tiene_telegram=0)
    codigo = panel_acceso.crear_codigo_invitado(base, chat_id)
    enlace = "https://asistente.tail722206.ts.net/#codigo=%s" % codigo
    base.crear_aviso_conversacion(
        admin, sesion["chat_id"], "panel",
        "%s agregó a %s" % (admin, nombre))
    return {"ok": True, "tipo": "invitado", "nombre": nombre,
            "chat_id": chat_id, "codigo": codigo, "enlace": enlace}


@app.post("/api/familiares/{identificador}/desactivar")
async def desactivar_familiar(identificador: str,
                              sesion: dict = Depends(sesion_requerida)):
    _exigir_admin(sesion)
    # Buscar por chat_id sintético o por nombre.
    objetivo = None
    for fila in base.familiares_registrados():
        if str(fila["chat_id"]) == identificador or (fila["nombre"] or "").strip() == identificador.strip():
            objetivo = fila
            break
    if objetivo is None:
        raise HTTPException(404, "No se encontró ese familiar.")
    chat_id = str(objetivo["chat_id"])
    if chat_id == str(sesion["chat_id"]):
        raise HTTPException(403, "No puede desactivarse a sí mismo.")
    if not base.marcar_familiar_inactivo(chat_id):
        raise HTTPException(409, "Ese familiar ya estaba desactivado.")
    base.revocar_tokens_panel_de(chat_id)
    admin = _nombre_de(sesion) or "Ilse"
    nombre = (objetivo["nombre"] or "").strip() or "Familiar"
    base.crear_aviso_conversacion(
        admin, sesion["chat_id"], "panel",
        "%s desactivó a %s" % (admin, nombre))
    return {"ok": True, "nombre": nombre}

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


@app.post("/api/inactividad")
async def ajustar_inactividad(cuerpo: TiempoInactividad,
                              sesion: dict = Depends(sesion_requerida)):
    """Cambia los minutos de inactividad. Ajuste en caliente (Fase 4).

    El panel NO se lo dice al orquestador por MQTT ni lo reinicia: escribe el valor
    en la misma base que los dos comparten, y el orquestador lo relee en su ciclo
    de 10 s. Ese es el motivo de que este endpoint no publique nada y de que el
    cambio se note «en la siguiente vuelta» y no al instante.

    Devuelve el valor que quedó, que es `cuerpo.tiempo_min`: `db` lo revalida (misma
    franja) y solo puede lanzar si el tipo y la ruta se hubieran desincronizado, así
    que ese `ValueError` se traduce a un 422 de todas formas en vez de a un 500.
    """
    try:
        minutos = base.guardar_tiempo_inactividad(cuerpo.tiempo_min)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    registro.info("%s ajusta la inactividad a %d min",
                  db.enmascarar_chat(sesion["chat_id"]), minutos)
    return {"tiempo_min": minutos}


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
    # `resuelto_por` puede faltar en la fila a medias de `_con_estado` (una alerta
    # que no sale en las 100 más recientes), por eso se busca sin lanzar.
    if isinstance(fila, dict):
        resuelto_por = fila.get("resuelto_por")
    elif "resuelto_por" in fila.keys():
        resuelto_por = fila["resuelto_por"]
    else:
        resuelto_por = None
    return {
        "id": fila["id"],
        "tipo": fila["tipo"],
        "estado": fila["estado"],
        "creado_en": fila["creado_en"],
        "carga": carga,
        "resuelta_en": fila["resuelto_en"],
        "resuelto_por": resuelto_por,
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
    if not alertas_modulo.id_valido(id_alerta):
        raise HTTPException(404, "No se encontró esa alerta.")

    fila, fresca = alertas_modulo.resolver(
        base, mqtt.publicar if mqtt else None, id_alerta,
        quien=_nombre_de(sesion) or db.enmascarar_chat(sesion["chat_id"]),
        origen="el panel",
        # El NOMBRE («Ana») es lo que queda en `resuelto_por` para la linea de
        # tiempo del panel; el chat_id solo se usa en el log de arriba.
        nombre_quien=_nombre_de(sesion),
    )
    if fila is None:
        raise HTTPException(404, "No se encontró esa alerta.")

    respuesta = _alerta_json(_con_estado(fila))
    respuesta["fresca"] = fresca
    return respuesta


@app.get("/api/alertas/{id_alerta}")
async def detalle_alerta(id_alerta: str, sesion: dict = Depends(sesion_requerida)):
    """Una alerta con su línea de tiempo «Qué pasó» (decisión B.3).

    401 sin token (la dependencia) y 404 si la alerta no existe, igual que en
    `atender`. El `chat_id` no aparece en ninguna parte de la respuesta: los
    eventos llevan el NOMBRE del familiar (`familiares.nombre`), y si no hay
    nombre, «Familiar».

    **Los eventos son SOLO hechos reales de la base**, nada calculado ni
    imaginado: cuándo se creó, qué avisos salieron de verdad (`enviado_en`),
    qué avisos siguen esperando y cuándo quién la atendió. Una línea de tiempo
    que rellena horas con «ahora más una» enseña cosas que no pasaron.
    """
    del sesion
    if not alertas_modulo.id_valido(id_alerta):
        raise HTTPException(404, "No se encontró esa alerta.")
    fila = base.obtener_alerta(id_alerta)
    if fila is None:
        raise HTTPException(404, "No se encontró esa alerta.")

    eventos = [{"tipo": "creada", "en": fila["creado_en"]}]
    for envio in base.envios_de_alerta(id_alerta):
        # Sin nombre guardado, «Familiar»: un `chat_id` no va a una pantalla
        # de familia, y el envío sí ocurrió, así que el evento no se omite.
        nombre = (envio["nombre"] or "").strip() or "Familiar"
        if envio["enviado_en"]:
            eventos.append({"tipo": "enviada", "en": envio["enviado_en"],
                            "nombre": nombre})
        else:
            eventos.append({"tipo": "pendiente", "en": envio["creado_en"],
                            "nombre": nombre})
    if fila["resuelto_en"]:
        eventos.append({"tipo": "atendida", "en": fila["resuelto_en"],
                        "nombre": (fila["resuelto_por"] or "").strip() or None})
    if fila["descartada_en"]:
        eventos.append({"tipo": "descartada", "en": fila["descartada_en"]})
    # Orden por hora; con horas iguales manda el orden en que se añadieron
    # (`sorted` es estable), que es el en que pasaron las cosas.
    eventos.sort(key=lambda evento: evento["en"])

    return {"alerta": _alerta_json(_con_estado(fila)), "eventos": eventos,
            "servidor_hora": int(time.time())}


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
                "resuelto_por": (fila["resuelto_por"]
                                 if "resuelto_por" in fila.keys() else None),
                "descartada_en": fila["descartada_en"], "enviados": 0, "sin_mandar": 0}
    return dict(completas[0])


# --- Los recordatorios -------------------------------------------------------


# Lo que puede llevar el texto de un recordatorio: letras (de cualquier alfabeto,
# con acentos), dígitos, espacios y los signos de uso común. `[^\W_]` es «carácter
# de palabra salvo guion bajo» y la alternativa añade los signos permitidos, así
# que los emojis y los símbolos raros quedan fuera; el guion bajo también, a
# propósito.
_TEXTO_RECORDATORIO = re.compile(r"^(?:[^\W_]|[ .,;:¡!¿?()'\"\-])+$", re.UNICODE)

# Los días ISO: 1 = lunes ... 7 = domingo, como `datetime.isoweekday`.
DIAS_ISO = frozenset(range(1, 8))


def _hora_a_minutos(hora: str) -> int:
    """'HH:MM' a minutos desde medianoche.

    El patrón de `NuevoRecordatorio` ya garantiza el formato, así que aquí no hay
    nada que validar: solo la conversión.
    """
    horas, minutos = hora.split(":")
    return int(horas) * 60 + int(minutos)


def _minutos_a_hora(hora_min: int) -> str:
    """Minutos desde medianoche a 'HH:MM', la forma que ve la pantalla."""
    return "%02d:%02d" % (hora_min // 60, hora_min % 60)


def _fecha_valida(fecha: str) -> bool:
    """Si `fecha` es un 'YYYY-MM-DD' con una fecha real (no un 31 de febrero)."""
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _clave(fila, nombre):
    """Una columna de `listar_recordatorios`, o `None` si la fila no la trae.

    `obtener_recordatorio` es un `SELECT *` y no trae las columnas derivadas de la
    última entrega que sí trae `listar_recordatorios`; así una sola función de
    respuesta sirve para las dos consultas.
    """
    return fila[nombre] if nombre in fila.keys() else None


def _recordatorio_json(fila) -> dict:
    """Un recordatorio en la forma de la API (apartado 3).

    `ultima_entrega` es como mucho UNA línea: «se dijo» con el momento en que se
    dijo, o «no se pudo decir» con el momento en que tocaba. No se manda el
    historial entero, que la pantalla no lo muestra.
    """
    estado = _clave(fila, "ultimo_estado")
    if not estado:
        ultima = None
    elif estado == "entregado":
        ultima = {"estado": estado, "cuando": _clave(fila, "ultimo_entregado_en")}
    else:
        ultima = {"estado": estado, "cuando": _clave(fila, "ultimo_toca_en")}
    return {
        "id": fila["id"],
        "texto": fila["texto"],
        "hora": _minutos_a_hora(fila["hora_min"]),
        "repeticion": fila["repeticion"],
        "fecha": fila["fecha"],
        "dias": [int(p) for p in fila["dias"].split(",")] if fila["dias"] else [],
        "proximo_en": fila["proximo_en"],
        "activo": bool(fila["activo"]),
        "creado_en": fila["creado_en"],
        "creado_por": _nombre_de_creador(fila["creado_por"]),
        "cancelado_en": fila["cancelado_en"],
        "hecho_en": fila["hecho_en"],
        "ultima_entrega": ultima,
    }


def _nombre_de_creador(chat_id) -> str:
    """El nombre del familiar que creó el recordatorio, enmascarado si no lo hay.

    Mismo criterio que `_nombre_de`: un `chat_id` completo no sale a la interfaz,
    y si el familiar todavía no tiene nombre se manda el enmascarado.
    """
    return base.nombre_de(chat_id) or db.enmascarar_chat(chat_id)


@app.get("/api/recordatorios")
async def listar_recordatorios(sesion: dict = Depends(sesion_requerida)):
    """Los recordatorios activos, el que antes toca primero (apartado 3).

    Solo los activos: lo cancelado o ya dicho no va a volver a sonar, y la pantalla
    enseña «qué va a pasar», no un historial. Las filas siguen en la base hasta la
    purga de los 90 días, pero eso es cosa del orquestador, no de aquí.
    """
    del sesion
    filas = base.listar_recordatorios()
    return {"recordatorios": [_recordatorio_json(f) for f in filas],
            "servidor_hora": int(time.time())}


@app.post("/api/recordatorios", status_code=201)
async def crear_recordatorio(cuerpo: NuevoRecordatorio,
                             sesion: dict = Depends(sesion_requerida)):
    """Crea un recordatorio. 201 con lo creado (apartado 3).

    Las reglas se comprueban aquí y no en el tipo: dependen de `repeticion` y así
    el 422 puede decir en palabras de usted qué falta. Un `una_vez` con la fecha y
    la hora ya pasadas es un 422, no un recordatorio que nunca sonaría.
    """
    texto = cuerpo.texto.strip()
    if not texto:
        raise HTTPException(422, "El texto del recordatorio no puede estar vacío.")
    if not _TEXTO_RECORDATORIO.match(texto):
        raise HTTPException(
            422, "El texto solo puede llevar letras, números, espacios y los "
                 "signos . , ; : ¡ ! ¿ ? ( ) ' \" -")

    hora_min = _hora_a_minutos(cuerpo.hora)
    fecha = None
    dias = None
    if cuerpo.repeticion == "una_vez":
        if not cuerpo.fecha:
            raise HTTPException(422, "Elija el día del recordatorio.")
        if not _fecha_valida(cuerpo.fecha):
            raise HTTPException(422, "El día elegido no es válido.")
        if cuerpo.dias:
            raise HTTPException(422, "Un recordatorio de una sola vez no lleva días.")
        fecha = cuerpo.fecha
    elif cuerpo.repeticion == "diario":
        if cuerpo.fecha or cuerpo.dias:
            raise HTTPException(422, "Un recordatorio de todos los días no lleva "
                                     "día ni días de la semana.")
    else:  # semanal
        if cuerpo.fecha:
            raise HTTPException(422, "Un recordatorio de ciertos días no lleva fecha.")
        if not cuerpo.dias:
            raise HTTPException(422, "Elija al menos un día de la semana.")
        if any(dia not in DIAS_ISO for dia in cuerpo.dias):
            raise HTTPException(422, "Los días van del 1 (lunes) al 7 (domingo).")
        dias = ",".join(str(dia) for dia in sorted(set(cuerpo.dias)))

    ahora = int(time.time())
    plantilla = {"hora_min": hora_min, "repeticion": cuerpo.repeticion,
                 "fecha": fecha, "dias": dias}
    proximo = recordatorios.siguiente_vez(plantilla, ahora)
    if proximo is None:
        raise HTTPException(422, "Esa fecha y hora ya pasaron.")

    id_rec = "rec-" + secrets.token_hex(8)
    base.crear_recordatorio(id_rec, texto, hora_min, cuerpo.repeticion, fecha,
                            dias, sesion["chat_id"], proximo, ahora=ahora)
    registro.info("recordatorio %s creado por %s", id_rec,
                  db.enmascarar_chat(sesion["chat_id"]))
    return _recordatorio_json(base.obtener_recordatorio(id_rec))


@app.post("/api/recordatorios/{id_rec}/cancelar")
async def cancelar_recordatorio(id_rec: str, sesion: dict = Depends(sesion_requerida)):
    """Cancela un recordatorio. Idempotente (apartado 3).

    Igual que `/api/alertas/{id}/atender`: si ya estaba cancelado devuelve 200 con
    `"fresca": false` y no un 409, porque a quien pulsa le da igual. Un
    identificador que no existe es un 404.
    """
    if not alertas_modulo.id_valido(id_rec):
        raise HTTPException(404, "No se encontró ese recordatorio.")
    if base.obtener_recordatorio(id_rec) is None:
        raise HTTPException(404, "No se encontró ese recordatorio.")
    fresca = base.cancelar_recordatorio(id_rec)
    if fresca:
        registro.info("recordatorio %s cancelado por %s", id_rec,
                      db.enmascarar_chat(sesion["chat_id"]))
    return {"ok": True, "fresca": fresca}


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


# --- La página del enlace antiguo --------------------------------------------

# El enlace de un solo uso se sustituyó por un código de 6 caracteres (apartado 3.1).
# Esta página ya NO canjea nada: avisa de que el enlace no vale y de dónde se saca el
# código. Se sigue sirviendo para que un enlace viejo, guardado en un chat o en los
# marcadores de alguien, no caiga en un 404 sin explicación.
PAGINA_ENLACE_ANTIGUO = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex, nofollow">
<title>El enlace ya no funciona</title>
<style>
  :root { color-scheme: light; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font-family: system-ui, -apple-system, sans-serif; background:#F4F5F2;
         color:#1B1F1E; }
  main { max-width:26rem; padding:2rem; text-align:center; }
  h1 { font-size:1.3rem; margin:0 0 1rem; font-weight:600; }
  p { line-height:1.5; color:#4A524F; }
  a { color:#0E5E5A; font-weight:600; }
</style>
</head>
<body>
<main>
  <h1>El enlace de acceso ya no funciona</h1>
  <p>Ahora el panel se abre con un código de seis caracteres. Escriba <b>/panel</b>
     al bot de la casa en Telegram y le enviará el suyo; vale diez minutos y solo
     una vez. Después abra el panel y escríbalo ahí.</p>
  <p><a href="/">Ir al panel</a></p>
</main>
</body>
</html>
"""


@app.get("/acceso/{codigo}", response_class=HTMLResponse)
async def pagina_acceso(codigo: str):
    """El aviso del enlace antiguo: HTML mínimo, sin Flutter (apartado 3.1).

    Va declarada ANTES que el montaje de los ficheros estáticos a propósito. Con el
    `StaticFiles` montado en `/`, una ruta como `/acceso/abc` acabaría resoliéndose
    contra el build y el aviso no se vería. No lee el `codigo` de la URL: venga lo
    que venga, la respuesta es la misma, así que no hay nada que inyectar ni que
    escapar (`_js_seguro` se fue con el canje).
    """
    return HTMLResponse(PAGINA_ENLACE_ANTIGUO,
                        headers={"Referrer-Policy": "no-referrer",
                                 "X-Robots-Tag": "noindex, nofollow"})




# --- La conversacion ---------------------------------------------------------


class Hablar(BaseModel):
    model_config = {"extra": "forbid"}

    accion: str = Field(pattern="^(inicio|fin)$")


@app.post("/api/hablar", status_code=202)
async def hablar(cuerpo: Hablar, sesion: dict = Depends(sesion_requerida)):
    """Botón «Mantener para hablar» del panel: avisa al satélite por MQTT."""
    global _hablar_hasta
    import threading as _threading
    ahora = int(time.time())
    nombre = _nombre_de(sesion) or "Familiar"
    client_id = config.SATELITE_HABLAR
    satelites = estados.instantanea(base).get("satelite", {}).get("satelites", {})
    if not satelites.get(client_id, False):
        raise HTTPException(409, "El satélite no está conectado.")
    with _hablar_lock:
        if cuerpo.accion == "inicio":
            if time.time() < _hablar_hasta:
                raise HTTPException(409, "Ya hay una pulsación activa.")
            _hablar_hasta = time.time() + 10.0
            _threading.Timer(10.0, _limpiar_hablar).start()
        else:
            _hablar_hasta = 0.0
    topico = "casa/sala/satelite/%s/hablar" % client_id
    mqtt.publicar(topico, {"accion": cuerpo.accion, "ts": ahora}, qos=1,
                  retain=False)
    if cuerpo.accion == "inicio":
        base.crear_aviso_conversacion(
            nombre, sesion["chat_id"], "panel",
            "%s activó el micrófono de la sala" % nombre, ahora=ahora)
    return {"ok": True}


def _limpiar_hablar() -> None:
    global _hablar_hasta
    with _hablar_lock:
        _hablar_hasta = 0.0


@app.get("/api/conversacion")
async def conversacion_listar(sesion: dict = Depends(sesion_requerida),
                              limite: int = Query(50, ge=1, le=200),
                              antes_id: int = Query(0, ge=0)):
    del sesion
    filas, hay_mas = base.listar_conversacion(limite=limite,
                                              antes_id=antes_id if antes_id else None)
    entradas = [_entrada_conversacion_json(f, base) for f in filas]
    return {"entradas": entradas, "hay_mas": hay_mas,
            "servidor_hora": int(time.time())}


class MensajeConversacion(BaseModel):
    model_config = {"extra": "forbid"}
    texto: str = Field(min_length=1, max_length=config.LIMITE_CHAT_CARACTERES)


@app.post("/api/conversacion", status_code=201)
async def conversacion_enviar(cuerpo: MensajeConversacion,
                              sesion: dict = Depends(sesion_requerida)):
    texto = cuerpo.texto.strip()
    if not texto:
        raise HTTPException(422, "El texto no puede estar vacío.")
    if len(texto) > config.LIMITE_CHAT_CARACTERES:
        raise HTTPException(422, "El mensaje no puede pasar de %d caracteres."
                                 % config.LIMITE_CHAT_CARACTERES)
    ahora = int(time.time())
    ultima = base.ultimo_mensaje_de(sesion["chat_id"])
    if ultima is not None and ahora - int(ultima["creado_en"]) < config.PAUSA_CHAT_S:
        raise HTTPException(429, "Solo puede enviar un mensaje cada 5 segundos.")
    nombre = _nombre_de(sesion) or "Familiar"
    id_conv = base.crear_mensaje_conversacion(
        nombre, sesion["chat_id"], "panel", texto, ahora=ahora)
    fila = base.obtener_conversacion(id_conv)
    return _entrada_conversacion_json(fila, base)


# --- El panel compilado ------------------------------------------------------

app.mount("/", StaticFiles(directory=RAIZ_WEB, html=True), name="web")


# --- El arranque -------------------------------------------------------------




def _entrada_conversacion_json(fila, base) -> dict:
    entrada = {
        "id": fila["id"],
        "creado_en": fila["creado_en"],
        "tipo": fila["tipo"],
        "autor": fila["autor"],
        "origen": fila["origen"],
        "texto": fila["texto"],
        "estado": fila["estado"],
        "estado_texto": _estado_conversacion_texto(fila["estado"]),
    }
    if fila["tipo"] == "alerta" and fila["alerta_id"]:
        alerta = base.obtener_alerta(fila["alerta_id"])
        if alerta is not None:
            entrada["alerta"] = _alerta_json(_con_estado(alerta))
            entrada["alerta_id"] = fila["alerta_id"]
    return entrada


def _estado_conversacion_texto(estado) -> str:
    if estado == "se_dijo":
        return "Se dijo en la sala"
    if estado == "no_se_pudo_decir":
        return "No se pudo decir"
    if estado == "enviado":
        return "Enviado"
    return ""



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
    if not (config.PANEL_ADMINS or []):
        registro.warning("PANEL_ADMINS vacio: el panel arranca sin administradores")
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