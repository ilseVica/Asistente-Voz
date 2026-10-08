"""La puerta del panel: quién entra y con qué.

Tres cosas, y ninguna es una decisión de la interfaz:

1. **Bearer, no cookie.** `Authorization: Bearer <token>` en cada petición. La API la
   consume igual la web y la APK, y sin cookie no hay CSRF que pensar. El token va en
   el `localStorage` del navegador (a través de `shared_preferences`), que es por
   origen: una sesión abierta en `villaca.tail722206.ts.net` no vale en
   `192.168.1.169`, y por eso la pantalla de acceso canjea siempre en el origen donde
   el panel está abierto.
2. **Una comprobación por petición, y son cuatro.** Que el hash exista, que el token
   no esté revocado, que no haya caducado y que su `chat_id` siga en `familiares`.
   La cuarta es la que hace que `dar_de_baja()` cierre el panel: aunque se olvidara de
   borrar los tokens, la puerta se sigue cerrando.
3. **`ultimo_uso_en` no se escribe en cada petición.** Se anota solo cuando lo
   guardado tiene más de una hora, para no meter una escritura en SQLite en cada
   `GET /api/estado` de cada móvil abierto.

## Lo que NO se registra

Ni el token ni el código, ni en el log de la aplicación ni en el de accesos. El token
viaja en la query del WebSocket (`/api/vivo?token=...`, porque el navegador no puede
mandar cabeceras en un WebSocket) y con el log de accesos activado saldría entero en
cada conexión. Por eso uvicorn arranca con el log de accesos apagado y el servicio
lleva su propio log de lo que importa: accesos, 401, canjes, altas y bajas.

La alternativa, mandar el token en `Sec-WebSocket-Protocol`, existe y
`WebSocketChannel.connect` la admite, pero mete el token en una cabecera que algunos
proxies tocan. Se deja anotada y no se usa.

## El límite por IP de `/api/acceso`

20 fallos en 10 minutos, en memoria. No es la defensa del código —16 caracteres de un
alfabeto de 31 son unos 8x10^23 combinaciones— sino una red de seguridad para que un
fallo de programación en ese endpoint no se convierta en un vector. Vive aquí y no en
el proceso del bot porque es el único que expone el canje.
"""

import hashlib
import logging
import time

import config
import panel_acceso

registro = logging.getLogger("panel-seguridad")


def token_de_cabecera(cabecera: str):
    """El token de `Authorization: Bearer <token>`, o `None`.

    Acepta el prefijo `Bearer` en cualquier capitalización porque lo escriben así los
    navegadores, `curl --oauth2-bearer` y las librerías, y no tiene sentido que la
    diferencia sea un 401.
    """
    if not cabecera:
        return None
    partes = cabecera.split(None, 1)
    if len(partes) != 2 or partes[0].lower() != "bearer":
        return None
    token = partes[1].strip()
    return token or None


class LimiteAccesos:
    """Fallos de canje por IP, con ventana deslizante y en memoria.

    En memoria a propósito, y no en SQLite: si el proceso se reinicia se pierde el
    contador, y eso es correcto porque el contador solo sirve para frenar a quien está
    fallando ahora mismo. Ponerlo en la base sería escribir en la tabla compartida
    con el orquestador para guardar algo que nadie consulta.

    La ventana es fija (los fallos que caen fuera de la ventana se cuentan y se
    olvidan), que es lo bastante justo para un límite de este tamaño y evita tener
    que guardar marcas de tiempo de cada intento.
    """

    def __init__(self, maximo: int = None, segundos: int = None):
        self.maximo = maximo if maximo is not None else config.INTENTOS_ACCESO_POR_IP
        self.segundos = (segundos if segundos is not None
                         else config.SEGUNDOS_LIMITE_ACCESO)
        self._fallos = {}
        # Desde cuándo se está contando cada IP. Si entre el fallo N y el N+1 pasa la
        # ventana entera, el contador vuelve a cero: 20 canjes fallidos seguidos en 10
        # minutos bloquean, y 20 repartidos en una hora no.
        self._desde = {}

    def _podido(self, ip: str, ahora: int) -> bool:
        return self._fallos.get(ip, 0) < self.maximo

    def permitido(self, ip: str, ahora: int = None) -> bool:
        if ahora is None:
            ahora = int(time.time())
        self._olvidar(ip, ahora)
        return self._podido(ip, ahora)

    def anotar_fallo(self, ip: str, ahora: int = None) -> None:
        if ahora is None:
            ahora = int(time.time())
        self._fallos[ip] = self._fallos.get(ip, 0) + 1

    def anotar_exito(self, ip: str) -> None:
        self._fallos.pop(ip, None)
        self._desde.pop(ip, None)

    def _olvidar(self, ip: str, ahora: int) -> None:
        # La ventana empieza en el primer fallo de esa IP y NO se reinicia con cada
        # intento, que es lo que hace que 20 canjes seguidos en 10 minutos bloqueen y
        # 20 en una hora no.
        #
        # Se limpia al mirar y no con un temporizador: son unas pocas IPs y un
        # proceso de cuatro hilos. Y se borra la IP entera, no solo el contador, para
        # que el diccionario no crezca con las que ya no cuentan.
        if ip not in self._desde:
            self._desde[ip] = ahora
            return
        if ahora - self._desde[ip] > self.segundos:
            self._fallos.pop(ip, None)
            self._desde[ip] = ahora


def sesion_de(base, token: str, ahora: int = None):
    """La fila de la sesión si el token vale ahora mismo, o `None`.

    Delega el "¿vale esta sesión?" en `db.sesion_panel`, que es la misma pregunta que
    se le hace a la tabla y en la que ya está la condición de `familiares`. Aquí solo
    se anota el último uso, y solo de vez en cuando.
    """
    if not token:
        return None
    if ahora is None:
        ahora = int(time.time())
    fila = base.sesion_panel(panel_acceso.hash_de(token), ahora)
    if fila is None:
        return None
    _anotar_uso(base, fila, ahora)
    return fila


def _anotar_uso(base, fila, ahora: int) -> None:
    """Escribe `ultimo_uso_en` como mucho una vez por hora.

    El dato va para que el operador vea «llegó a usarlo el martes», no para decidir
    nada. Escribirlo en cada petición convertiría un `GET` de solo lectura en una
    escritura en la base compartida con el orquestador.
    """
    ultimo = fila.get("ultimo_uso_en")
    if ultimo is not None and ahora - ultimo < 3600:
        return
    try:
        base.marcar_token_usado(fila["token_hash"], ahora)
    except Exception as error:  # noqa: BLE001
        # Esto es un dato de cortesía. Si no se puede escribir, la sesión sigue
        # valiendo: el token no caduca por no anotar la hora.
        registro.warning("no se pudo anotar el ultimo uso de una sesion: %s",
                         sanear_error(error))


def sanear_error(error) -> str:
    """El texto de un error, sin secretos.

    El import va dentro de la función porque este módulo lo usan el bot y la API, y
    `sanear` importa `config`: dejarlo arriba lo pondría en el camino de importación
    de todo el servicio para tapar el error de una línea.
    """
    import sanear

    return sanear.sanear(error)


def huella(token: str) -> str:
    """Los seis primeros del sha256, para poder hablar de un token sin decirlo.

    Es lo que se usa si alguna vez hay que distinguir dos sesiones en un log: dos
    huellas iguales son el mismo token, y distintas son tokens distintos.
    """
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:6]