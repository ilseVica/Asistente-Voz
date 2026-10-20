# Firmware del nodo ESP32

Firmware del nodo `casa/sala` del Asistente-Voz. El diseño completo está en
`../../Archivos_text/Asistente-Voz/resultados/2026-10-03 - Propuesta Firmware ESP32 - Diseño del firmware del nodo ESP32 v3.txt`;
esto es solo cómo compilarlo, cargarlo y probarlo.

Los informes de cada tarea se archivaron fuera del repositorio, en
`Servidores/Archivos_text/Asistente-Voz/`, para que el proyecto se clone sin
cargarse de documentos de 700 KB.

## Compilar

PlatformIO está en el entorno virtual del usuario, no en el sistema:

```bash
~/.venvs/platformio/bin/pio run -e nodemcu-32s        # firmware normal
~/.venvs/platformio/bin/pio run -e nodemcu-32s-test   # firmware con pruebas remotas
~/.venvs/platformio/bin/pio run -e depurar            # el de pruebas con log al máximo
~/.venvs/platformio/bin/pio run                       # los tres
```

Cargar y monitorizar (con el ESP32 enchufado por USB):

```bash
~/.venvs/platformio/bin/pio run -e nodemcu-32s-test -t upload
~/.venvs/platformio/bin/pio device monitor
```

Los dos binarios salen en `firmware/.pio/build/<env>/firmware.bin`.

## Los tres builds

| Build | `PRUEBAS` | Qué cambia |
| --- | --- | --- |
| `nodemcu-32s` | 0 | El firmware de la demostración. Sin comandos de prueba. |
| `nodemcu-32s-test` | 1 | Además se suscribe a `casa/sala/nodo/prueba_gas` y acepta el comando `GAS <valor>` por el monitor serie. |
| `depurar` | 1 | Igual que `nodemcu-32s-test`, pero con `NIVEL_LOG=3` para ver todo el registro de la librería MQTT. |

Son tres, no dos: `depurar` es el mismo firmware de pruebas con el log al máximo,
y se compila junto a los otros dos en cada `pio run`.

En el firmware normal **no existe** ninguna forma de disparar gas por MQTT ni por
serie. Eso es a propósito: en la demostración no se quiere poder abrir un episodio
de gas por accidente.

## Credenciales

`include/secrets.h` **no está en el repositorio** y no se sube nunca. Se crea
copiando la plantilla:

```bash
cp include/secrets.example.h include/secrets.h
```

Y se rellenan el SSID y la contraseña del Wi-Fi, más el host, usuario y contraseña
del broker MQTT. `include/secrets.h` está en `.gitignore`, junto con `.pio/`.

Para cambiar el broker hay que recompilar: el host va en el binario, no se lee de
la Flash. El ESP32 resuelve ese host por mDNS una vez al arrancar.

## Pines

| Señal | GPIO | Notas |
| --- | --- | --- |
| LED integrado (hace de luz) | 2 | Polaridad con `LED_ACTIVO_EN_NIVEL_BAJO`, a confirmar en la primera prueba física |
| Botón BOOT (hace de botón de luz) | 0 | Pull-up. Pulsación corta enciende/apaga, 4 s reinician |
| Botón externo | 33 | Cableado, con antirrebote, pero `BOTON_EXTERNO_HABILITADO = false` hasta que llegue el hardware |
| Módulo de relé | 26 | `RELE_HABILITADO = false`. Con módulo de relé de 3.3 V y transistor, nunca el relé directo |
| Buzzer | 27 | Con módulo con transistor |
| DHT22 | 32 | **No es I2C**: es un pin de datos con su propia librería. Cableado a la fila de 3.3 V |
| MQ-2 | 34 | ADC1, atenuación 11 dB |
| PIR | 35 | Solo entrada |

## Botón BOOT

El botón BOOT (GPIO 0) tiene dos comportamientos, y solo uno de los dos publica algo:

| Pulsación | Qué hace | ¿Publica? |
| --- | --- | --- |
| **Corta** (menos de 4 s) | Alterna la luz: si estaba apagada la enciende y al revés | Sí, `casa/sala/luz/estado` con `origen` `boton` |
| **Larga** (4 s o más) | Reinicia el ESP32 | No |

Una pulsación corta produce **exactamente un** cambio, nunca dos. El antirrebote
ignora los cambios de menos de 50 ms y exige el estado estable en dos lecturas
seguidas, así que un rebote del contacto no genera un toggle extra.

El reinicio de la pulsación larga no publica nada: es un reinicio del
microcontrolador. Ojo con una consecuencia que sorprende: ese reinicio **no**
dispara el Last Will, porque el broker ve una desconexión limpia. El
`{"conectado":false}` retenido de `casa/sala/nodo/estado` solo aparece si el ESP32
se apaga sin cerrar sesión (se le quita la corriente), que es lo que mide la
prueba P27.

## Comandos de prueba desde el servidor

Todos los comandos se ejecutan **desde `servidor/`**, y usan `docker compose exec`
para hablar con el broker por su cliente MQTT interno.

Primero se cargan las contraseñas. El `set -a` es necesario: sin él, el shell
reparte las líneas como variables pero **no** las pasa al entorno de los procesos
hijos, y ni `mosquitto_pub` ni `docker` las verían. Los valores con espacios (como
`VOZ_DIRECCIONES`) van entre comillas dobles en el `.env`: sin ellas, el punto
cortaría el valor.

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
set -a; . ./.env; set +a
```

### Cada usuario solo puede hacer una parte

Esto no es opcional, y el fallo es silencioso. Con MQTT 3.1.1 un mensaje rechazado
por la ACL **no da error** a quien lo publica: simplemente no llega. Por eso las
pruebas se verifican **escuchando**, nunca mirando el código de salida del comando
de publicación.

| Usuario | Puede escribir | Puede leer |
| --- | --- | --- |
| `orquestador` | `luz/set`, `satelite/decir`, `nodo/config`, `alerta/ayuda`, `alerta/inactividad` | `luz/estado`, `sensores`, `nodo/estado`, `alerta/*`, `satelite/estado` |
| `nodo` | `luz/estado`, `sensores`, `alerta/gas`, `alerta/ayuda`, `nodo/estado` | `luz/set`, `nodo/config`, `nodo/prueba_gas` |
| `puente` | todo `casa/#` | todo `casa/#` |

De ahí salen tres reglas que contradicen la intuición:

- Las **órdenes de luz** se publican con `orquestador`, que es quien escribe `luz/set`.
- Las **alertas de gas** se publican con `nodo`, **no** con `orquestador`: el
  orquestador solo *lee* `alerta/gas`, no puede escribirlo.
- `prueba_gas` se publica con `puente`, no con `nodo`. El `acl.conf` le da a `nodo`
  **solo lectura** de `casa/sala/nodo/prueba_gas`: lo escucha, no lo publica. La
  tabla del contrato dice que lo publica la laptop, y la laptop es el `puente`. En
  esta VM, donde el puente todavía no existe, el usuario `puente` contra
  `127.0.0.1` funciona igual porque su ACL le da `casa/#` en ambos sentidos.

### Publicar una orden de luz (usuario `orquestador`)

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u orquestador -P "$MQTT_ORQUESTADOR_PASSWORD" -q 1 \
  -t casa/sala/luz/set -m "{\"estado\":\"on\",\"origen\":\"voz\",\"ts\":$(date +%s)}"
```

Con `ts` de hace 60 segundos (prueba P42, **debe descartarse**):

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u orquestador -P "$MQTT_ORQUESTADOR_PASSWORD" -q 1 \
  -t casa/sala/luz/set -m "{\"estado\":\"on\",\"origen\":\"voz\",\"ts\":$(($(date +%s)-60))}"
```

Sin `ts` (prueba P46, **debe descartarse**):

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u orquestador -P "$MQTT_ORQUESTADOR_PASSWORD" -q 1 \
  -t casa/sala/luz/set -m '{"estado":"on","origen":"voz"}'
```

### Escuchar el estado (usuario `orquestador`)

```bash
docker compose exec mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 \
  -u orquestador -P "$MQTT_ORQUESTADOR_PASSWORD" -q 0 -W 30 \
  -t casa/sala/luz/estado -t casa/sala/nodo/estado -v
```

El `-v` imprime el tópico delante de cada mensaje. Sin él no se sabe de qué
mensaje es cada línea cuando hay varios tópicos suscritos.

### Disparar el gas (usuario `puente`)

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":3000}'
```

Con el usuario `nodo` esto **no hace nada**, y no da error. La `ACL` de `nodo` solo
le da lectura de este tópico, porque el contrato dice que lo publica la laptop.

Este topico solo lo escucha el firmware compilado con `PRUEBAS=1`, o sea
`nodemcu-32s-test` y `depurar`. En el firmware normal no llega a nada.

Este es el mismo JSON del contrato que usa el nodo simulado. El firmware tambièn
acepta el número a pelo (`3000`), por compatibilidad, pero avisa en el log: es el
formato antiguo.

Para cerrar el episodio y que el contador de alertas vuelva a estar limpio, hay que
bajar el gas por debajo del umbral de cierre (1550) y esperar los 10 segundos que
tarda en cerrarse:

```bash
docker compose exec mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u puente -P "$MQTT_PUENTE_PASSWORD" -q 1 \
  -t casa/sala/nodo/prueba_gas -m '{"gas_adc":312}'
```

## Qué se puede probar sin nada más

1. **Prueba P10 (aire limpio, cero alertas).** Con `nodemcu-32s`, déjala 30 minutos
   con el broker conectado. No debe aparecer ningún mensaje en `casa/sala/alerta/gas`.
   El MQ-2 real da un valor bajo en aire limpio y no llega al umbral; durante los
   primeros 3 minutos (`MQ2_MS_CALIENTE`) el campo `gas_adc` sale `null` a propósito,
   porque un sensor sin calentar no mide.

2. **Prueba P32 (todolocal sin broker).** Detén el broker o quita el Wi-Fi. El botón
   BOOT, Alexa y la alarma de gas tienen que funcionar las tres.

3. **Prueba P45 (alerta sin red).** Con el build de pruebas y el Wi-Fi caído:

   ```
   GAS 3000
   ```

   A los ~3 s (3 lecturas a 1 Hz) debe encolarse una alerta y verse en el monitor la
   línea `encolada id=...`. Vuelve el Wi-Fi: la alerta se envía, llega `confirmada`
   con el **mismo id**, y la cola baja a 0. En el servidor (SQLite) tiene que haber
   un solo registro con ese id.

4. **Prueba P42 (orden vieja).** Publica `casa/sala/luz/set` con `ts` de hace 60 s.
   La luz no se enciende y el log dice el motivo del descarte.

## Log

Todos los mensajes llevan una etiqueta de módulo: `main`, `wifi`, `mqtt`, `alertas`,
`gas`, `luz`, `contrato`, `sensores`, `alexa`, `estado`, `pruebas`.

Nivel `Info` por defecto. Para ver los de depuración:

Hay un entorno `depurar`, que es el firmware de pruebas con el log al máximo:

```bash
~/.venvs/platformio/bin/pio run -e depurar -t upload
```

## Alexa

El nodo anuncia dos dispositivos por SSDP en el puerto TCP 80 (el 1901 por defecto de
fauxmoESP no lo encuentra ningún Alexa de gen 3):

| Dispositivo | Qué hace al activarlo |
| --- | --- |
| `Luz de la sala` | Enciende o apaga la luz y publica `luz/estado` con `origen` `alexa` |
| `Ayuda` | Publica `alerta/ayuda` una vez, apaga el interruptor, y no vuelve a publicar durante 60 s |

Las órdenes de Alexa **no llevan `ts`** y por eso no pasan por la regla de antigüedad
de MQTT, que descarta justamente lo que no lo trae: son locales, como las del botón.

## Lo que falta para la entrega completa

- **No se ha probado en hardware.** No hay ESP32 conectado por USB en la máquina
  donde se compiló, así que ninguna de las pruebas de arriba está ejecutada. Los dos
  builds compilan y enlazan.
- **No hay relé ni buzzer reales.** `RELE_HABILITADO = false`; el LED hace de luz.
- **Los sensores de ambiente ya son reales.** El DHT22 se lee en GPIO 32 y el MQ-2 en
  GPIO 34; cada uno puede salir `null` si no tiene dato (DHT22: tres fallos seguidos;
  MQ-2: los primeros 3 minutos de calentamiento, o `MQ2_HABILITADO = false`). La
  presencia sigue con el PIR real y, como respaldo, la simulación de
  `SIM_PRESENCIA_PERIODO_S`. Ya no quedan valores de gas, temperatura ni humedad
  simulados.
- **La polaridad del LED está sin confirmar.** Si en la primera prueba GPIO 2 alto
  resulta ser apagar, se cambia `LED_ACTIVO_EN_NIVEL_BAJO` en `include/config.h`, no el
  código.
- **Orden de la hora:** el NTP primario es la VM (`192.168.1.169`) y el respaldo
  `pool.ntp.org`. Mientras no haya hora válida, `ts` y la hora de los registros salen
  `null` y el reintento es cada 5 s (`MS_REINTENTO_SIN_HORA`).
