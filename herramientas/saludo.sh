#!/usr/bin/env bash
# Disparo manual del saludo diario (demo).
set -euo pipefail
mosquitto_pub -h 127.0.0.1 -p 1883 -u voz -P "${MQTT_VOZ_PASSWORD}" \
  -t "casa/sala/saludo/disparar" -m "{\"ts\":$(date +%s)}" 2>/dev/null || \
docker exec asistente-mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u voz -P "${MQTT_VOZ_PASSWORD}" -t "casa/sala/saludo/disparar" \
  -m "{\"ts\":$(date +%s)}"
