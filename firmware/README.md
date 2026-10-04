# Firmware del nodo ESP32

Firmware del nodo `casa/sala` del Asistente-Voz. El diseño completo está en
`resultados de prompt/2026-10-03 - Propuesta Firmware ESP32 - Diseño del firmware del nodo ESP32 v3.txt`;
esto es solo cómo compilarlo, cargarlo y probarlo.

## Compilar

PlatformIO está en el entorno virtual del usuario, no en el sistema:

```bash
~/.venvs/platformio/bin/pio run -e nodemcu-32s        # firmware normal
~/.venvs/platformio/bin/pio run -e nodemcu-32s-test   # firmware con pruebas remotas
~/.venvs/platformio/bin/pio run                       # los dos
```

Cargar y monitorizar (con el ESP32 enchufado por USB):

```bash
~/.venvs/platformio/bin/pio run -e nodemcu-32s-test -t upload
~/.venvs/platformio/bin/pio device monitor
```

Los dos binarios salen en `firmware/.pio/build/<env>/firmware.bin`.

## Los dos builds

| Build | `PRUEBAS` | Qué cambia |
| --- | --- | --- |
| `nodemcu-32s` | 0 | El firmware de la demostración. Sin comandos de prueba. |
| `nodemcu-32s-test` | 1 | Además se suscribe a `casa/sala/nodo/prueba_gas` y acepta el comando `GAS <valor>` por el monitor serie. |

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
| DHT22 | 4 | **No es I2C**: es un pin de datos con su propia librería |
| MQ-2 | 34 | ADC1, atenuación 11 dB |
| PIR | 35 | Solo entrada |

## Qué se puede probar sin nada más

1. **Prueba P10 (aire limpio, cero alertas).** Con `nodemcu-32s`, déjala 30 minutos
   con el broker conectado. No debe aparecer ningún mensaje en `casa/sala/alerta/gas`.
   El gas simulado es una constante (312) precisamente para que esto sea cierto.

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
- **Los sensores son simulados.** Cuando lleguen el DHT22, el PIR y el MQ-2 se
  sustituyen las funciones de lectura de `src/sensores.cpp` y no se toca nada más.
- **La polaridad del LED está sin confirmar.** Si en la primera prueba GPIO 2 alto
  resulta ser apagar, se cambia `LED_ACTIVO_EN_NIVEL_BAJO` en `src/config.h`, no el
  código.
- **El NTP local (chrony) no está configurado**, así que el respaldo por SNTP contra
  la VM todavía no funciona. Requiere `sudo`.
