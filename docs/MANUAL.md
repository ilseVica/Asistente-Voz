# Manual del Asistente de Voz

Para alguien que no conoce el proyecto. Qué hay en cada equipo, cómo arrancarlo
para una demostración, cómo comprobar que todo está bien, cómo usar el chat, qué
hacer si algo falla, respaldos y cambio del token del bot.

## 1. Qué hay en cada equipo

- **Servidor (VM).** Broker MQTT (`mosquitto`), orquestador, servicio de voz
  (Vosk), panel web y contenedor de Tailscale. La base SQLite está en el volumen
  `asistente_orquestador_datos` (`/datos/orquestador.sqlite3`).
- **Satélite.** Una laptop con Omarchy (Arch, Hyprland, PipeWire) y voz Piper (es_MX-claude-high). Corre `satelite/satelite.py` como servicio de **usuario**
  `asistente-satelite` (instalado en `~/.local/bin/asistente-satelite`).
- **Nodo ESP32.** Sensores: DHT22 (GPIO 32), MQ-2 (GPIO 34), PIR (GPIO 35);
  relé (GPIO 26) con foco USB, buzzer (GPIO 27), botón BOOT = luz. Publica
  sensores, estado y alertas de gas; la inactividad la calcula el orquestador.
- **Móviles de la familia.** Reciben alertas por Telegram y usan el panel web con
  un código de un solo uso.
- **Panel web.** Estado de la casa (Equipos, alertas, historial), control de luz y
  marcar alertas como atendidas.
- **Conexiones del nodo:** `docs/CONEXIONES.md`.

## 2. Arranque para una demostración

1. Revisar que Storyteller e Immich no tengan tareas en curso; detenerlos solo
   si están procesando, para dejar CPU y memoria libres para Vosk.
2. Encender el ESP32 al menos 3 minutos antes (el MQ-2 necesita estabilizarse).
3. Arrancar el servidor:
   ```bash
   cd /home/ilse/Servidores/Asistente-Voz/servidor
   docker compose up -d mosquitto orquestador voz panel tailscale
   ```
   (o `docker compose start` si ya están creados; revisar con `docker compose ps`.
   Un `stop` manual no se levanta solo al reiniciar el host.)
4. Conectar el receptor USB-C del micrófono inalámbrico y encender el micrófono
   **antes** de arrancar el satélite. Comprobar:
   ```bash
   pactl list short sources | grep -iv monitor
   ```
5. Arrancar el satélite (sin sudo):
   ```bash
   systemctl --user start asistente-satelite
   ```
6. Si el receptor se conectó después:
   ```bash
   systemctl --user restart asistente-satelite
   ```
7. Si hace falta, revisar el log:
   ```bash
   journalctl --user -u asistente-satelite
   ```

Para probar el audio:

```bash
cd /home/ilse/Servidores/Asistente-Voz/satelite
uv run python prueba_audio.py
# http://127.0.0.1:8765/
```

Para hablar: mantener presionada **F9**, o usar el botón «Mantener para hablar»
del panel (suena un tono en la sala y queda un aviso en la conversación). No hay
palabra de activación.

## 3. Comprobar que todo está bien

- **Panel.** `https://asistente.tail722206.ts.net` (público) o
  `http://192.168.1.169:8080` (casa). En **Equipos** deben verse el satélite y
  el ESP32 como conectados; el ESP32 debe aparecer «con hora».
- **Entrada.** En el bot, `/panel` da un código de 6 caracteres con botones
  «Copiar código» y «Abrir panel». Registro de familiares: `/registro <código>`.
- **Telegram.** `/alertas` responde con pendientes, enviadas y las últimas cinco.
- **Voz.** Decir «enciende la luz»: la luz enciende y el satélite responde.

## 4. Uso del chat

Una sola conversación de la sala, igual en el panel y en Telegram. Desde Telegram
basta escribir texto (los comandos no entran). La sala escucha el mensaje y
responde si se mantiene F9 dentro de 60 s. «Necesito ayuda» siempre genera la
alerta, aun dentro de la ventana. Límite entre mensajes: 5 segundos.

## 5. Frases de la gramática

Decir frases completas; las incompletas (por ejemplo «prende») se responden con
«no entendí». No incluye las frases señuelo.

**ENCENDER_LUZ:** enciende la luz, prende la luz, enciende el foco, prende el
foco, enciende la luz de la sala, enciende la luz por favor, prende la luz por
favor, enciende el foco por favor, prende el foco por favor.

**APAGAR_LUZ:** apaga la luz, apaga el foco, apaga la luz de la sala, apaga la
luz por favor, apaga el foco por favor.

**AYUDA:** necesito ayuda, ayuda, ayudame, auxilio, ayudame por favor, ayuda por
favor, necesito auxilio, ayuda ya, auxilio ya.

**CANCELAR:** cancela, olvidalo. «Cancela» deshace una petición de ayuda hecha
por voz en los 2 minutos anteriores; Telegram dice «Falsa alarma».

**HORA:** que hora es, dime la hora, que horas son. Para la hora usar «dime la
hora» o «qué horas son».

**FECHA:** que dia es hoy, que fecha es hoy.

**TEMPERATURA:** que temperatura hace, como esta el clima.

**RECORDATORIOS:** tengo recordatorios, que tengo hoy.

**ESTOY_BIEN:** avisa a mi familia que estoy bien.

**BUSCAR_VIDEO:** busca un video, pon un video, quiero ver un video. «busca un
video» a veces no se reconoce; usar «pon un video».

**CERRAR_VIDEO:** cierra el video, quita el video, para el video. Para video usar
«pon un video».

## 6. Video por voz

«pon un video» → «¿qué video quiere ver?» → nombre del video → se abre en la
laptop; «cierra el video». Ayuda y gas cierran el video. Requiere en
`~/.config/hypr/autostart.conf`:

```text
exec-once = systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR
```

Se apaga con `VIDEO_HABILITADO=false` en el entorno del orquestador.

## 7. Si falla cada parte

- **Broker detenido.** `docker compose up -d mosquitto` (o `start`). El
  orquestador tiene sesión persistente: al volver, recibe lo acumulado.
- **Satélite sin audio.** Comprobar el micrófono y reiniciar:
  `systemctl --user restart asistente-satelite`. Ver el log con
  `journalctl --user -u asistente-satelite`.
- **ESP32 sin hora.** El firmware espera NTP de la VM (unos 15 s). Sin hora
  válida descarta las órdenes de luz del panel y de la voz; el botón sigue
  funcionando. Comprobar la red y reiniciar el ESP32.
- **Foco que no prende.** ¿Hace clic el relé? Si sí, alimentación del foco
  (power bank apagado; usar cargador de pared). Si no, nodo conectado en
  Equipos. El foco debe ir en NO, no en NC.
- **Cambios de ACL.** Recrear mosquitto, NUNCA SIGHUP.

Demo del gas: el MQ-2 real no llega al umbral (1800). En el monitor serie:
`GAS 3000` → buzzer continuo, aviso por voz, Telegram y panel; `GAS 300` →
termina en unos 10 s; pulsar `EN` para volver a la lectura real.

Monitor serie:

```bash
PIO=$(command -v pio || echo ~/.venvs/platformio/bin/pio)
$PIO device monitor -b 115200 --echo --filter send_on_enter
```

## 8. Respaldos

### Base de datos

No hay `sqlite3` en el contenedor ni en el host; se usa la API de backup de
Python:

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
R=orquestador-$(date +%Y%m%d-%H%M%S).sqlite3
docker compose exec orquestador python3 -c "
import sqlite3
src = sqlite3.connect('file:/datos/orquestador.sqlite3?mode=ro', uri=True)
dst = sqlite3.connect('/datos/respaldo-tmp.sqlite3')
src.backup(dst)
dst.close(); src.close()
"
docker cp asistente-orquestador:/datos/respaldo-tmp.sqlite3 \
  /home/ilse/Servidores/asistente-voz-secretos/respaldos/$R
docker compose exec orquestador rm /datos/respaldo-tmp.sqlite3
chmod 600 /home/ilse/Servidores/asistente-voz-secretos/respaldos/$R
python3 -c "
import sqlite3
c = sqlite3.connect('file:/home/ilse/Servidores/asistente-voz-secretos/respaldos/' + '$R' + '?mode=ro', uri=True)
print('integrity_check:', c.execute('PRAGMA integrity_check').fetchone()[0])
print('user_version:', c.execute('PRAGMA user_version').fetchone()[0])
print('alertas:', c.execute('SELECT COUNT(*) FROM alertas').fetchone()[0])
print('conversacion:', c.execute('SELECT COUNT(*) FROM conversacion').fetchone()[0])
c.close()
"
```

### NVS del ESP32

Cerrar el monitor serie antes. Rutas completas:

```bash
/home/ilse/Servidores/Asistente-Voz/herramientas/respaldo-nvs.sh respaldo
/home/ilse/Servidores/Asistente-Voz/herramientas/respaldo-nvs.sh restaurar <archivo.bin>
```

Restaurar **solo** el respaldo más reciente y **solo** si el contador volvió a 0.
Nunca borrar la flash.

## 9. Familiares y panel

- Panel: `https://asistente.tail722206.ts.net` y `http://192.168.1.169:8080`.
- Entrada al panel: en el bot, `/panel` da un código de 6 caracteres con botones
  «Copiar código» y «Abrir panel».
- Familiares: `/registro <código>` en Telegram, o desde la pestaña Familiares (solo
  administradores, `PANEL_ADMINS` en `servidor/.env`) con o sin Telegram (enlace
  de un solo uso, 72 h); desactivar desde ahí o con
  `python -m registro inactivar`. Cambiar nombre:
  `python -m registro nombre CHAT_ID "Nombre"`.

## 10. Rotación de claves y token del bot

Rotación de una contraseña MQTT (sin imprimir la clave):

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
MOSQUITTO_SECRETS_DIR=/home/ilse/Servidores/asistente-voz-secretos/mosquitto \
  /home/ilse/Servidores/Asistente-Voz/servidor/mosquitto/rotar-claves.py panel
```

Después, recrear mosquitto y el servicio de ese usuario (sin SIGHUP):

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
docker compose up -d --no-deps --force-recreate mosquitto
docker compose up -d --no-deps --force-recreate panel
```

Token del bot: revocar en BotFather, actualizar `TELEGRAM_BOT_TOKEN` en
`servidor/.env` y recrear el orquestador:

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
docker compose up -d --no-deps --force-recreate orquestador
```

## 11. Vuelta atrás de un despliegue (solo si algo falla)

1. Detener orquestador y panel:
   ```bash
   docker compose stop orquestador panel
   ```
2. Restaurar la base con sudo, usando la ruta real del respaldo:
   ```bash
   sudo cp /home/ilse/Servidores/asistente-voz-secretos/respaldos/<archivo>.sqlite3 \
     /var/lib/docker/volumes/asistente_orquestador_datos/_data/orquestador.sqlite3
   sudo chown 1000:1000 /var/lib/docker/volumes/asistente_orquestador_datos/_data/orquestador.sqlite3
   sudo chmod 600 /var/lib/docker/volumes/asistente_orquestador_datos/_data/orquestador.sqlite3
   sudo rm -f /var/lib/docker/volumes/asistente_orquestador_datos/_data/orquestador.sqlite3-wal \
              /var/lib/docker/volumes/asistente_orquestador_datos/_data/orquestador.sqlite3-shm
   ```
   Lectura del esquema con `mode=ro`:
   ```bash
   sudo python3 -c "import sqlite3; c=sqlite3.connect('file:/var/lib/docker/volumes/asistente_orquestador_datos/_data/orquestador.sqlite3?mode=ro', uri=True); print(c.execute('PRAGMA user_version').fetchone()[0])"
   ```
3. Retaggear las imágenes con la etiqueta del último despliegue:
   ```bash
   docker tag asistente-voz:<etiqueta> asistente-voz:1.0
   docker tag asistente-orquestador:<etiqueta> asistente-orquestador:1.0
   docker tag asistente-panel:<etiqueta> asistente-panel:1.0
   ```
4. Restaurar el `web_build` anterior.
5. Arrancar los tres con:
   ```bash
   docker compose up -d --no-deps --force-recreate orquestador panel voz
   ```
6. Verificar el esquema que tenía el respaldo (lectura en `mode=ro`).

## Lista de verificación antes de presentar

- [ ] Storyteller e Immich revisados (detener solo si procesan).
- [ ] ESP32 encendido al menos 3 minutos.
- [ ] Broker, orquestador, voz, panel y tailscale en `docker compose ps`.
- [ ] Receptor conectado y micrófono encendido.
- [ ] F9 probada.
- [ ] Satélite conectado (panel → Equipos).
- [ ] ESP32 «con hora» en Equipos.
- [ ] Cargador del foco conectado.
- [ ] Buzzer probado: GAS 3000, luego GAS 300 y pulsar EN.
- [ ] `/alertas` responde en Telegram.
- [ ] «Enciende la luz» funciona por voz.

## Después de la demo

- Volver a arrancar Storyteller e Immich si se detuvieron.
- Si el ESP32 se compartió con otro proyecto: respaldar la NVS al terminar y, al
  volver, revisar que el ESP32 arranca y reporta hora correctamente.
