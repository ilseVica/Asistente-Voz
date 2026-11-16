#!/usr/bin/env bash
# Simula un nivel de agua en el ESP32 publicando en casa/sala/nodo/prueba_agua.
# Uso: herramientas/agua.sh <agua_pct>   (0..100)
# La simulacion se apaga sola a los 60 s.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd -- "$SCRIPT_DIR/../servidor" && pwd)"

# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

if [[ $# -ne 1 ]] || ! [[ "$1" =~ ^[0-9]+$ ]] || (( $1 < 0 || $1 > 100 )); then
    echo "Uso: $0 <agua_pct>   (entero entre 0 y 100)" >&2
    echo "La simulacion se apaga sola a los 60 s." >&2
    exit 2
fi

VALOR="$1"
PASS="$(cargar_var_env "$SERVER_DIR/.env" MQTT_PUENTE_PASSWORD)" || {
    echo "No se pudo cargar MQTT_PUENTE_PASSWORD desde $SERVER_DIR/.env" >&2
    exit 1
}

(
    cd "$SERVER_DIR"
    docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -u puente \
        -P "$PASS" -t casa/sala/nodo/prueba_agua -q 1 \
        -m "{\"agua_pct\":$VALOR}"
)

unset PASS
