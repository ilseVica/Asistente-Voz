#!/usr/bin/env bash
# Disparo manual del saludo diario (demo).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd -- "$SCRIPT_DIR/../servidor" && pwd)"

# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

MQTT_VOZ_PASSWORD="$(cargar_var_env "$SERVER_DIR/.env" MQTT_VOZ_PASSWORD)" || {
    echo "No se pudo cargar MQTT_VOZ_PASSWORD desde $SERVER_DIR/.env" >&2
    exit 1
}

mosquitto_pub -h 127.0.0.1 -p 1883 -u voz -P "${MQTT_VOZ_PASSWORD}" \
  -t "casa/sala/saludo/disparar" -m "{\"ts\":$(date +%s)}" 2>/dev/null || \
docker exec asistente-mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u voz -P "${MQTT_VOZ_PASSWORD}" -t "casa/sala/saludo/disparar" \
  -m "{\"ts\":$(date +%s)}"
