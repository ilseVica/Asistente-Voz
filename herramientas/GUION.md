# Guion de demostración (5 minutos)

Para enseñar el sistema funcionando de punta a punta: un nodo en la red, el broker
con sus permisos, el orquestador que guarda y filtra, y el Telegram del familiar.

Todo lo de aquí está probado con el nodo simulado y con el bot de Telegram real. Lo
que **no** se puede probar sin la placa está marcado como tal en el último paso.

## Antes de empezar

Dos terminales, o uno solo con el script.

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
set -a; . ./.env; set +a
docker compose up -d
```

Y el simulador, si no está ya:

```bash
cd /home/ilse/Servidores/Asistente-Voz
docker run -d --name sim-nodo --network host \
  -v "$PWD/nodo_simulado:/nodo" -e MQTT_HOST=127.0.0.1 \
  -e MQTT_NODO_PASSWORD="$MQTT_NODO_PASSWORD" -e PYTHONUNBUFFERED=1 \
  nodo-simulador:local
```

Comprobación de que el nodo está hablando con el broker:

```bash
docker logs sim-nodo 2>&1 | tail -2
```

Debería verse `conectado a 127.0.0.1:1883 como nodo` y un `nodo/estado` con
`"conectado":true`.

En otra terminal, para ver todo lo que pasa por el broker mientras se hace la
demostración (opcional, pero es lo que hace que se entienda):

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
docker compose exec mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 0 -v -t 'casa/sala/#'
```

## Paso 0 · Un solo comando, si hay prisa

```bash
cd /home/ilse/Servidores/Asistente-Voz
bash herramientas/guion.sh
```

Hace los seis pasos siguientes, esperando lo que tiene que esperar y enseñando en el
terminal lo que va ocurriendo. En el paso 4 se para y espera a que pulses Intro, que
es donde hace falta el móvil.

## Paso 1 · La luz (10 s)

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/luz/set -m "{\"estado\":\"on\",\"origen\":\"panel\",\"ts\":$(date +%s)}"
```

Y lo que contesta el nodo:

```bash
docker compose exec mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 0 -C 1 -t casa/sala/luz/estado
```

Sale `{"estado":"on","origen":"panel","ts":...}`. Va retenido, así que cualquier
suscriptor nuevo lo ve sin esperar a que la luz cambie otra vez.

## Paso 2 · Alerta de gas (30 s)

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":3000}'
```

A los unos 3 segundos, el móvil recibe **ALERTA DE GAS** con el botón
**Ya lo atendí**. En el log del simulador:

```bash
docker logs sim-nodo 2>&1 | grep -E "EPISODIO|\[alerta\]" | tail -3
```

Lo interesante para explicar: **no ha saltado con el primer mensaje**. El gas está
alto, y el episodio solo abre con tres lecturas seguidas a 1 Hz. Un pico de ruido de
una sola lectura no avisa a nadie.

## Paso 3 · Petición de ayuda, dos veces (40 s)

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/alerta/ayuda -m '{"id":"guion-b0001-0001","origen":"alexa"}'
sleep 10
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/alerta/ayuda -m '{"id":"guion-b0001-0002","origen":"alexa"}'
```

Al móvil llega **una sola** petición de ayuda. La segunda llega a la cola, se ve que
es nueva, y el orquestador la junta con la primera:

```
INFO alertas: alerta de ayuda guion-b0001-0002 NO se encola por el antirrebote de 60 s
(P05): ya hubo una ayuda en esa ventana. El id queda registrado como visto
```

Para verlo:

```bash
docker compose logs --since=30s orquestador 2>&1 | grep -E "ALERTA AYUDA|antirrebote"
```

## Paso 4 · «Ya lo atendí» (20 s)

En el móvil, al mensaje de gas: **Ya lo atendí**.

Pasa tres cosas:

1. El mensaje se reescribe como atendido, con la hora.
2. La alerta queda `resuelta` en la base.
3. Se publica en `casa/sala/satelite/decir` que ya hay alguien con el aviso.

```bash
docker compose logs --since=60s orquestador 2>&1 | grep -E "resuelta|satelite/decir"
```

Y el estado de todo, desde el propio bot, con `/alertas`.

## Paso 5 · Por qué hay que cerrar el episodio (1 min)

Este es el paso que más confunde, y el que más se olvida.

**5a. Repetir el disparo sin cerrar no hace nada:**

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":3000}'
```

No llega ninguna alerta nueva, y **no es un fallo**. El gas sigue alto dentro del
mismo episodio: es la misma alarma, y mandarla otra vez sería machacar al familiar
con el mismo aviso. En el log:

```bash
docker compose logs --since=20s orquestador 2>&1 | grep "ALERTA GAS" | tail -2
```

Sin nada nuevo.

**5b. Cerrar: por debajo de 1550 y 10 segundos seguidos.**

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":312}'
sleep 12
docker logs sim-nodo 2>&1 | grep "EPISODIO CERRADO"
```

Sale `EPISODIO CERRADO, gas=312`. Los 10 segundos son a propósito: si el gas baja un
momento y vuelve a subir, se cancela el cierre y el episodio sigue siendo el mismo.
Un valor bajo de un segundo no significa que el gas se haya ido.

**5c. Ahora sí, la siguiente alarma avisa, con id nuevo:**

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":3000}'
docker logs sim-nodo 2>&1 | grep -E "\[alerta\]" | tail -1
```

El id sale con el número de arranque y el contador nuevos, y el orquestador lo acepta
como una alerta nueva.

## Paso 6 · Los umbrales, en caliente (30 s)

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/config -m '{"gas_on_adc":1500,"gas_off_adc":1300}'
docker logs sim-nodo 2>&1 | grep nodo/config
```

El nodo se los guarda y sigue funcionando. Para volver a los de fábrica:

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/config -m '{"gas_on_adc":1800,"gas_off_adc":1550}'
```

## Registro en el bot, si hace falta empezar de cero

```bash
docker compose exec orquestador python -m registro codigo
```

Sale un código de un solo uso, válido 10 minutos. En el chat privado del bot:

```
/registro EL_CODIGO
```

Un segundo familiar puede registrarse con otro código: las alertas pasan a su chat y
el anterior deja de recibirlas. Sin código, `/registro` no hace nada.

## Qué NO demuestra esta demostración

Porque necesita la placa, y el ESP32 no está en esta máquina:

- La histeresis real del MQ-2: aquí el gas es un número, no un sensor.
- El pin del relé, el botón físico y el buzzer.
- Que el ESP32 aguante un corte de luz y su Last Will.

Y dos cosas que sí se pueden ver, pero no en la demostración de 5 minutos:

- El doble envío en una ventana de milisegundos, si el proceso muere entre la
  respuesta de Telegram y el `UPDATE` de la base. Está documentado y aceptado a
  propósito: es preferible avisar dos veces que no avisar.
- La ACL en negación. `mosquitto_pub` no protesta cuando el broker le quita el
  permiso: el mensaje simplemente no llega. Para verlo, escucha a la vez:
  `docker compose exec mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 -u nodo
  -P "$MQTT_NODO_PASSWORD" -t casa/sala/luz/estado` no recibe nada, porque el usuario
  `nodo` solo publica, no lee, ese tópico.
