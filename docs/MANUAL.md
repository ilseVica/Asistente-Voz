# Manual del Asistente de Voz (Lucía)

Para alguien que no programó el sistema. Qué hay en cada equipo, cómo se usa,
cómo se arranca, cómo se comprueba que todo está bien, qué hacer si algo falla
y qué está apagado a propósito.

## 1. Qué hay en cada equipo

- **Servidor (VM).** Broker MQTT (`mosquitto`), orquestador, servicio de voz
  (Vosk), panel web y contenedor de Tailscale. La base SQLite está en el volumen
  `asistente_orquestador_datos` (`/datos/orquestador.sqlite3`).
- **Satélite.** Una laptop con Omarchy (Arch, Hyprland, PipeWire) y voz Piper
  (es_MX-claude-high). Corre `satelite/satelite.py` como servicio de **usuario**
  `asistente-satelite` (instalado en `~/.local/bin/asistente-satelite`).
  Su identificador es `satelite-sala`.
- **Nodo ESP32.** Sensores: DHT22 (GPIO 32), MQ-2 (GPIO 34), PIR (GPIO 35);
  relé (GPIO 26) con foco USB, buzzer (GPIO 27), botón BOOT = luz. Publica
  sensores, estado y alertas de gas; la inactividad la calcula el orquestador.
- **Móviles de la familia.** Reciben alertas por Telegram y usan el panel web con
  un código de un solo uso.
- **Panel web.** Estado de la casa (Equipos, alertas, historial), control de luz
  y marcar alertas como atendidas.
- **Conexiones del nodo:** `docs/CONEXIONES.md`.
- **Broker de la laptop (hotspot del iPhone):** Mosquitto 2.1 en `ilse`, IP fija
  172.20.10.10/28, bridge `puente-sitio` al broker del servidor
  (100.116.194.116:1883), usuario `puente`, QoS 1, cleansession false.
  Dispatcher `90-mosquitto-hotspot` reinicia Mosquitto al levantar el hotspot.
  Orden de conexión: laptop, luego Echo y ESP32; hotspot con «Maximizar
  compatibilidad».

## 2. Cómo hablar con Lucía

1. Diga «Lucía» y espere el tono corto. Solo entonces dé la orden.
2. Como alternativa, mantenga presionada **F9** o use el botón «Mantener para
   hablar» del panel (suena un tono en la sala y queda un aviso en la
   conversación).
3. No diga la orden en la misma frase: «Lucía, prende la luz» de corrido no
   funciona. Primero la palabra de activación, después la orden.
4. La palabra de activación «Lucía» funciona si está activa en la configuración
   del satélite (`lucia.activa = true` en `config.toml`). Si no responde, use F9
   o el botón del panel.
5. Si no entiende, repita la frase completa. Las frases incompletas (por ejemplo
   «prende») se responden con «no entendí».

## 3. Frases que entiende Lucía

Diga frases completas. No incluye las frases señuelo. Las frases son las de
`servidor/voz/gramatica.json`.

**ENCENDER_LUZ:** enciende la luz, prende la luz, enciende el foco, prende el
foco, enciende la luz de la sala, enciende la luz por favor, prende la luz por
favor, enciende el foco por favor, prende el foco por favor, prenda la luz,
encienda la luz, prenda el foco.

**APAGAR_LUZ:** apaga la luz, apaga el foco, apaga la luz de la sala, apaga la
luz por favor, apaga el foco por favor, apague la luz, apague el foco.

**AYUDA:** necesito ayuda, ayuda, ayudame, auxilio, ayudame por favor, ayuda por
favor, necesito auxilio, ayuda ya, auxilio ya, auxilio por favor, ayúdeme.

**CANCELAR:** cancela, olvidalo, olvídelo, cancele. «Cancela» deshace una
petición de ayuda hecha por voz en los 2 minutos anteriores; Telegram dice
«Falsa alarma».

**HORA:** que hora es, dime la hora, que horas son, que hora tiene, me dice la
hora, sabe que hora es, que hora tenemos. Para la hora use «dime la hora» o
«qué horas son».

**FECHA:** que dia es hoy, que fecha es hoy, que dia es, a cuantos estamos, que
fecha tenemos, que dia estamos.

**TEMPERATURA:** que temperatura hace, como esta el clima, cuantos grados hace,
hace calor, hace frio, que tal el clima.

**RECORDATORIOS:** tengo recordatorios, que tengo hoy, que recordatorios tengo,
tengo pendientes.

**TEMPORIZADOR:** pon un temporizador de un minuto, de dos minutos, de tres
minutos, de cinco minutos, de diez minutos, de quince minutos, de veinte
minutos, de treinta minutos, de cuarenta y cinco minutos, de sesenta minutos;
cuanto falta; cancela el temporizador.

**ESTADO_CASA:** como esta la casa, todo bien en la casa.

**REPITE:** repite, que dijiste, no te escuche, repite por favor, dilo otra vez,
otra vez por favor.

**BUENOS_DIAS:** buenos dias, buenos dias lucia, hola buenos dias.

**AGUA:** cuanta agua hay, nivel del agua, como esta el agua, cuanta agua queda,
cuanta agua tiene el tinaco.

**DORMIR:** ya me voy a dormir, me voy a dormir, ya me voy a acostar, me voy a
acostar, hasta mañana.

**LEVANTARSE:** ya me levanté.

**LLEGUE:** ya llegué, ya estoy en casa.

**AVISAR_BIEN (estoy bien):** avísale a mi familia que estoy bien, dile a mi
familia que estoy bien, avisa que estoy bien.

**SIENTO_MAL:** me siento mal, no me siento bien, no estoy bien, estoy mal.

**SALUDO_RESPUESTA (al saludo diario):** bien, muy bien, bien gracias, muy bien
gracias, estoy bien, más o menos, regular, no muy bien, mal, muy mal, me siento
bien, me siento muy mal.

**MUSICA (apagada para la demo):** pon musica, pon una cancion, quiero escuchar
musica, pon musica ranchera, pon boleros, pon musica tranquila.

**MUSICA_CONTROL (apagada para la demo):** sube el volumen, baja el volumen,
pausa la musica, sigue la musica, continua la musica, quita la musica.

**BUSCAR_VIDEO:** busca un video, pon un video, quiero ver un video, pon un video
en ingles, quiero ver un video en ingles. «busca un video» a veces no se
reconoce; use «pon un video».

**CERRAR_VIDEO:** cierra el video, quita el video, para el video.

### Mensajes de la familia

La familia escribe desde el panel o desde Telegram. Lucía dice el mensaje en voz
alta en la sala. Para responder por voz, diga «Lucía» y la respuesta dentro de
60 segundos; la respuesta aparece transcrita en el panel. La alerta de gas o de
ayuda cierra el video si estaba abierto.

### «Ya lo atendí»

Es el botón de Telegram (y del panel) para marcar una alerta como atendida. Al
pulsarlo, el mensaje se reescribe con la hora, la alerta queda resuelta y Lucía
avisa en la sala que alguien ya la atendió.

## 4. Saludo diario

Una vez al día Lucía pregunta «¿Cómo amaneció?» y, tras la pregunta, suena un
tono y se abre la escucha sola: se contesta directo («bien», «más o menos»,
«mal» o «ayuda») en los ~3 s siguientes, sin decir «Lucía». Si no hay respuesta,
repregunta una vez y luego espera al reintento de 10 min. Con el botón
«Mantener para hablar» no se dice «Lucía».

Si se responde «me siento mal» (o similar), se avisa a la familia.

- Se activa con `SALUDO_DIARIO_ACTIVO=1` en `servidor/.env` (por defecto está
  desactivado). Después se recrea el orquestador:
  `docker compose up -d --no-deps --force-recreate orquestador`.
- Para lanzarlo a mano: `herramientas/saludo.sh`, o publicando en
  `casa/sala/saludo/disparar`.

## 5. Lo que ve la familia

**En el panel web** (`https://asistente.tail722206.ts.net` o
`http://192.168.1.169:8080`): Equipos (nodo y satélite conectados, IP y hora del
ESP32), alertas activas y recientes, historial, mensajes para la sala, control de
la luz, recordatorios, ajuste de inactividad y lista de familiares.

**En Telegram:** alertas (gas, ayuda, inactividad, agua baja) con botones
«Abrir panel» y «Ya lo atendí»; comando `/alertas` (pendientes, enviadas y las
últimas cinco); `/panel` para entrar al panel.

### Cómo agregar un familiar

- **Con Telegram:** en el bot, `/panel` da un código de 6 caracteres con botones
  «Copiar código» y «Abrir panel». El familiar envía `/registro <código>` en su
  chat privado con el bot. Los administradores también pueden hacerlo desde la
  pestaña Familiares.
- **Sin Telegram (invitado):** un administrador (`PANEL_ADMINS` en
  `servidor/.env`) crea desde la pestaña Familiares un enlace de un solo uso,
  válido 72 h. Se puede desactivar desde ahí o con
  `python -m registro inactivar`.
- Cambiar el nombre de un familiar:
  `python -m registro nombre CHAT_ID "Nombre"`.

## 6. Arranque del sistema

### 6.1 Red de la casa (camino principal)

1. **VM y contenedores.** Arrancan solos (`restart: unless-stopped`). Comprobar:
   ```bash
   cd /home/ilse/Servidores/Asistente-Voz/servidor
   docker compose ps
   ```
   Todos deben estar «running/healthy»: mosquitto, orquestador, voz, panel,
   tailscale. Si alguno no está arriba: `docker compose up -d <servicio>`.
2. **Laptop.**
   - Conectada a corriente, sin suspensión ni bloqueo de pantalla.
   - Receptor USB-C del micrófono inalámbrico conectado y micrófono encendido
     **antes** de arrancar el satélite. Comprobar:
     ```bash
     pactl list short sources | grep -iv monitor
     ```
   - Tailscale conectado: `tailscale status`.
   - Arrancar el satélite (sin sudo):
     ```bash
     systemctl --user start asistente-satelite
     ```
3. **ESP32.** Encender con cargador de pared al menos 3 minutos antes (el MQ-2
   necesita estabilizarse), en la red de la casa. Comprobar en el panel →
   Equipos que aparezca «con hora».
4. **Comprobaciones finales.**
   - Panel → Equipos: nodo y satélite conectados; ESP32 «con hora».
   - Voz: decir «Lucía» → tono → «prende la luz»: la luz enciende y Lucía
     responde.
   - Telegram: `/alertas` responde.

### 6.2 Hotspot del iPhone (respaldo)

1. Revisar que Storyteller e Immich no tengan tareas en curso; detenerlos solo si
   están procesando.
2. Encender el ESP32 al menos 3 minutos antes.
3. Arrancar el servidor:
   ```bash
   cd /home/ilse/Servidores/Asistente-Voz/servidor
   docker compose up -d mosquitto orquestador voz panel tailscale
   ```
   (o `docker compose start` si ya están creados; revisar con `docker compose ps`).
4. Conectar el receptor USB-C del micrófono y encender el micrófono **antes**
   de arrancar el satélite. Comprobar con `pactl list short sources | grep -iv monitor`.
5. Arrancar el satélite (sin sudo): `systemctl --user start asistente-satelite`.
   Si el receptor se conectó después: `systemctl --user restart asistente-satelite`.
6. Revisar el log si hace falta: `journalctl --user -u asistente-satelite`.

Simular gas (ESP32 en el enchufe, sin monitor serie):
```bash
cd /home/ilse/Servidores/Asistente-Voz
./herramientas/gas.sh 3000
```
Se apaga sola a los 60 s. Para apagarla antes: `./herramientas/gas.sh 0`.

Simular nivel de agua (ESP32 en el enchufe, sin monitor serie):
```bash
cd /home/ilse/Servidores/Asistente-Voz
./herramientas/agua.sh 30
```
Se apaga sola a los 60 s. Para apagarla antes: `./herramientas/agua.sh 0` (o `REAL`).

Calibración del bote (nivel de agua):
1. Medir la distancia del sensor al fondo con el bote vacío = `AGUA_VACIO_CM`.
2. Medir la distancia del sensor a la superficie con el bote lleno = `AGUA_LLENO_CM`.
3. Editar `servidor/.env`: `AGUA_VACIO_CM=...` y `AGUA_LLENO_CM=...`.
4. Reiniciar el orquestador: `docker compose up -d orquestador` (publica
   `nodo/config` retenido).
5. El ESP32 guarda los valores en NVS. Valores por defecto: vacío 20 cm, lleno 5 cm.
   JSN-SR04T: cambiar `AGUA_ZONA_CIEGA_CM` (2 -> 20) y recalibrar.

## 7. Problemas comunes

- **Nivel de audio en 0.** El ReSpeaker está desconectado: reconéctelo y reinicie
  el satélite con `systemctl --user restart asistente-satelite`.
- **Satélite desconectado.** Revisar el panel → Equipos; reiniciar el servicio y
  ver el log con `journalctl --user -u asistente-satelite`.
- **Mosquitto de la laptop sin red.** Comprobar que escucha en 172.20.10.10:
  `ss -ltn 'sport = :1883'`; revisar el dispatcher `90-mosquitto-hotspot` y que
  el hotspot tenga «Maximizar compatibilidad» (2.4 GHz).
- **El foco no prende.** ¿Hace clic el relé? Si sí, alimentación del foco (power
  bank apagado; usar cargador de pared). Si no, nodo conectado en Equipos. El
  foco debe ir en NO, no en NC. Revisar los tornillos 2 y 3 del relé.
- **Agua «Sin sensor».** El HC-SR04 no tiene eco: revisar el cableado, que VCC
  esté a 5 V y la zona ciega (`AGUA_ZONA_CIEGA_CM`).
- **Gas en 0–35.** Cable flojo del MQ-2: reconectar y verificar el divisor.
- **ESP32 sin hora.** El firmware espera NTP de la VM (unos 15 s). Sin hora
  válida descarta las órdenes de luz del panel y de la voz; el botón sigue
  funcionando. Comprobar la red y reiniciar el ESP32.
- **Broker detenido.** `docker compose up -d mosquitto` (o `start`). El
  orquestador tiene sesión persistente: al volver, recibe lo acumulado.
- **Satélite sin audio.** Comprobar el micrófono y reiniciar:
  `systemctl --user restart asistente-satelite`.
- **Cambios de ACL.** Recrear mosquitto, NUNCA SIGHUP.

Demo del gas: el MQ-2 real no llega al umbral (1800). En el monitor serie:
`GAS 3000` → buzzer continuo, aviso por voz, Telegram y panel; `GAS 300` →
termina en unos 10 s; pulsar `EN` para volver a la lectura real.

Monitor serie:
```bash
PIO=$(command -v pio || echo ~/.venvs/platformio/bin/pio)
$PIO device monitor -b 115200 --echo --filter send_on_enter
```

## 8. Lo que está apagado y por qué

- **Música (`MUSICA_HABILITADO=0`).** Las frases de música existen, pero la
  orden no llega al satélite; se mantiene apagada para la demostración.
- **Palabra de activación «Lucía» (`lucia.activa = false` por defecto).** Se
  activa en `config.toml` si se quiere usar.
- **Video en inglés (`VIDEO_INGLES_HABILITADO=0` por defecto).**
- **APK de Android.** Fuera del alcance; en Android se usa la web.

## 9. Video por voz (opcional)

«pon un video» → «¿qué video quiere ver?» → nombre del video → se abre en la
laptop; «cierra el video». Ayuda y gas cierran el video. Requiere en
`~/.config/hypr/autostart.conf`:

```text
exec-once = systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR
```

Se apaga con `VIDEO_HABILITADO=false` en el entorno del orquestador.

## 10. Respaldos

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

## 11. Familiares y panel

- Panel: `https://asistente.tail722206.ts.net` y `http://192.168.1.169:8080`.
- Entrada al panel: en el bot, `/panel` da un código de 6 caracteres con botones
  «Copiar código» y «Abrir panel».
- Familiares: `/registro <código>` en Telegram, o desde la pestaña Familiares (solo
  administradores, `PANEL_ADMINS` en `servidor/.env`) con o sin Telegram (enlace
  de un solo uso, 72 h); desactivar desde ahí o con
  `python -m registro inactivar`. Cambiar nombre:
  `python -m registro nombre CHAT_ID "Nombre"`.

## 12. Rotación de claves y token del bot

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

## 13. Vuelta atrás de un despliegue (solo si algo falla)

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

## Lista de verificación antes de presentar

- [ ] Storyteller e Immich revisados (detener solo si procesan).
- [ ] ESP32 encendido al menos 3 minutos.
- [ ] Broker, orquestador, voz, panel y tailscale en `docker compose ps`.
- [ ] Receptor conectado y micrófono encendido.
- [ ] F9 probada.
- [ ] Satélite conectado (panel → Equipos).
- [ ] ESP32 «con hora» en Equipos.
- [ ] Cargador del foco conectado.
- [ ] Buzzer probado (T-35): GAS 3000 (alerta), luego GAS OFF (o esperar 60 s); la lectura vuelve sola, sin pulsar EN.
- [ ] `/alertas` responde en Telegram.
- [ ] «Enciende la luz» funciona por voz.
- [ ] `MUSICA_HABILITADO=0` y `SALUDO_DIARIO_ACTIVO` según lo previsto.
- [ ] Skill de Alexa (si `ALEXA_SKILL_HABILITADO=1`): «Alexa, abre asistente de casa» y «enciende la luz» responden; rollback disponible con `asistente-panel:pre-alexa`.

## Después de la demo

- Volver a arrancar Storyteller e Immich si se detuvieron.
- Si el ESP32 se compartió con otro proyecto: respaldar la NVS al terminar y, al
  volver, revisar que el ESP32 arranca y reporta hora correctamente.

## 14. Catálogo de canciones en inglés (T-56)

El término de búsqueda de «pon un video en inglés» ya no es libre: se reconoce
contra un catálogo cerrado en `servidor/voz/canciones_ingles.json`.

Agregar canciones:
1. Editar `servidor/voz/canciones_ingles.json` (lista de objetos
   `{"titulo": "...", "artista": "..."}`).
   - `titulo` en minúsculas, sin puntuación ni apóstrofos, tal como se dice.
   - `artista` no vacío.
2. Validar el vocabulario del modelo inglés:
   ```bash
   cd servidor/voz
   python validar_catalogo.py
   ```
   Los títulos con palabras fuera del vocabulario se omiten al arrancar el
   servicio de voz (se registra un aviso) y no se reconocen.
3. Activar la función:
   - `VIDEO_INGLES_HABILITADO=1` en `servidor/.env` (lo edita Ilse).
   - Reconstruir y recrear voz y orquestador: `docker compose build voz orquestador`
     y `docker compose up -d --no-deps voz orquestador`.

## Palabra de activación «Lucía»

El satélite puede escuchar continuamente la palabra «Lucía» (opción
`lucia.activa = true` en el config). Al detectarla, suena un tono corto y
empieza a escuchar la frase, igual que con F9. Si en 5 s no se dice nada, se
cierra sin responder. F9 y el botón «Mantener para hablar» del panel siguen
funcionando como respaldo. Por defecto está desactivada (`lucia.activa = false`).

## Saludo diario

Lucía pregunta una vez al día «¿Cómo amaneció?» y, tras la pregunta, suena un
tono y se abre la escucha sola: se contesta directo («bien», «más o menos»,
«mal» o «ayuda») en los ~3 s siguientes, sin decir «Lucía». Si no hay respuesta,
repregunta una vez y luego espera al reintento de 10 min. Se puede disparar
manualmente con `herramientas/saludo.sh` o publicando en
`casa/sala/saludo/disparar`.
