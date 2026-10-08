"""Los dos secretos del panel web: el enlace de un solo uso y el token de sesión.

Vive aquí, y no en el bot ni en la API, porque lo necesitan LOS DOS y en procesos
distintos: el bot genera el enlace que manda en Telegram (`/panel`) y la API es la
que lo canjea. Si el hash viviera en uno de los dos, el otro tendría que
recalcularlo a su manera y un cambio en uno dejaría al otro aceptando o rechazando
cosas distintas sin que ninguna prueba se entere.

Y vive en `orquestador/` porque la imagen del panel monta esta misma carpeta en
`/app` (ver `panel/Dockerfile`), así que `import panel_acceso` funciona igual en los
dos contenedores sin instalar nada.

## Qué se guarda y qué no

- El **enlace** es el código de 16 caracteres. Se guarda SOLO su sha256: lo que
  está en SQLite no sirve para entrar al panel. El código en claro sale una vez,
  dentro del botón de Telegram, y a partir de ahí no existe en ninguna parte del
  servidor.
- El **token** de sesión son 32 bytes de `secrets`. También va solo hasheado, por el
  mismo motivo: un `SELECT` sobre la tabla no debe entregar sesiones usables, y el
  volumen de la base no es un almacén de credenciales.

Con el sha256 y un código de 16 caracteres de un alfabeto de 31 (unos 8x10^23
combinaciones) no hay fuerza bruta que valga. El límite de 5 intentos por código que
hay en `db.tomar_enlace_panel` no es la defensa: está para que un cambio futuro en el
alfabeto, o un endpoint que admita más de un intento sobre un código conocido, no se
quede sin techo.

## Por qué el canje es un POST y no un GET

Lo hace el otro lado, la página de canje (`/acceso/<codigo>`): abre el enlace con un
GET y luego canjea con un POST a `/api/acceso`. Una vista previa de Telegram, un
escáner del navegador o un antivirus siguen los enlaces, y con un GET cualquiera de
ellos gastaría el código de un solo uso antes de que el familiar haya hecho nada.

## Los plazos

- El **enlace**, 10 minutos. Es lo que ya dura un código de registro, y es lo que
  hace que un enlace olvidado en una conversación no sirva para siempre.
- El **token**, 30 días (`config.DIAS_TOKEN_PANEL`). Es el plazo que aprobó el
  usuario, y coincide con el del historial de alertas: es lo que un familiar aguanta
  sin volver a hacer `/panel`. Caduca solo por tiempo, nunca por usarlo: un token que
  se renueva cada vez que lo tocas es un token que no caduca nunca.
"""

import hashlib
import logging
import secrets
import time

import config
import registro as registro_modulo

registro = logging.getLogger("panel-acceso")

# El mismo alfabeto y la misma longitud que el código de registro, y por el mismo
# motivo: se teclea y se dicta, así que no lleva 0/O ni 1/l/I. No es un alfabeto
# distinto porque no hay ninguna razón para que lo sea.
LONGITUD_CODIGO = registro_modulo.LONGITUD_CODIGO
ALFABETO = registro_modulo.ALFABETO


def hash_de(valor: str) -> str:
    """El sha256 en hexadecimal de un código o de un token.

    Sale de `hashlib`, no de un `hash()` de Python: `hash()` cambia entre
    reinicios del proceso, así que un token guardado con él dejaría de valer cada
    vez que se reiniciara el contenedor.
    """
    return hashlib.sha256(str(valor).encode("utf-8")).hexdigest()


def nuevo_codigo() -> str:
    """Un código de un solo uso, en claro. No se guarda en ningún sitio.

    El que lo llama lo mete en el enlace y lo guarda solo hasheado con
    `crear_enlace`. No se escribe en el log ni en la base: `docker logs` se lee con
    cualquiera que tenga acceso al servidor, y un código en claro ahí es una puerta
    al panel de la familia.
    """
    return "".join(secrets.choice(ALFABETO) for _ in range(LONGITUD_CODIGO))


def nuevo_token() -> str:
    """Un token de sesión, en claro. Tampoco se guarda: solo su hash.

    Son 32 bytes de `secrets`, que son 256 bits: no se pueden adivinar ni aunque el
    alfabeto del enlace se pudiera recorrer entero.

    `token_urlsafe` y no `token_hex` a propósito. `token_urlsafe(32)` da 43 caracteres
    en base64url, y esos 43 caracteres son los 32 bytes, no una codificacion mas corta
    de menos bytes: la entropia es la misma en los dos casos. Se elige base64url porque
    el token viaja en una cabecera `Authorization: Bearer ...` y en la query del
    WebSocket, y base64url no trae `+`, `/` ni `=`; en la query, un `+` llega al
    servidor como un espacio y el token se rechazaria sin error claro, y un `/` dentro
    de una query es un temporal de la URL.

    Hex son 64 caracteres en vez de 43 y no aporta nada aqui.
    """
    return secrets.token_urlsafe(32)


def crear_enlace(base, chat_id: str, ahora: int = None) -> str:
    """Crea un enlace de un solo uso para este familiar y devuelve el código.

    El plazo es el de `registro.SEGUNDOS_CODIGO` (10 minutos) porque es el mismo
    tipo de cosa: algo que el operador, o el bot, genera y que vale un rato corto.
    No se reusa la constante de registro para el panel por comodidad, se usa a
    propósito: si un día el plazo del registro cambia a otra cosa, el del panel tiene
    que seguir siendo el que se aprobó, y por eso tiene su propia constante aquí.

    `chat_id` se guarda junto al hash, y es lo que permite revocar el acceso de una
    persona concreta sin recorrer nada más.
    """
    if ahora is None:
        ahora = int(time.time())
    codigo = nuevo_codigo()
    base.crear_enlace_panel(
        hash_de(codigo), chat_id, ahora + registro_modulo.SEGUNDOS_CODIGO, ahora
    )
    return codigo


def crear_token(base, chat_id: str, ahora: int = None) -> tuple:
    """Crea una sesión para este familiar. Devuelve `(token, caduca_en)`.

    El token en claro se devuelve una sola vez, al proceso que lo canjea, y ese lo
    manda al navegador. Lo que queda en la base es su hash.
    """
    if ahora is None:
        ahora = int(time.time())
    token = nuevo_token()
    caduca_en = ahora + config.DIAS_TOKEN_PANEL * 86400
    base.crear_token_panel(hash_de(token), chat_id, caduca_en, ahora)
    return token, caduca_en


def canjear(base, codigo: str, ahora: int = None) -> tuple:
    """Canjea un código de un solo uso. Devuelve `(ok, fila)`.

    Todo el trabajo (comprobar que existe, que no caducó, que no se gastó y el `UPDATE`
    atómico) lo hace `db.tomar_enlace_panel`, que es quien decide. Aquí solo se hashea
    el código y se devuelve lo que salió, sin decidir nada: si el canje se atomizara
    también aquí, habría dos sitios donde comprobar la condición y el día que uno se
    olvidara de la condición del `UPDATE` se abrirían dos sesiones del mismo enlace.

    La fila que sale lleva el `chat_id` del familiar y nada más del código: el código
    en claro no está en ella, porque en la tabla solo se guardó su hash.
    """
    if ahora is None:
        ahora = int(time.time())
    fila = base.tomar_enlace_panel(hash_de(codigo), ahora)
    return (fila is not None), (fila or {})


def revocar(base, token_hash: str, ahora: int = None) -> bool:
    """Cierra UNA sesión. `True` si estaba viva.

    Revoca solo el token que le pasan, no todos los del familiar: si una misma persona
    tiene el panel abierto en el móvil y en el portátil, cerrar sesión en uno no
    debería cerrar el otro. Para cerrar todos los de una persona está
    `db.revocar_token_panel(chat_id)`, que es la operación del operador.
    """
    if ahora is None:
        ahora = int(time.time())
    return base.revocar_token_panel(token_hash, ahora)


def enlace_de(config_panel_url: str, codigo: str) -> str:
    """La URL completa del botón: `PANEL_URL + /acceso/ + código`.

    `PANEL_URL` tiene que ser la COMPLETA de Tailscale, con dominio, porque Telegram
    rechaza en el envío un botón con una URL sin dominio («wrong http url»). Comprobado
    el 2026-10-05 mandando los dos botones de verdad: el nombre corto da HTTP 400 y el
    completo HTTP 200.
    """
    return "%s/acceso/%s" % (config_panel_url.rstrip("/"), codigo)


def ahora() -> int:
    """El reloj, en un solo sitio, para que las pruebas puedan compararlo."""
    return int(time.time())