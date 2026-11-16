# Bitácora del proyecto

## 31 de octubre de 2026

- Foco revisado: cables COM/NO del relé.
- Puerto serie remoto de la laptop a la VM por Tailscale (esp_rfc2217_server).
- fauxmoESP no funciona con el Echo Pop: se decide reemplazarlo por la skill de Alexa.
- Firmware de tres redes (commit d4dd914).
- Respaldo de la NVS y regrabado del asistente tras cargar el código del carrito (NVS conservada).
- Diseño de la torre del nodo.

## 1 de noviembre de 2026

- Broker MQTT de la laptop para el hotspot del iPhone (commit 2b9d029): Mosquitto 2.1 en `ilse`, listeners loopback y Wi-Fi del hotspot, IP fija 172.20.10.10/28, dispatcher `90-mosquitto-hotspot`, bridge `puente-sitio` → 100.116.194.116:1883 con usuario `puente`, QoS 1, cleansession false. Riesgos documentados: si cae el bridge el panel sigue mostrando el nodo conectado; orden de conexión laptop, luego Echo y ESP32; hotspot con «Maximizar compatibilidad». No se escriben contraseñas.
- Selector de red por serie (commit 170a57c): comandos `RED`, `RED 1|2|3`, `RED AUTO`; forzado en RTC; vuelta a AUTO a los 60 s sin Wi-Fi + broker; campos `red` y `modo_red` en `nodo/estado` (el panel aún no los muestra).
- Skill de Alexa Fase A (commit 42df57f): skill Custom, Spanish (MX), invocación «asistente de casa», endpoint `https://asistente.tail722206.ts.net/alexa`, variables `ALEXA_SKILL_ID` / `ALEXA_SKILL_HABILITADO` / `ALEXA_VIDEO_HABILITADO`, rollback con la imagen `asistente-panel:pre-alexa` (docs/alexa/consola.md y pruebas.md).
- Pruebas marcadas como cumplidas: P05, P06, P21, P22, P23.

## 11 de noviembre de 2026

- Diagnóstico de zona horaria en la intención HORA: el error estaba en `_hora_en_palabras` (`servidor/orquestador/voz.py`), rama `hora == 0` con minuto ≠ 0, que decía «la una» en vez de «las doce». Corregido a «Son las doce y {minutos} de la noche.». 00:00 sigue siendo «medianoche». Pruebas: 688 comprobaciones, 0 fallos.
- ESP32 (pendiente, NO grabado hoy): `ZONA_HORARIA "America/Mazatlan"` (`firmware/include/config.h`) no lo entiende newlib del ESP32; `setenv("TZ", ...)` no surte efecto y los timestamps del monitor serie salen en UTC. Lo correcto es la cadena POSIX `"MST7"` (UTC−7 sin horario de verano). Solo afecta la hora de los logs del monitor serie; no afecta el ts epoch de las órdenes ni la validación. Se corrige en la próxima grabación, junto con respaldo de NVS y sin erase.

## 6 de noviembre de 2026

- Identidad visual «Lucía»: nombre, personaje y paleta (según Ilse; sin commit ni reporte en el repositorio).
- Diseño de la torre del nodo y de la lámpara de cascarón (según Ilse).

## 8 de noviembre de 2026

- Sensor de agua HC-SR04 habilitado y calibrado en la NVS (22/5 cm); soporte sobre la cubeta (según Ilse; firmware grabado el 8 nov, commit cf406b2 del 11 nov).

## 9 y 10 de noviembre de 2026

- El foco no prendía por mal contacto en los tornillos 2 y 3 del relé; reacomodo de cables (los pines del ESP32 no cambiaron; commit 99034ab del 15 nov).
- «Sin sensor» del agua corregido revisando el cableado (según Ilse).
- Sensor de agua HC-SR04 cableado en `docs/CONEXIONES.md` (commit cf406b2, 11 nov).

## 11 de noviembre de 2026

- Hora 00:xx corregida: se decía «la una»; ahora «las doce … de la noche» (commit ad4bec2).
- El default de `config.py` pisaba la NVS del agua → recalibrado a 20/5 cm (commit a7e7483).
- Palabra de activación «Lucía» en la laptop: luz 10 de 10 y responder mensajes con «Lucía» (commit 24847c4).
- Integración de Lucía en el satélite: escucha continua, tono, cierre por silencio o a los 5 s, saludo y modo depurar (commit e3e5b7c).
- Agua habilitada en el firmware (commit cf406b2).
- Panel: log por petición con familiar enmascarado (commit 92f0c4b).

## 12 y 13 de noviembre de 2026

- Gramática ampliada: 110 → 146 frases; «ayúdeme por favor» queda fuera de AYUDA y «auxilio por favor» pasa a AYUDA (commits c1ce8b6, c623323).
- Repite, «cómo está la casa», temporizador y «buenos días» con efeméride (saludo por hora, hora en palabras, temperatura, efemérides 13–30 nov y 40 curiosidades); textos «llena» al 100 % y «diga Lucía» (commit c623323).
- Mosquitto de la laptop con `espera-red.conf` (commit 888c56f).
- La presentación se movió al miércoles 18 (commit 9389d9e, 14 nov).
- Música (tanda 3) sin funcionar; queda detrás de `MUSICA_HABILITADO` (por defecto 0) (commit 888c56f).

## 13 de noviembre de 2026 (noche)

- Merge de `agua-habilitado` a `master` (commit 9dcc3af).
- Música apagada para la demo (`MUSICA_HABILITADO=0`) (commit 888c56f).

## 14 de noviembre de 2026

- «Lucía cuida» desplegada: frases de la vida diaria (Parte A) y saludo diario apagado por defecto (commits e8cc4f8, a5a527f, e5f3189, a5aa16f); se quitó «ya desperté» por confundirse con «repite».
- Arnés sintético: 87 % de acierto (commit a5a527f; reporte en `resultados/`).
- Panel desde fuera aprobado (P57, validación manual 14 nov).
- Manual, guion de 10 minutos y tabla de pruebas para la demo del 18 nov (commit 3f6f709).
- Respaldo de la base e imágenes `:antes-lucia` (según Ilse).

## 15 de noviembre de 2026

- Servidor apagado → Mosquitto no arrancó al encender (Exited 0) y se levantó a mano; P25 y P59 aprobadas.
- Panel rediseñado: base neutra, marca «Lucía» con flamita, agua junto a movimiento, pestaña con línea rosa (commits 8f166e7, d9a2389, 507fe47, 8a0505e, 595f691, 4f5b19c).
- Los scripts cargan solos la contraseña de MQTT (commit 01ce1cb).
- Tabla de pruebas cerrada (P56 y P13 pendientes) (commits 42a8eac, 7780a5b, f71dfd7).
- `GUION.md` eliminado del repositorio (commit 71174a6).
- `CONEXIONES.md` con el reacomodo (commit 99034ab).

## Lecciones

- Tras encender el servidor, revisar `docker compose ps`: el 15 nov Mosquitto quedó en `Exited (0)` y todo el sistema quedó caído hasta levantarlo a mano.
- Los valores por defecto del código (p. ej. `config.py` con 30 cm para el agua) pueden pisar la NVS calibrada; hay que quitarlos o documentarlos.
- En una gramática cerrada, frases cortas se confunden entre sí («ya desperté» con «repite»); conviene probar con voz real y quitar las ambiguas.
- Un hotspot de respaldo con distinto nombre o un Wi-Fi abierto no presente en el firmware no sirve para la demo; hay que probar la red real de punta a punta.
- El reacomodo físico de cables puede dejar el mismo firmware funcionando o no; revisar el cableado antes de culpar al código («Sin sensor» del agua).
- Un tópico atendido en el despacho (`orquestador.enrutar`) pero ausente de la lista de suscripciones de `mqtt_cliente.suscribirse` es código muerto sin ruido: el broker nunca entrega el mensaje y ninguna prueba lo ve, porque el cliente falso de `pruebas.py` entrega directo sin pasar por la suscripción. A partir de ahora, toda prueba de despacho debe comprobar que el tópico esté en las suscripciones.

- `acl.conf` es un bind mount de un solo archivo (`./mosquitto/config/acl.conf:/mosquitto/config/acl.conf:ro` en `servidor/docker-compose.yml`). Cualquier herramienta que lo reemplace (editor que escribe un archivo nuevo, `git checkout`, un commit que lo regenera) deja al contenedor con el inodo viejo; el `SIGHUP` no lo arregla y Mosquitto sigue con la ACL anterior. Hay que editar en sitio (`sed -i`, `>>`) o recrear el contenedor. Conviene, a futuro, montar la carpeta (`./mosquitto/config:/mosquitto/config:ro`) en lugar del archivo, para que un archivo nuevo se vea; no se cambia ahora.

## 16 de noviembre de 2026

- Despliegue del arreglo del saludo manual: el orquestador no estaba suscrito a `casa/sala/saludo/disparar` (el despacho lo atendía, pero el mensaje nunca llegaba) y la ACL no permitía al usuario `voz` publicarlo ni al `orquestador` leerlo. Corregido en `065ed50`/`d0bcffe` y ACL `d5f2ec9`/`af08395`; recargada con `docker kill -s HUP`.
- P56 aprobada con la voz real («ayuda» ×3, «no me siento bien», saludo manual a las 13:34: `SALUDO_RESPUESTA` y `ALERTA BIENESTAR srv-bienestar-1794861274`). Limitación: el micrófono no se abre solo tras «¿Cómo amaneció?»; hay que decir «Lucía» antes.
- P13 aprobada: recordatorio «prueba 13» cancelado desde el panel; no sonó.
- Tabla de pruebas: 39 aprobadas, 0 pendientes (P56 y P13 cerradas).


## 16 de noviembre de 2026 (documentación)

- MANUAL.md: el saludo diario ya no necesita «Lucía»; Lucía pregunta, suena el tono y se contesta directo en los ~3 s siguientes; si no hay respuesta, repregunta una vez y espera al reintento de 10 min. «Ayuda» dentro de la escucha manda la alerta de ayuda. Con el botón «Mantener para hablar» no se dice «Lucía».
- P56 cerrada con la versión 008ea23 y satélite md5 e970230d: A («mal» → bienestar), B (silencio → timeout → repregunta con escucha → timeout → silencio), D («ayuda» → alerta de ayuda), C («Lucía» → «qué hora es» sin cambios). Pruebas: orquestador 751, satélite 99.
