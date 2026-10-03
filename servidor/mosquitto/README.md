# Broker Mosquitto del servidor

Broker MQTT de la casa. Vive en la VM `villaca` (Proxmox) y sirve tanto a los
ESP32 de la red local como a la laptop por Tailscale.

Implementa la propuesta "arranque anticipado v2" del 2026-10-03 (Paso 0b de
la Fase 4), con dos correcciones que salieron al instalarlo y que
están documentadas más abajo. No hay red propia de Compose, no hay proxy
inverso y no hay TLS: la exposición se controla con ACL y con dejar los
listeners en tres interfaces concretas.

---

## 1. Como esta armado

```
ESP32 (WiFi, 192.168.1.0/24) ─┐
                              ├─> ens18 192.168.1.169:1883
orquestador en villaca ───────┴─> 127.0.0.1:1883

laptop (Tailscale) ────────────────> tailscale0 100.116.194.116:1883
                                    MagicDNS: villaca.tail722206.ts.net
```

Tres listeners, mismo puerto, IPv4:

| Listener              | Interfaz | Quién entra                                       |
| --------------------- | -------- | ------------------------------------------------- |
| `127.0.0.1:1883`      | loopback | orquestador, pruebas, agente de la laptop por SSH |
| `100.116.194.116:1883` | tailscale0 | laptop por Tailscale, VM dedicada cuando exista   |
| `192.168.1.169:1883`  | ens18    | ESP32 por WiFi                                    |

No hay listener en `0.0.0.0`. El servidor además tiene `docker0` y tres
bridges de Docker (172.17-172.20), y el broker **no** escucha en ninguno: se
comprobó con `ss -tlnp | grep 1883`, que muestra solo las tres direcciones de
arriba. Si mañana se le agrega una interfaz al servidor, el broker no queda
expuesto por accidente.

**El broker no se expone a Internet.** No hay que abrir ningún puerto en el
router. Fuera de casa, el ESP32 se conecta al broker que corre en la laptop, y
ese broker llega al servidor por Tailscale, cifrado. Lo único que hay que
verificar en el router es que **no** exista una redirección del 1883 hacia
192.168.1.169 ni una DMZ que apunte a esa IP, y que UPnP esté desactivado (ver
la sección 9).

Archivos:

| Ruta                                       | Qué es                                        |
| ------------------------------------------ | --------------------------------------------- |
| `servidor/docker-compose.yml`               | servicio, límites, healthcheck                |
| `servidor/.env`                             | contrasñas, modo 600, fuera del repositorio   |
| `servidor/mosquitto/config/mosquitto.conf`  | configuración del broker                      |
| `servidor/mosquitto/config/acl.conf`        | permisos por usuario y topico                 |
| `servidor/mosquitto/crear-usuarios.sh`      | crea y rota el archivo de contrasenas         |
| `asistente-voz-secretos/mosquitto/passwd`   | hashes de las contrasenas, 640, fuera del repo |

Los secretos viven en `/home/ilse/Servidores/asistente-voz-secretos/`, que
está **fuera** del repositorio. Si alguna vez se cambia esa ruta, el script se
niega a correr si apunta dentro del proyecto.

---

## 2. Puesta en marcha

Requisitos: Docker con Compose v2, y ser usuario del grupo `docker`.

```bash
cd /home/ilse/Servidores/Asistente-Voz

# 1. Plantilla del .env
cp servidor/.env.example servidor/.env
chmod 600 servidor/.env

# 2. Los cinco usuarios. Pregunta cada contrasena; no se escribe nada a mano.
servidor/mosquitto/crear-usuarios.sh

# 3. Copiar las contrasenas del paso 2 a servidor/.env (las cinco)

# 4. Levantar
cd servidor && docker compose up -d && docker compose ps
```

Si `servidor/.env` no existe o le falta una contrasena, `docker compose` avisa
y no arranca nada:

```
falta MQTT_SALUD_PASSWORD en servidor/.env
```

### Comandos de uso

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor

docker compose ps                       # estado y salud
docker compose logs -f mosquitto       # registro en vivo
docker compose restart mosquitto       # reiniciar (tambien recarga el ACL)
docker compose down                     # parar (conserva el volumen)
docker compose up -d                    # volver a levantar
docker compose down -v                  # borrar TODO, incluidos los retenidos
```

---

## 3. Accesos

| Usuario       | Desde                 | Para qué                                     |
| ------------- | --------------------- | -------------------------------------------- |
| `salud`       | solo el healthcheck   | leer `$SYS/broker/version`. No publica nada  |
| `nodo`        | ESP32 (WiFi)          | publica estado, sensores y alertas; recibe `luz/set`, `nodo/config` y `prueba_gas` |
| `puente`      | broker de la laptop   | reenvía todo `casa/#` en ambos sentidos; anuncia su estado |
| `orquestador` | `127.0.0.1`           | publica órdenes, configuración y avisos; escucha todo lo de la casa |
| `satelite`    | laptop por Tailscale  | publica su estado; escucha `casa/sala/satelite/decir` |

`satelite` **no** puede publicar en `casa/sala/luz/set`. Si el agente de la
laptop intentara mandar la orden de luz, el broker la descarta (ver sección 6:
el rechazo es silencioso). Y el que sí publica en `satelite/decir` es el
servidor, no el satélite.

`salud` solo puede leer `$SYS/broker/version`. No tiene lectura sobre `$SYS/#`,
así que cosas como `$SYS/broker/retained messages/count` no las puede usar.

---

## 4. Permisos por topico

Fuente de verdad: la tabla de tópicos del contrato en el plan de la Fase 4
(`fase4_md/Fase_4_Desarrollo_e_Integracion.md`). El prefijo es
`casa/<lugar>/<elemento>/<acción>` y el lugar es `sala`.

| Tópico | Publica | Escucha | QoS | Retain |
| ------ | ------- | ------- | --- | ------ |
| `casa/sala/luz/set` | orquestador | nodo | 1 | no |
| `casa/sala/luz/estado` | nodo | orquestador | 0 | sí |
| `casa/sala/sensores` | nodo | orquestador | 0 | no |
| `casa/sala/alerta/gas` | nodo | orquestador | 1 | no |
| `casa/sala/alerta/ayuda` | nodo u orquestador | orquestador | 1 | no |
| `casa/sala/alerta/inactividad` | orquestador | orquestador | 1 | no |
| `casa/sala/satelite/decir` | orquestador | satelite | 1 | no |
| `casa/sala/satelite/estado` | satelite | orquestador | 1 | sí |
| `casa/sala/nodo/estado` | nodo | orquestador | 1 | sí |
| `casa/sala/nodo/config` | orquestador | nodo | 1 | sí |
| `casa/sala/nodo/prueba_gas` | puente | nodo | 1 | no |
| `$SYS/broker/connection/puente-sitio/state` | puente (automático) | orquestador | 1 | sí |

Quién escribe y quién lee, usuario por usuario:

| Usuario | Escribe | Lee |
| ------- | ------- | --- |
| `nodo` | `luz/estado`, `sensores`, `alerta/gas`, `alerta/ayuda`, `nodo/estado` | `luz/set`, `nodo/config`, `nodo/prueba_gas` |
| `puente` | todo `casa/#` (lo reenvía) | todo `casa/#` |
| `orquestador` | `luz/set`, `alerta/inactividad`, `satelite/decir`, `nodo/config`, `alerta/ayuda` | todo lo que publica la casa, más `$SYS/broker/connection/+/state` |
| `satelite` | `satelite/estado` | `satelite/decir` |
| `salud` | nada | `$SYS/broker/version` |

Tres cosas que parecen rarezas y no lo son:

- **`satelite/decir` lo publica el servidor, no el satélite.** Es lo que el
  servidor le dice que diga. Por eso `satelite` solo lee ese tópico, y
  `nodo` y `puente` no lo tocan.
- **`alerta/ayuda` la pueden publicar los dos.** La tabla dice «ESP32 (vía Alexa)
  o servidor (vía voz)». Si solo pudiera el ESP32, una alerta de voz nunca
  saldría.
- **`nodo/prueba_gas` lo publica el puente, no el ESP32.** En el contrato se
  publica desde la laptop con `mosquitto_pub` al broker local y el puente lo
  reenvía. El ESP32 solo lo escucha, y solo si se compiló con `PRUEBAS=1`.

El puente tiene `readwrite casa/#` porque la configuración del puente en el
plan es `topic casa/# both 1`: reenvía en los dos sentidos. Eso significa que
el broker de la laptop es de confianza: lo que se publique allí llega a los
ESP32. Lo que lo mantiene acotado es que el puente no puede salir de `casa/#`.

Cambiar `acl.conf` **obliga a reiniciar** el contenedor: el broker lo lee una
sola vez al arrancar.

### El estado del puente

`notifications true` no es lo que publica el estado del puente: eso lo hace
Mosquitto solo, en `$SYS/broker/connection/<clientid>/state`. Pone `1` cuando el
puente conecta y `0` como Last Will cuando se cae, y lo entrega **retenido**,
así que un suscriptor nuevo ve el estado al instante.

El `<clientid>` lo pone Mosquitto, no el ACL, y sale del `remote_clientid` del
puente. En la configuración del puente del plan ese valor es `puente-sitio`, así
que el tópico real es **`$SYS/broker/connection/puente-sitio/state`**, y es lo que
el ACL autoriza.

La tabla de tópicos del contrato decía `sitio/state`, que no existe: se
corrigió el plan para que diga `puente-sitio/state` (2026-10-03). El ACL solo
tiene ese camino, no `sitio/state`.

El panel se suscribe con `$SYS/broker/connection/+/state`, así que le da igual
cómo se llame el cliente: le llega igual.

Comprobado: `puente` publica en `puente-sitio/state` y el broker acepta;
`puente` en `sitio/state` y en `$SYS/broker/version` se rechaza, y también
`salud` en `$SYS/broker/connection/otro/state`.

---

## 5. Contrato de mensajes

| Tópico | QoS | Retain | Regla de `ts` |
| ------ | --- | ------ | -------------- |
| `casa/sala/luz/set` | 1 | no | obligatorio, epoch en segundos |
| `casa/sala/luz/estado` | 0 | sí | no |
| `casa/sala/sensores` | 0 | no | no |
| `casa/sala/alerta/gas` | 1 | no | no |
| `casa/sala/alerta/ayuda` | 1 | no | no |
| `casa/sala/alerta/inactividad` | 1 | no | no |
| `casa/sala/satelite/decir` | 1 | no | obligatorio, epoch en segundos |
| `casa/sala/satelite/estado` | 1 | sí | no |
| `casa/sala/nodo/estado` | 1 | sí | no |
| `casa/sala/nodo/config` | 1 | sí | no |
| `casa/sala/nodo/prueba_gas` | 1 | no | no |

Órdenes y alertas van con QoS 1, que garantiza la entrega pero puede repetir
mensajes; los `id` procesados se guardan en SQLite con índice único durante 7
días para deduplicar.

**Regla de antigüedad (10 s).** El broker es MQTT 3.1.1 y no tiene
`message-expiry-interval`: no puede descartar mensajes viejos. La caducidad la
aplica el consumidor:

- ESP32 y `satelite` **descartan** un mensaje con más de 10 s de antigüedad.
- No cuentan los retenidos (`casa/sala/nodo/config`) ni las alertas: se procesan
  aunque sean viejos.
- Excepción: `tipo` `recordatorio` o `chat` no caduca nunca.
- Un mensaje sin `ts`, o con `ts` que no es un número, se descarta y se
  registra.

El ESP32 necesita reloj (SNTP) antes de aplicar esto. Sin reloj válido, todo se
descarta.

`ts` es epoch en **segundos**, no milisegundos. La diferencia es de mil, y un
mensaje del año 1970 parece expirado hace medio siglo.

## 6. Pruebas

Todas desde `/home/ilse/Servidores/Asistente-Voz/servidor`. Los clientes MQTT
no están instalados en el host; se usan los del contenedor:

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
set -a; . ./.env; set +a
```

### Lo importante: un publish denegado no se ve desde el publicador

Con MQTT 3.1.1, cuando el ACL rechaza un `PUBLISH`, Mosquitto **igual manda
el PUBACK** (el PUBACK de 3.1.1 no tiene sitio para un código de motivo, solo
para MQTT 5). O sea: `mosquitto_pub` sale con código 0 y parece que todo
funcionó, pero el mensaje se descartó.

| Qué pasa | Cómo lo ve el que publica |
| -------- | ------------------------- |
| Suscripción denegada | `mosquitto_sub` recibe SUBACK 0x80 y sale con error |
| Publish denegado | `mosquitto_pub` sale con 0, como si nada |
| Publish denegado, en el registro | solo con `log_type debug`: `Denied PUBLISH from ...` |

Por eso los permisos se comprueban **mirando si el mensaje llega**.

### Matriz completa de permisos

Esta es la prueba que de verdad importa: para cada tópico del contrato,
publica como el publicador que dice la tabla y comprueba que lo reciben
exactamente los que la tabla dice que lo escuchan.

```bash
# Requiere log_type debug para ver la parte de "Denied PUBLISH".
# 1. descomentar "log_type debug" en mosquitto/config/mosquitto.conf
# 2. docker compose restart
```

```bash
#_topico                                _publica      _oyente
casa/sala/luz/set                       orquestador   nodo
casa/sala/luz/estado                    nodo          orquestador
casa/sala/sensores                      nodo          orquestador
casa/sala/alerta/gas                    nodo          orquestador
casa/sala/alerta/ayuda                  nodo          orquestador
casa/sala/alerta/ayuda                  orquestador   orquestador
casa/sala/alerta/inactividad            orquestador   orquestador
casa/sala/satelite/decir                orquestador   satelite
casa/sala/satelite/estado               satelite      orquestador
casa/sala/nodo/estado                   nodo          orquestador
casa/sala/nodo/config                   orquestador   nodo
casa/sala/nodo/prueba_gas               puente        nodo
```

Resultado de correrla el 2026-10-03: **0 discrepancias**. `puente` aparece
recibiendo todos los `casa/…`, que es lo esperado porque reenvía con
`topic casa/# both 1`.

### Comprobaciones de acceso

```bash
# P1  El broker responde. Debe imprimir: $SYS/broker/version mosquitto version 2.0.22
docker compose exec -T mosquitto sh -c \
  'mosquitto_sub -h 127.0.0.1 -u salud -P "$MOSQUITTO_SALUD_PASSWORD" -t \$SYS/broker/version -C 1 -W 5 -v'

# P2  Contrasena incorrecta. Debe salir: Connection Refused: not authorised.
docker compose exec -T mosquitto mosquitto_sub -h 127.0.0.1 \
  -u salud -P incorrecta -t '$SYS/broker/version' -C 1 -W 3

# P3  Usuario desconocido. Mismo "not authorised".
docker compose exec -T mosquitto mosquitto_sub -h 127.0.0.1 \
  -u fantasma -P loquepsea -t '$SYS/broker/version' -C 1 -W 3

# P13 El healthcheck del contenedor: healthy
docker inspect --format '{{.State.Health.Status}}' asistente-mosquitto
```

Ojo con el `\$`: `docker compose exec` pasa el texto al shell del contenedor, y
sin la barra invertida el shell convierte `$SYS` en vacío y el topic queda
`/broker/version`.

### Permisos de escritura

```bash
# P5  satelite NO publica en casa/sala/luz/set   -> Denied PUBLISH
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u satelite \
  -P "$MQTT_SATELITE_PASSWORD" -t casa/sala/luz/set -m '{"estado":"off","origen":"voz"}' -q 1

# P6  nodo tampoco                                -> Denied PUBLISH
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u nodo \
  -P "$MQTT_NODO_PASSWORD" -t casa/sala/luz/set -m '{"estado":"off","origen":"boton"}' -q 1

# P7  orquestador SI: tiene que llegar a un suscriptor de nodo
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u orquestador \
  -P "$MQTT_ORQUESTADOR_PASSWORD" -t casa/sala/luz/set -m '{"estado":"on","origen":"voz"}' -q 1

# P8  salud no publica en $SYS/broker/connection/...   -> Denied PUBLISH
docker compose exec -T mosquitto sh -c \
  'mosquitto_pub -h 127.0.0.1 -u salud -P "$MOSQUITTO_SALUD_PASSWORD" \
   -t \$SYS/broker/connection/otro/state -m 1 -q 1'

# P8b puente SI en .../puente-sitio/state, y NO en ningun otro $SYS
# (incluido .../sitio/state, que ya no existe en el ACL)
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u puente \
  -P "$MQTT_PUENTE_PASSWORD" -t '$SYS/broker/connection/puente-sitio/state' -m 1 -q 1
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u puente \
  -P "$MQTT_PUENTE_PASSWORD" -t '$SYS/broker/connection/sitio/state' -m 1 -q 1   # este se deniega
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u puente \
  -P "$MQTT_PUENTE_PASSWORD" -t '$SYS/broker/version' -m 1 -q 1   # este se deniega
```

### Retenidos

```bash
# Los tres retenidos del contrato. Publicar y luego leer al suscribirse.
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u orquestador \
  -P "$MQTT_ORQUESTADOR_PASSWORD" -t casa/sala/nodo/config \
  -m '{"gas_on_adc":1800,"gas_off_adc":1550}' -q 1 -r
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u nodo \
  -P "$MQTT_NODO_PASSWORD" -t casa/sala/nodo/estado -m '{"conectado":true}' -q 1 -r
docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u satelite \
  -P "$MQTT_SATELITE_PASSWORD" -t casa/sala/satelite/estado -m '{"conectado":true}' -q 1 -r

# Lerlos de nuevo: tienen que llegar al suscribirse
docker compose exec -T mosquitto mosquitto_sub -h 127.0.0.1 -u nodo \
  -P "$MQTT_NODO_PASSWORD" -t casa/sala/nodo/config -C 1 -W 3 -v
docker compose exec -T mosquitto mosquitto_sub -h 127.0.0.1 -u orquestador \
  -P "$MQTT_ORQUESTADOR_PASSWORD" -t casa/sala/nodo/estado -C 1 -W 3 -v
```

### Persistencia

```bash
docker compose exec -T mosquitto sh -c 'kill -USR1 1'
docker compose exec -T mosquitto ls -l /mosquitto/data        # mosquitto.db
docker compose restart
# los retenidos siguen ahi
```

### Los tres oyentes

```bash
ss -tlnp | grep 1883
```

Deben salir exactamente tres líneas: `127.0.0.1:1883`, `192.168.1.169:1883` y
`100.116.194.116:1883`. Si aparece `0.0.0.0:1883` o alguno de los bridges de
Docker (172.17-172.20), algo está mal.

Prueba funcional desde las tres direcciones:

```bash
for h in 127.0.0.1 192.168.1.169 100.116.194.116; do
  docker compose exec -T mosquitto mosquitto_sub -h "$h" -u nodo \
    -P "$MQTT_NODO_PASSWORD" -t casa/sala/nodo/config -C 1 -W 3 -v
done
```

### P9.c: el estado del enlace del puente

```bash
docker compose exec -T mosquitto mosquitto_sub -h 127.0.0.1 -u orquestador \
  -P "$MQTT_ORQUESTADOR_PASSWORD" -t '$SYS/broker/connection/+/state' -v
```

Tiene que llegar `1` al suscribirse (el mensaje va retenido). Para la prueba de
verdad hace falta la laptop: el puente de la laptop se conecta por Tailscale,
y si se le corta la red, el panel tiene que ver `0` en menos de 90 segundos.
Necesita `ilse-1` encendida.

### P14 y P15: la regla de los 10 segundos

No se pueden probar aquí: son código del ESP32 y del agente de la laptop, que
todavía no existen. El broker solo los transporta.

## 7. Permisos de los secretos, y por qué el usuario es 1883

```
asistente-voz-secretos/                  750  ilse:ilse
asistente-voz-secretos/mosquitto/        750  ilse:ilse
asistente-voz-secretos/mosquitto/passwd  640  ilse:ilse
servidor/mosquitto/config/mosquitto.conf 640  ilse:ilse
servidor/mosquitto/config/acl.conf       640  ilse:ilse
```

El `passwd` **no** puede ser 600. Mosquitto lee su configuración y después
baja a uid 1883 / gid 1883 (`mosquitto`), y el archivo se abre en ese punto.
Con 600 se lleva `Error: Unable to open pwfile` y no arranca.

Y aquí está la trampa: **arrancar como root no sirve**. Mosquitto, al bajar
privilegios, llama a `initgroups("mosquitto", 1883)`, y eso **borra** los grupos
suplementarios que puso Docker. Se verificó: con root + `group_add: ["1000"]` el
broker dice igual `Unable to open pwfile`. Por eso el servicio declara

```yaml
user: "1883:1883"
group_add: ["1000"]
```

El proceso arranca ya como `mosquitto`, nunca entra al código de root, y
conserva el grupo 1000 con el que lee el `passwd` (`id` dentro del contenedor:
`uid=1883 gid=1883 groups=1000,1883`).

Sin root no corre el `chown -R /mosquitto` del entrypoint, pero no hace falta:
cuando se crea el volumen, Docker copia el contenido y la propiedad de
`/mosquitto/data` de la imagen, que ya es `1883:1883`. Comprobado. El volumen
queda `drwxr-xr-x 1883:1883` y el `mosquitto.db` se escribe sin problema.

Todo va montado en solo lectura, así que el `chown` del entrypoint tampoco
podría tocar los archivos del host aunque corriera.

---

## 8. Rotar una contrasena

```bash
cd /home/ilse/Servidores/Asistente-Voz
servidor/mosquitto/crear-usuarios.sh nodo     # pregunta solo la de nodo
```

Se reescribe el hash, se deja en 640 y se borran las copias `passwd.backup.*`
que deja la herramienta. Después: actualizar `servidor/.env`, `docker compose
up -d` (lee el `.env` de nuevo) y P1.

Agregar un usuario: mismo script con el nombre. Quitarlo: editar `acl.conf`,
rotar la clave del usuario y borrar su línea del `passwd` a mano (no hay comando
de borrado en el script, a propósito).

---

## 9. Lo que no hay que hacer

- No quitar `user: "1883:1883"`. Si el proceso arranca como root,
  `initgroups()` borra el grupo 1000 y el broker no encuentra el `passwd`.
- No volver `password_file` 600, ni el directorio 700 (750: el grupo tiene que
  poder *recorrer* el directorio, no solo leer el archivo).
- No volver a 644 los dos archivos de `config/`: Mosquitto avisa y en versiones
  futuras se niegue a cargarlos.
- No agregar `log_privacy`: no existe en Mosquitto, y una opción desconocida
  aborta el arranque.
- No montar `/mosquitto/log` como volumen nombrado: la imagen ya lo declara
  como VOLUME y se acumularían volúmenes anónimos en cada `up`. Está en
  `tmpfs` a propósito.
- No agregar servicios al `compose`: el broker va solo.
- No abrir el 1883 en el router. El broker no se expone a Internet: fuera de
  casa se usa el broker de la laptop y el puente llega por Tailscale. En el
  router (192.168.1.254, OpenWrt) hay que comprobar, al revés de lo habitual:
  - que **no** haya redirección o port forward del 1883 hacia 192.168.1.169
  - que la **DMZ no apunte** a 192.168.1.169 (lo normal es que esté vacía)
  - que **UPnP esté desactivado**, para que nada del interior pida puertos solos
- No abrir puertos nuevos en el firewall del servidor: 1883 es el único.

## 10. Registro

`log_dest stdout`, así que todo sale por `docker compose logs mosquitto`, con
rotación de 10 MB x 3. Niveles: error, warning, notice, information.

En cada arranque salen cuatro avisos que son **normales** en esta instalación:

```
Warning: File /mosquitto/secrets/passwd owner is not mosquitto. Future versions will refuse to load this file.
Warning: File /mosquitto/secrets/passwd group is not mosquitto. Future versions will refuse to load this file.
Warning: File /mosquitto/config/acl.conf owner is not mosquitto. Future versions will refuse to load this file.
Warning: File /mosquitto/config/acl.conf group is not mosquitto. Future versions will refuse to load this file.
```

Mosquitto quisiera que `passwd` y `acl.conf` fueran de `mosquitto:mosquitto`.
No lo son porque los archivos son de `ilse`: los de `config/` están en el
repositorio y el `passwd` tiene que poder regenerar `crear-usuarios.sh` sin
`sudo`. En 2.0.22 es solo un aviso. **Si alguna vez se actualiza a Mosquitto 3**,
habrá que cambiar esto: mover el `passwd` a `root:mosquitto` (con un contenedor
de un solo uso que lo haga, porque `ilse` no puede hacer `chown`) y decidir qué
pasa con los archivos del repositorio.

Un `PUBLISH` denegado por el ACL **no** deja ninguna línea en esos niveles:
Mosquitto solo lo registra en `debug`. Para verlo hay que descomentar
`log_type debug` en `mosquitto.conf`, reiniciar, y buscar `Denied PUBLISH`.

Nunca se registran contrasenas ni cargas útiles de los mensajes, así que no hay
nada que enmascarar.
