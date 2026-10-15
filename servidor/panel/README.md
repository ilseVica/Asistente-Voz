# Panel web de los familiares

Servicio HTTP y WebSocket que dibuja el estado de la casa y deja actuar sobre lo
poco que un familiar puede actuar: la luz y confirmar que ya se atiende una alerta.

No es un proceso aparte con sus propias reglas: comparte con el orquestador la base de
datos SQLite, el modulo de alertas y el de acceso. Son dos procesos distintos con una
base compartida, no dos copias del codigo.

## Que corre, y donde

| Fichero | Que es |
|---|---|
| `api.py` | FastAPI. Rutas, lifespan, WebSocket y el arranque por direcciones. |
| `estado.py` | El estado de la casa en memoria, los eventos y el cliente MQTT. |
| `seguridad.py` | Hash del token, canje del enlace y la dependencia de 401. |
| `pruebas_api.py` | 130 comprobaciones de la API y del WebSocket, sin broker. |
| `pruebas_estado.py` | 63 comprobaciones del estado en memoria y de los eventos. |
| `pruebas_acl.py` | 8 comprobaciones de la ACL de Mosquitto: 2 escrituras permitidas y 6 denegadas. **Necesita un broker de prueba.** |
| `web/` | El proyecto Flutter. |
| `web_build/` | El resultado de compilar. Va montado en `/srv/web`. Ignorado por git. |

## Compilar el frontend

El orden importa: `docker compose build panel` copia `panel/` entero, y el build de
Flutter tiene que estar dentro antes, o la imagen sale sirviendo el `web_build` viejo (o
ninguno).

El SDK no está en `/opt`: está en `$HOME/flutter` (Flutter 3.47.6, Dart 3.13.5). Además
del `web_build` hay que compilar `flutter analyze` sin errores y `flutter test` verde
(14 comprobaciones) antes de empaquetar:

```bash
cd servidor/panel/web
export PATH="$HOME/flutter/bin:$PATH"
flutter analyze        # sin errores
flutter test           # 14/14
flutter build web \
    --release \
    --no-web-resources-cdn \
    --pwa-strategy=none \
    --base-href / \
    --output ../web_build
cd ..
docker compose build panel
```

Los dos flags que no son de empaquetado:

- `--no-web-resources-cdn`: sin el, el build pide CanvasKit a
  `storage.googleapis.com` y el panel no carga sin Internet.
- `--pwa-strategy=none`: sin el, Flutter genera un service worker que sirve el
  `index.html` desde la cache y puede dejar una version anterior en el movil durante
  dias sin que se vea ningun error. Sin service worker, recargar carga la version
  nueva. La contra es que en Android no aparece el icono de "instalar"; en iOS
  "Anadir a la pantalla de inicio" funciona igual, y en Android la via de instalacion
  es la APK.

  Aviso (Flutter 3.47.6): el flag esta marcado como obsoleto y el build imprime
  «The --pwa-strategy option is deprecated and will be removed in a future Flutter
  release» (issue flutter/flutter#156910). Se mantiene porque hoy sigue siendo la
  unica forma de no generar el service worker: sin el flag, el build usa por defecto
  `offline-first` y el service worker vuelve. No hay reemplazo equivalente todavia;
  si una version futura lo quita, habra que revisar este punto. El aviso, por ahora,
  no cambia la salida.

Volver a compilar despues de cambiar el frontend es exactamente la misma orden. No hace
falta reiniciar el broker ni el orquestador.

## Variables

| Variable | Para que sirve |
|---|---|
| `MQTT_PANEL_PASSWORD` | **Obligatoria.** Sin ella el proceso muere al arrancar, a proposito. |
| `PANEL_URL` | La URL completa que se manda a los familiares en `/panel`. |
| `PANEL_DIRECCIONES` | Interfaces donde escucha, separadas por comas. Se anade `127.0.0.1` siempre. |
| `PANEL_PUERTO` | Puerto en esas interfaces. |
| `MODO_SITIO` | `instalacion` lee el sitio de `nodo/estado`; `demostracion`, de `$SYS`. |
| `PANEL_RAIZ_WEB` | Donde estan los ficheros estaticos. Por defecto `/srv/web`. |
| `NIVEL_LOG` | `DEBUG`, `INFO` (por defecto), `AVISO` o `ERROR`. |

`0.0.0.0` no aparece en ningun sitio. El panel se abre a un movil cualquiera delante de
la casa, y con `0.0.0.0` se abriria a cualquiera que llegara al puerto de la LAN.

## Puesta en marcha

1. Compilar `web_build/` (arriba).
2. `docker compose build panel`.
3. Anadir el usuario MQTT `panel` con `mosquitto/crear-usuarios.sh`.
4. `docker compose restart mosquitto` -- la ACL se lee una sola vez al arrancar.
5. Poner las variables en `servidor/.env`.
6. `docker compose up -d orquestador` -- migra el esquema a la version 2.
7. `docker compose up -d panel`.
8. Comprobar `GET /api/salud`, un `401` sin token y `/api/estado` con token.

El paso 3 y el 4 necesitan confirmacion antes de hacerlos: reiniciar el broker corta la
conexion del orquestador y del nodo unos segundos, y el enlace del sitio con la casa se
cae.

## Comprobar que esta vivo

```bash
curl -s http://127.0.0.1:8080/api/salud
```

Sin sesion, cualquier ruta de la casa tiene que dar `401`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/estado
```

El `healthcheck` del compose usa `/api/salud`, que no necesita token: por eso es la
unica ruta sin sesion.

Por Internet (Funnel), la misma comprobacion es:

```bash
curl -s https://asistente.tail722206.ts.net/api/salud
```

Desde el publico solo llega `ok` y `hora`: el detalle de `/api/salud` (url, esquema,
modo, estado del broker) lo ve solo quien llama desde `127.0.0.1` sin
`X-Forwarded-For`, que es el healthcheck de Docker.

## El nodo de Tailscale (Funnel)

Desde la Etapa 2 el panel se publica por Internet con un nodo de Tailscale llamado
`asistente` (`asistente.tail722206.ts.net`), para quien no tiene Tailscale. El nodo
vive en el contenedor `asistente-tailscale`, separado del Tailscale de la VM
(`villaca`): red del host, userspace (`TS_USERSPACE=true`) y puerto UDP propio
(`--port=41642`), porque el de la VM ya usa el 41641.

Lo que se publica se decide en `tailscale/serve.json` (un solo manejador `/` que
apunta al panel en `127.0.0.1:8080`, y `AllowFunnel` para
`asistente.tail722206.ts.net:443`). El permiso para abrir el Funnel va en la consola
de Tailscale, en la politica de acceso del nodo etiquetado. Los cambios en
`serve.json` no se leen solos: hay que recrear el contenedor.

### La clave de autenticacion es de UN SOLO USO

`TAILSCALE_AUTHKEY` (en `servidor/.env`) solo hace falta en el PRIMER arranque,
cuando el volumen `tailscale_datos` aun no tiene estado. Con estado dentro y
`TS_AUTH_ONCE=true`, el contenedor arranca aunque la clave haya caducado (la propia
es de 1 dia) o la variable este vacia: usa la identidad guardada. Por eso en el
compose la variable NO es obligatoria (va sin `:?`).

Si se pierde el volumen (o hay que re-registrar el nodo):

1. Generar una clave nueva en https://console.tailscale.com/admin/settings/keys:
   un solo uso, etiqueta `asistente`, pre-aprobada y caducidad de 1 dia.
2. Borrar el nodo "asistente" viejo en Machines, si sigue.
3. Poner la clave en `servidor/.env` (`TAILSCALE_AUTHKEY=tskey-...`).
4. `docker compose up -d tailscale`.
5. Comprobar que el nodo vuelve con el MISMO nombre "asistente": si aparece como
   "asistente-2", el nombre del DNS de Funnel y el certificado han cambiado, y hay
   que seguir por el nuevo.
6. Cuando el nodo este logueado se puede dejar la clave vacia en el `.env`.

El Funnel no es un acceso a la tailnet: solo expone el panel. Y con la politica del
tailnet (solo `autogroup:member` inicia conexiones), los nodos etiquetados no pueden
alcanzar a ningun otro, que es justo lo que se quiere para un nodo expuesto.

### Diagnosticar el acceso publico

Hay DOS caminos al mismo dominio, y el DNS por defecto resuelve al privado:

- Privado (tailnet): `asistente.tail722206.ts.net` resuelve a la IP del nodo
  (p. ej. `100.100.158.110`). Es el camino que usa quien tiene Tailscale.
- Publico (Funnel): la entrada es una IP de Talaria (p. ej. `208.111.34.11`). Solo
  este camino prueba que el Funnel cumple lo suyo.

Para forzar el camino publico desde el servidor (donde el DNS daria la IP privada):

```bash
curl -sS --resolve asistente.tail722206.ts.net:443:208.111.34.11 \
    https://asistente.tail722206.ts.net/api/salud
```

La configuracion y la capacidad del Funnel se ven desde dentro del contenedor:

```bash
docker compose exec tailscale tailscale funnel status
docker compose exec tailscale tailscale status --json   # Self.Capabilities incluye "funnel"
```

Si el nodo responde por el camino privado pero NO por el publico con un error TLS,
lo mas probable es que le falte la capacidad `funnel` en la politica de la consola
(`nodeAttrs` debe apuntar al tag del nodo) o que el certificado no este emitido;
se corrige en la consola y se recrea el contenedor.

### El latido y por que ya no se corta el WebSocket

El `latido` y el `pong` son mensajes de aplicacion, no tramas de control: el servidor
manda `latido` cada 20 s y cierra con **1001** a los 45 s sin el `{"tipo": "pong"}` que
el cliente tiene que devolver (`SEGUNDOS_PONG_WS`). El servidor los recibe igual por
los dos caminos (privado y Funnel): NO hay ninguna limitacion del Funnel con el
WebSocket.

El corte de ~60 s que se midio el 2026-10-08 no lo causaba el camino publico: el
cliente web escuchaba el `latido` pero no respondia el `pong` de aplicacion, asi que el
servidor lo cerraba a los 60 s por CUALQUIER camino. Se arreglo en el cliente:

- responde `{"tipo": "pong"}` a cada `latido` (el ping de protocolo WebSocket no
  cuenta);
- manda su propio `{"tipo": "ping"}` cada 25 s, porque el navegador no tiene ping de
  protocolo;
- se reconecta solo tras cualquier cierre (1001, 1008, 1013 o red caida) con esperas
  1, 2, 4, 8, 15, 30 s y ±20 % de desorden (apartado 4.3 de la propuesta Etapa 1); al
  reconectar recibe la instantanea entera.

Verificado el 2026-10-08: 5 min seguidos por el camino publico (celular con datos
moviles y sin Tailscale, IP publica real en el log) sin un solo corte, y ante un cierre
forzado del servidor la app se reconecto sola y siguio recibiendo eventos.

## Las pruebas

Las dos primeras no necesitan broker ni red, y se pueden correr en la imagen del panel:

```bash
docker run --rm -v "$PWD:/app" -w /app asistente-panel:1.0 \
    sh -c "pip install -q -r panel/requerimientos-pruebas.txt && python panel/pruebas_api.py"

docker run --rm -v "$PWD:/app" -w /app asistente-panel:1.0 \
    sh -c "pip install -q -r panel/requerimientos-pruebas.txt && python panel/pruebas_estado.py"
```

El `requerimientos-pruebas.txt` instala `httpx2` (y no `httpx`): desde starlette 1.7.0
el `TestClient` aborta con "requires the httpx2 package" si no esta. La otra linea del
mismo archivo (`websockets`) es lo que permite probar `/api/vivo` de verdad.

### La ACL

`pruebas_acl.py` es la unica que necesita un broker, y **no debe apuntar al de
produccion**: publica mensajes de prueba, deja retenidos que no se borran sin reiniciar,
y el `satelite/decir` haria hablar al sitio. El propio script se niega a arrancar si
`MQTT_PUERTO=1883`.

La cabecera de `pruebas_acl.py` tiene la orden exacta para levantar el broker de prueba
con esta ACL y este `passwd`.

Por que la prueba mira si un mensaje **llega** y no el SUBACK: Mosquitto concede la
suscripcion con el QoS pedido y filtra despues, en la entrega. Se comprobó que devuelve
`1` incluso para `otro/tema`, con un usuario sin permiso para nada alli. Un SUBACK da
igual a "concedido", asi que un script que se fiara de el daria verde a una ACL vacia.
Por lo mismo `publish()` no sirve como senal: no lanza excepcion nunca, el broker acepta
la conexion y descarta el mensaje en silencio.

## Los estados

`conectado`, `desconectado`, `sin_datos` y `no_instalado`.

Un componente del que nunca se ha recibido nada es `no_instalado`, no `desconectado`:
la diferencia es si se ha visto alguna vez. Las lecturas que aun no existen (por ejemplo
el satelite, si no hay) van a `sin_datos`, porque el estado es el que se ha leido, no el
dispositivo.

## El WebSocket

Instantanea completa al conectar, luego un evento por cambio, y `latido` cada 20 s. El
cliente contesta `{"tipo": "pong"}` y manda su propio `ping` cada 25 s: sin el pong, el
servidor cierra a los 45 s. La cola de cada conexion esta acotada (`COLA_EVENTOS_WS`):
si un cliente no consume y se llena, se le cierra con **1013** ("recargando"), no con
un error. Cualquier cierre (1013, 1001, 1008 o red caida) se trata como reconexion
automatica con esperas 1, 2, 4, 8, 15, 30 s, y al reconectar recibe la instantanea
entera, que es lo que lo hace seguro.

## La pagina de canje

`/acceso/<codigo>` es HTML a pelo, sin Flutter, y canjea el codigo por un token con un
`POST`. Es a proposito: Telegram y los navegadores hacen vista previa de un `GET`, y un
`GET` a `/acceso/<codigo>` dejaria el codigo en un historial, en un `Referer` y en una
vista previa de Telegram. Por eso el canje es un `POST` y la pagina lleva
`Referrer-Policy: no-referrer` y `X-Robots-Tag: noindex`.

El codigo va dentro de la pagina como literal de JavaScript, y ahi `json.dumps` solo no
basta: escapa lo no-ASCII y deja los `<`, `>` y `&` como estaban, asi que un
`</script>` en la URL cerraria la etiqueta. Lo escapa `_js_seguro()`.

## Lo que este servicio no hace

- **No crea alertas.** Las resuelve, que es otra cosa: confirma que alguien ya esta
  atendiendo una para que el sitio deje de llamar.
- **No lee `casa/sala/alerta/#`.** Las alertas salen de la misma SQLite que las del bot.
- **No lee `casa/sala/nodo/config`.** Ahi estan los umbrales de gas de cada habitacion.
- **No toca el tiempo de inactividad.** Eso lo decide el orquestador.
- **No pone ni toca nada mas.** Lo que puede hacer con una sesion esta en el bloque
  `user panel` de `mosquitto/config/acl.conf`, y `pruebas_acl.py` lo comprueba.