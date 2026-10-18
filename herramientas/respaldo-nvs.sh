#!/usr/bin/env bash
# Respaldo/restauración de la partición NVS del ESP32 (nodemcu-32s-test)
# Offset/tamaño: tabla compilada del build (.pio/build/.../partitions.bin
# vía gen_esp32part.py); si no existe el build, usa default.csv con aviso.
# Nunca hace erase_flash ni toca otras regiones.

set -uo pipefail

FIRMWARE="/home/ilse/Servidores/Asistente-Voz/firmware"
PARTS_BIN="$FIRMWARE/.pio/build/nodemcu-32s-test/partitions.bin"
GEN_PART="/home/ilse/.platformio/packages/framework-arduinoespressif32/tools/gen_esp32part.py"
PARTS_CSV="/home/ilse/.platformio/packages/framework-arduinoespressif32/tools/partitions/default.csv"
PORT="/dev/esp32"
ESPTOOL="/home/ilse/.platformio/packages/tool-esptoolpy/esptool.py"
BACKDIR="/home/ilse/Servidores/asistente-voz-secretos/respaldos"

uso() {
    cat <<'USO'
Uso:
  respaldo-nvs.sh respaldo
  respaldo-nvs.sh restaurar <archivo.bin>

Requisitos:
- Puerto /dev/esp32 libre antes de leer/escribir
- Usa offset/tamaño de la partición nvs de la tabla por defecto
- Nunca imprime contenido de NVS; nunca erase_flash
USO
}

comprobar_puerto_libre() {
    if [[ ! -c "$PORT" ]]; then
        echo "ERROR: puerto $PORT no existe o no es un char device" >&2
        exit 1
    fi
    if fuser -v "$PORT" 2>&1 | grep -q "$PORT"; then
        echo "ERROR: $PORT está ocupado" >&2
        fuser -v "$PORT" >&2
        echo "AVISO: NO cierres procesos ajenos sin preguntar." >&2
        exit 1
    fi
}

leer_nvs_offset_size() {
    local offset size
    if [[ -f "$PARTS_BIN" ]]; then
        local tabla
        tabla=$(python3 "$GEN_PART" "$PARTS_BIN" 2>/dev/null | grep '^nvs,')
        if [[ -n "$tabla" ]]; then
            offset=$(echo "$tabla" | awk -F, '{gsub(/[[:space:]]/, "", $4); print $4}')
            size=$(echo "$tabla" | awk -F, '{gsub(/[[:space:]]/, "", $5); print $5}')
            if [[ -n "$offset" && -n "$size" ]]; then
                # convertir sufijos K/M a bytes
                size=$(python3 -c "
import re,sys
s='$size'
m=re.fullmatch(r'(\d+)([KkMm]?)', s)
if not m: sys.exit(1)
n=int(m.group(1)); suf=m.group(2).upper()
if suf=='K': n*=1024
elif suf=='M': n*=1024*1024
print(n)
" 2>/dev/null) || { echo "ERROR: tamaño inválido: $size" >&2; exit 1; }
                echo "FUENTE: tabla compilada $PARTS_BIN" >&2
                echo "$offset $size"
                return 0
            fi
        fi
    fi
    echo "AVISO: no existe o no se pudo leer la tabla compilada $PARTS_BIN; usando default.csv" >&2
    [[ -f "$PARTS_CSV" ]] || { echo "ERROR: no existe $PARTS_CSV" >&2; exit 1; }
    offset=$(awk -F, '!/^[[:space:]]*#/ && /^[[:space:]]*nvs[[:space:]]*,/ {gsub(/[[:space:]]/, "", $4); print $4; exit}' "$PARTS_CSV")
    size=$(awk -F, '!/^[[:space:]]*#/ && /^[[:space:]]*nvs[[:space:]]*,/ {gsub(/[[:space:]]/, "", $5); print $5; exit}' "$PARTS_CSV")
    if [[ -z "$offset" || -z "$size" ]]; then
        echo "ERROR: no encuentro partición nvs en $PARTS_CSV" >&2
        exit 1
    fi
    size=$(python3 -c "
import re,sys
s='$size'
m=re.fullmatch(r'(\d+)([KkMm]?)', s)
if not m: sys.exit(1)
n=int(m.group(1)); suf=m.group(2).upper()
if suf=='K': n*=1024
elif suf=='M': n*=1024*1024
print(n)
" 2>/dev/null) || { echo "ERROR: tamaño inválido: $size" >&2; exit 1; }
    echo "$offset $size"
}

leer_contador_arranque() {
    python3 - "$1" <<'PY'
import struct, sys
try:
    data = open(sys.argv[1], 'rb').read()
except Exception:
    sys.exit(0)
val = None
page_size = 4096
for page_off in range(0, len(data), page_size):
    off = page_off + 32
    while off + 32 <= len(data) and off < page_off + page_size:
        nsIndex = data[off]
        etype = data[off+1]
        span = data[off+2]
        reserved = data[off+3]
        raw_key = data[off+8:off+24].split(b'\x00')[0]
        if etype == 0xff or span == 0 or reserved != 0xff:
            off += 32
            continue
        try:
            key = raw_key.decode('ascii')
        except Exception:
            key = ''
        if key == 'arranque' and etype == 4 and off+28 <= len(data):
            val = struct.unpack_from('<I', data, off+24)[0]
        off += span * 32
if val is not None:
    print(val)
PY
}

cmd_respaldo() {
    comprobar_puerto_libre
    local NVS_OFF NVS_SZ
    read -r NVS_OFF NVS_SZ < <(leer_nvs_offset_size)
    mkdir -p "$BACKDIR"
    local fecha
    fecha=$(date +%Y-%m-%d-%H%M)
    local dest="$BACKDIR/nvs-esp32-$fecha.bin"
    echo "NVS offset=$NVS_OFF size=$NVS_SZ"
    echo "Destino: $dest"
    python3 "$ESPTOOL" --port "$PORT" --baud 460800 read_flash "$NVS_OFF" "$NVS_SZ" "$dest"
    chmod 600 "$dest"
    echo "---"
    ls -l "$dest"
    stat -c '%y' "$dest" 2>/dev/null || stat "$dest" 2>&1 | head -1
    sha256sum "$dest"
    echo "---"
    echo "Respaldo listo. No generes alertas antes de desenchufar: el reinicio de esptool ya creó un arranque nuevo."
    local contador
    contador=$(leer_contador_arranque "$dest")
    if [[ -n "$contador" ]]; then
        echo "Contador de arranques en el archivo (última entrada 'arranque'): $contador"
    fi
}

cmd_restaurar() {
    local archivo="${1:-}"
    [[ -n "$archivo" ]] || { uso; exit 1; }
    [[ -f "$archivo" ]] || { echo "ERROR: no existe $archivo" >&2; exit 1; }
    comprobar_puerto_libre
    local NVS_OFF NVS_SZ
    read -r NVS_OFF NVS_SZ < <(leer_nvs_offset_size)
    echo "Archivo: $archivo"
    ls -l "$archivo"
    stat -c '%y' "$archivo" 2>/dev/null || stat "$archivo" 2>&1 | head -1
    sha256sum "$archivo"
    echo "---"
    echo "ATENCIÓN: Se ESCRIBIRÁ SOLO la partición NVS en $PORT"
    echo "Offset=$NVS_OFF Size=$NVS_SZ"
    echo "NO se ejecutará erase_flash ni se tocarán otras regiones."
    echo "Escribe 'restaurar' para continuar (cualquier otra cosa cancela):"
    read -r resp
    if [[ "$resp" != "restaurar" ]]; then
        echo "Cancelado."
        exit 1
    fi
    python3 "$ESPTOOL" --port "$PORT" --baud 460800 write_flash "$NVS_OFF" "$NVS_SZ" "$archivo"
    echo "Restauración completada (solo región NVS)."
}

main() {
    local cmd="${1:-}"
    [[ -n "$cmd" ]] || { uso; exit 1; }
    case "$cmd" in
        respaldo)
            cmd_respaldo
            ;;
        restaurar)
            cmd_restaurar "${2:-}"
            ;;
        *)
            uso; exit 1
            ;;
    esac
}

main "$@"
