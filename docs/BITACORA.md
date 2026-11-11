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
