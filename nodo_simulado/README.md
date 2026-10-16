# Nodo simulado

Un script de Python que hace de ESP32 ante el broker: se conecta con el mismo
usuario, publica en los mismos tópicos y acepta los mismos formatos que el
firmware real. Sirve para desarrollar y probar el resto del sistema sin el
ESP32 conectado, y para provocar a voluntad una alarma de gas o de ayuda.

No es un emulador del firmware. No simula el hardware, simula el nodo en la red.
La diferencia importa: el simulador no tiene sensor de gas, ni pin de la luz, ni
buzzer. Lo que hace en su lugar son números y comandos. Lo que se prueba contra el
simulador es el broker, la ACL, el orquestador, el bot y la cola de Telegram; el
ESP32 hay que probarlo en el ESP32.

## Puesta en marcha

```bash
cd /home/ilse/Servidores/Asistente-Voz/nodo_simulado
python3 -m venv .venv
.venv/bin/pip install -r requerimientos.txt
```

Y con el broker levantado en `servidor/`, la contraseña del usuario `nodo` en el
entorno:

```bash
set -a; . ../servidor/.env; set +a
.venv/bin/python nodo_simulado.py
```

El `set -a` no es opcional: sin él el shell lee las líneas como variables pero no
las pasa al entorno de los procesos hijos, y `paho` no vería la contraseña. Y los
valores con espacios (como `VOZ_DIRECCIONES`) van entre comillas dobles en el
`.env`: sin ellas, el punto cortaría el valor.

Para darle otra dirección o puerto sin tocar el código:

```bash
MQTT_HOST=192.168.1.50 MQTT_PUERTO=1883 .venv/bin/python nodo_simulado.py
```

## Tópicos

Se suscribe a tres, y publica en cuatro:

| Topic | QoS | Retenido | Para qué |
| --- | --- | --- | --- |
| `casa/sala/luz/set` | 1 | no | La orden que sí manda la casa |
| `casa/sala/nodo/config` | 1 | no | Ajustes de umbrales del nodo |
| `casa/sala/nodo/prueba_gas` | 1 | no | Forzar el valor de gas |
| `casa/sala/luz/estado` publica | 0 | **sí** | El estado actual de la luz |
| `casa/sala/nodo/estado` publica | 0 | **sí** | Los ocho campos del nodo |
| `casa/sala/sensores` publica a 1 Hz | 0 | no | `temp_c`, `hum_pct`, `gas_adc`, `presencia` |
| `casa/sala/alerta/gas` publica | 1 | no | `{"id":"...","gas_adc":2100}` |
| `casa/sala/alerta/ayuda` publica | 1 | no | `{"id":"...","origen":"alexa"}` |

Las alertas van con QoS 1 porque no se pueden perder en una desconexión: si se
pierde una, el gas alto de la casa no genera aviso. Los estados van con QoS 0
porque son «publica y sigue»: al que llega tarde le da igual.

Las dos publicaciones retenidas del principio no son un extra: sin ellas, un
suscriptor que se conecte después no vería `luz/estado` ni `nodo/estado` hasta
que hubiera un cambio, y no hay forma de distinguir «la luz está apagada» de
«nadie ha dicho nada».

`sensores` **no** se retiene, y es a propósito: es una serie de lecturas, no un
estado. Retenerlo dejaría la última lectura para siempre y cualquier suscriptor
nuevo recibiría un valor viejo como si fuera actual. Las alertas tampoco se
retienen, porque una alerta repetida al reconectar es una alarma falsa.

## Consola

El simulador lee órdenes de la entrada estándar. No hacen falta ni `mosquitto_pub`
ni el broker para usarlas, y son la forma rápida de forzar una situación:

| Orden | Qué hace |
| --- | --- |
| `gas 3000` | Fuerza la lectura de gas a 3000 |
| `gas normal` | Vuelve al aire limpio (312) |
| `gas oscilar` | Alterna el gas alrededor del umbral, cada segundo |
| `ayuda [origen]` | Publica una alerta de ayuda; por defecto `alexa` |
| `luz on` / `luz off` | Enciende o apaga la luz, como el panel |
| `boton` | Alterna la luz, como el botón físico |
| `temp 23.5` | Fuerza la temperatura |
| `hum 60` | Fuerza la humedad |
| `presencia on` / `off` | Fuerza el detector de presencia |
| `estado` | Umbrales, lectura actual, episodio y contadores |
| `salir` | Cierra la sesión MQTT limpiamente |

Ejemplo:

```
[consola] gas 3000
[gas] lectura 3000 sobre el umbral 1800 (1 de 3)
[gas] lectura 3000 sobre el umbral 1800 (2 de 3)
[gas] EPISODIO ABIERTO con gas_adc 3000 (umbral 1800)
[mqtt] publicado en casa/sala/alerta/gas: {"id":"000000-b0001-0001","gas_adc":3000}
```

## Guion de demostración

Para enseñar el sistema entero funcionando en 5 minutos hay un guion con los
comandos sueltos en `herramientas/GUION.md`, y un script que los hace por ti:

```bash
bash herramientas/guion.sh
```

Va desde encender la luz hasta cerrar un episodio de gas y disparar el siguiente, y
se para en el paso donde hace falta el móvil.

## Comandos MQTT

Con la contraseña del usuario `nodo` en el entorno, y el broker en `servidor/`:

**Encender la luz** (la orden la escribe el usuario `orquestador`, no `nodo`):

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u orquestador -P "$MQTT_ORQUESTADOR_PASSWORD" -q 1 \
  -t casa/sala/luz/set -m "{\"estado\":\"on\",\"origen\":\"panel\",\"ts\":$(date +%s)}"
```

**Provocar una alarma de gas** (con `puente`, que es la laptop; `nodo` solo tiene
lectura de ese tópico):

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":3000}'
```

**Cerrar el episodio**, para que la próxima alarma vuelva a contar como nueva:

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":312}'
```

Ojo con esto: el episodio de gas **no** se cierra en cuanto el valor baja. Hay
que bajar de 1550 **y** esperar 10 segundos. Si solo se manda una vez el valor
normal, el episodio sigue abierto y la siguiente alarma con `gas 3000` no
publica nada, porque es el mismo episodio.

**Ver todo lo que hace el simulador:**

```bash
docker compose exec mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 \
  -u orquestador -P "$MQTT_ORQUESTADOR_PASSWORD" -q 0 -W 120 -v \
  -t 'casa/sala/#'
```

## La alarma de gas, en detalle

La máquina de estados es copia de `firmware/src/gas.cpp`, con los mismos nombres:

1. Se muestrea a **1 Hz**, no en cada vuelta del bucle. A la cadencia del bucle
   (0,5 s aquí, 50 ms en el ESP32) las tres lecturas se contarían en milisegundos y
   un solo pico de ruido abriría un episodio.
2. El episodio abre con **tres lecturas seguidas** iguales o mayores que
   `gas_on_adc` (1800 por defecto), y genera **una** alerta.
3. Cierra cuando la lectura lleva **10 segundos seguidos** por debajo de
   `gas_off_adc` (1550). Si vuelve a subir antes, se cancela el cierre y no hay
   alerta nueva: sigue siendo el mismo episodio.

`gas oscilar` alterna 60 unidades por encima y por debajo del umbral, cada
segundo. Con la regla de las tres lecturas seguidas, subir y bajar sin parar tres
veces seguidas **no** abre episodio: es la comprobación de que un pico de ruido no
dispara la casa.

El buzzer no se simula: no hay pin. Lo que hace el firmware durante el episodio es
el sonido local, y eso no se puede demostrar sin la placa.

## Ids de alerta y reinicios

El id de cada alerta es `MAC-arranque-contador`, por ejemplo
`000000-b0002-0003`. Los dos números se guardan en `contadores.json` y **no se
pierden al reiniciar**: cada arranque suma uno a `arranque` y el contador sigue
donde estaba.

Esto es copia de `firmware/src/persistencia.cpp`, que guarda los mismos dos
números en NVS. Sin esto, reiniciar el simulador reiniciaba los contadores y la
primera alerta de la nueva ejecución salía con el mismo id que la primera de la
anterior; el orquestador la descartaba por duplicada, sin avisar, y la alarma se
perdía en silencio.

El archivo se llama `contadores.json` y no `estado.json` a propósito: `estado.json`
lo escribe el proceso para decir que está vivo, y cuando los dos usaron el mismo
nombre se pisaron entre ellos.

`vivo.json` es lo otro: el pid, el client id y la hora del último latido, para que
un humano o una prueba de humo vea si el proceso está vivo. Son dos archivos
distintos porque uno cuenta y el otro describe.

## Diferencias con el ESP32 real, a propósito

| | ESP32 | Simulador |
| --- | --- | --- |
| Client id | `nodo-sala` | `nodo-simulado` |
| MAC en los ids de alerta | La real (`8cabb0`) | `000000` |
| Origen de la luz | GPIO, botón, regla, retained | Solo MQTT y consola |
| Aplica `nodo/config` | Sí, a los umbrales de gas | Sí, a los umbrales de gas |
| Umbral de gas | 1800 abre, 1550 cierra | 1800 abre, 1550 cierra |
| Gas real | ADC del MQ-2 | Un número del comando |
| Buzzer durante el episodio | Suena | No |
| Latido de sensores | 60 s, por temporizador | 60 s, más 1 Hz para el gas |

El client id tiene que ser distinto por una razón concreta: dos clientes con el
mismo id en el mismo broker se expulsan de su sesión, y el último que entra echa
al primero. Como el simulador y el ESP32 pueden estar conectados a vez durante las
pruebas, si compartieran id se expulsarían el uno al otro.

El pseudo-MAC es `000000` y no algo tipo `sim001` a propósito: tiene que ser
hexadecimal de seis caracteres para no romper el formato de los ids de alerta, y
`000000` es a la vez obviamente falso y válido.

Que el simulador aplique `nodo/config` es una decisión. Antes no lo aplicaba, con
la idea de que una prueba de configuración pasara en el simulador y fallara en el
hardware. Pero los umbrales son los mismos en los dos, así que la prueba de
configuración es la misma en los dos sitios, y no aplicarlos dejaba al simulador
como un nodo que no obedece.

## Problemas frecuentes

**`FALTA MQTT_NODO_PASSWORD en el entorno.`**
Falta el `set -a; . ../servidor/.env; set +a`.

**El simulador se desconecta solo y el `nodo/estado` retenido se queda en `false`.**
El Last Will se publica cuando el proceso desaparece sin cerrar sesión. Si ves
`conectado: false` retenido, mira el broker: puede que se haya caído el broker, o
que otro proceso haya entrado con el mismo client id y lo haya expulsado.

**Las alertas de gas no llegan al orquestador.**
Comprueba con qué usuario las publicas. `orquestador` **no** puede escribir en
`casa/sala/alerta/gas`: en el `acl.conf` solo puede leerlo. Las alertas de gas se
publican con el usuario `nodo`. Un mensaje rechazado por la ACL no da error a
quien lo publica, simplemente no llega, así que hay que comprobarlo escuchando.

**Mando `gas 3000` y no sale ninguna alerta.**
Lo más probable es que el episodio siga abierto de una prueba anterior. Cierra
primero con `gas 312` y espera 10 segundos a que se lean. El log lo dice:
`EPISODIO CERRADO`.

**`nodo/config` no cambia nada en el orquestador.**
`nodo/config` ajusta los umbrales del gas del nodo, no los del orquestador. El
orquestador tiene los suyos, en `servidor/orquestador/alertas.py`, y no los
cambia por MQTT.
