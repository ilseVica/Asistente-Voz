#!/usr/bin/env bash
# Simula una lectura de gas en el ESP32 publicando en casa/sala/nodo/prueba_gas.
# Uso: herramientas/gas.sh <gas_adc>   (1..4095)
# La simulacion se apaga sola a los 60 s.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd -- "$SCRIPT_DIR/../servidor" && pwd)"

if [[ $# -ne 1 ]] || ! [[ "$1" =~ ^[0-9]+$ ]] || (( $1 < 1 || $1 > 4095 )); then
    echo "Uso: $0 <gas_adc>   (entero entre 1 y 4095)" >&2
    echo "La simulacion se apaga sola a los 60 s." >&2
    exit 2
fi

VALOR="$1"
PASS="$(grep '^MQTT_PUENTE_PASSWORD=' "$SERVER_DIR/.env" | cut -d= -f2-)"
PASS="${PASS%\"}"; PASS="${PASS#\"}"

(
    cd "$SERVER_DIR"
    docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u puente \
        -P "$PASS" -t casa/sala/nodo/prueba_gas -q 1 \
        -m "{\"gas_adc\":$VALOR}"
)

unset PASS
