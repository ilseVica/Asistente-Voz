#!/usr/bin/env bash
# Instalador del satelite de voz (etapa B del Paso 2) para Arch Linux / Omarchy.
#
# Deja todo listo para otra computadora menos los secretos: NO pide contrasenas ni
# tokens por la linea de comandos. Al terminar, imprime los pasos que faltan (pegar
# el token y la contrasena en el config, los atajos de Hyprland, enable-linger y el
# servicio).
#
# Que hace:
#   1. Instala `uv` en el espacio del usuario (sin sudo) si falta.
#   2. Instala Python 3.12 y sincroniza el entorno del satelite (websockets y
#      paho-mqtt con version fija).
#   3. Descarga Piper 2023.11.14-2 y la voz es_MX-ald-medium, comprobando SHA-256.
#   4. Crea ~/.config/asistente-satelite/config.toml a partir de la plantilla.
#   5. Instala el lanzador y el aviso de tecla en ~/.local/bin.
#   6. Copia la unidad de systemd de usuario.
#
# Es idempotente: volver a ejecutarlo no rompe lo ya hecho.

set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="${HOME}/.local/share/asistente-satelite"
PIPER_DIR="${DESTINO}/piper"
VOCES_DIR="${DESTINO}/voces"
CONFIG_DIR="${HOME}/.config/asistente-satelite"
BIN_DIR="${HOME}/.local/bin"
UNIT_DIR="${HOME}/.config/systemd/user"

PIPER_TARBALL_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
PIPER_BIN_SHA256="12672a94ca6716e5a8f335cfa68bf43bd9a33284960e3f9d16b85090bf7aab6b"

VOZ_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/ald/medium"
VOZ_ONNX_SHA256="019b3803293c93e34a206dd2e53a3889209a514e786fd7144f7b70196c579b63"
VOZ_JSON_SHA256="5a71498158e04afc8099bfd019c7e87c68eb9d042505a2b1a87e5c1ac2b1a61d"

log()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
aviso(){ printf '\033[33mAVISO:\033[0m %s\n' "$*" >&2; }
fallo(){ printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

comprobar_sha() {
  local archivo="$1" esperado="$2"
  local real
  real="$(sha256sum "${archivo}" | awk '{print $1}')"
  [ "${real}" = "${esperado}" ] || \
    fallo "SHA-256 no coincide para ${archivo}: ${real} != ${esperado}"
}

# --- 0. Plataforma ----------------------------------------------------------
if ! command -v pacman >/dev/null 2>&1; then
  aviso "No parece Arch (no hay pacman). El script es para Arch/Omarchy."
fi
for herramienta in curl tar sha256sum; do
  command -v "${herramienta}" >/dev/null 2>&1 || \
    fallo "Falta '${herramienta}'. Instalalo (p. ej. sudo pacman -S curl tar coreutils)."
done

# --- 1. uv ------------------------------------------------------------------
export PATH="${BIN_DIR}:${PATH}"
if ! command -v uv >/dev/null 2>&1; then
  log "Instalando uv en ${BIN_DIR} (sin sudo)..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  export PATH="${BIN_DIR}:${PATH}"
fi
command -v uv >/dev/null 2>&1 || fallo "uv no quedo en el PATH."
log "uv: $(uv --version)"

# --- 2. Entorno de Python 3.12 ---------------------------------------------
log "Instalando Python 3.12 con uv..."
uv python install 3.12
log "Sincronizando el entorno del satelite..."
( cd "${AQUI}" && uv sync )
VENV_PY="${AQUI}/.venv/bin/python"
[ -x "${VENV_PY}" ] || fallo "No se creo el entorno en ${AQUI}/.venv"

# --- 3. Piper y la voz ------------------------------------------------------
mkdir -p "${DESTINO}" "${VOCES_DIR}"

if [ ! -x "${PIPER_DIR}/piper" ]; then
  log "Descargando Piper 2023.11.14-2..."
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp}"' EXIT
  curl -fSL --retry 3 -o "${tmp}/piper.tar.gz" "${PIPER_TARBALL_URL}"
  tar -xzf "${tmp}/piper.tar.gz" -C "${tmp}"
  # El tarball trae una carpeta piper/ con el binario y sus bibliotecas.
  rm -rf "${PIPER_DIR}"
  if [ -d "${tmp}/piper" ]; then
    mv "${tmp}/piper" "${PIPER_DIR}"
  else
    mkdir -p "${PIPER_DIR}"
    cp -a "${tmp}"/. "${PIPER_DIR}/"
  fi
  chmod +x "${PIPER_DIR}/piper"
else
  log "Piper ya esta en ${PIPER_DIR}."
fi
comprobar_sha "${PIPER_DIR}/piper" "${PIPER_BIN_SHA256}"

if [ ! -f "${VOCES_DIR}/es_MX-ald-medium.onnx" ]; then
  log "Descargando la voz es_MX-ald-medium..."
  curl -fSL --retry 3 -o "${VOCES_DIR}/es_MX-ald-medium.onnx" \
    "${VOZ_BASE}/es_MX-ald-medium.onnx"
  curl -fSL --retry 3 -o "${VOCES_DIR}/es_MX-ald-medium.onnx.json" \
    "${VOZ_BASE}/es_MX-ald-medium.onnx.json"
else
  log "La voz ya esta en ${VOCES_DIR}."
fi
comprobar_sha "${VOCES_DIR}/es_MX-ald-medium.onnx" "${VOZ_ONNX_SHA256}"
comprobar_sha "${VOCES_DIR}/es_MX-ald-medium.onnx.json" "${VOZ_JSON_SHA256}"

# --- 4. Configuracion -------------------------------------------------------
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_DIR}/config.toml" ]; then
  cp "${AQUI}/config.example.toml" "${CONFIG_DIR}/config.toml"
  chmod 600 "${CONFIG_DIR}/config.toml"
  log "Plantilla copiada a ${CONFIG_DIR}/config.toml (permisos 600)."
else
  log "Ya existe ${CONFIG_DIR}/config.toml; no se toca."
fi

# --- 5. Lanzadores ----------------------------------------------------------
mkdir -p "${BIN_DIR}"
cat > "${BIN_DIR}/asistente-satelite" <<LANZADOR
#!/bin/sh
# Lanzador del satelite. Lo crea instalar.sh.
exec "${BIN_DIR}/uv" run --directory "${AQUI}" python satelite.py \
  --config "${CONFIG_DIR}/config.toml" "\$@"
LANZADOR
chmod +x "${BIN_DIR}/asistente-satelite"

cat > "${BIN_DIR}/asistente-tecla" <<TECLA
#!/bin/sh
# Aviso corto de la tecla para Hyprland. Lo crea instalar.sh.
exec /usr/bin/python3 "${AQUI}/tecla.py" "\$@"
TECLA
chmod +x "${BIN_DIR}/asistente-tecla"
log "Lanzadores en ${BIN_DIR}/asistente-satelite y ${BIN_DIR}/asistente-tecla"

# --- 6. Unidad de systemd de usuario ---------------------------------------
mkdir -p "${UNIT_DIR}"
cp "${AQUI}/asistente-satelite.service" "${UNIT_DIR}/asistente-satelite.service"

cat <<FIN

============================================================================
El satelite esta instalado. Faltan los pasos que necesita una persona:

1) Editar la configuracion (secreto ahi, nunca en el chat):

     \$EDITOR ${CONFIG_DIR}/config.toml

   Poner ws_url y mqtt.host, el token (token_archivo o token) y la contrasena
   (password_archivo o password). Elegir los nodos de PipeWire de entrada y
   salida con:

     wpctl status        (o:  pw-cli ls Node | grep node.name)

2) Atajos de Hyprland (mantener presionada la tecla para hablar). En el archivo
   de atajos de Omarchy:

     bind   = , F9, exec, ${BIN_DIR}/asistente-tecla presionar
     bindr  = , F9, exec, ${BIN_DIR}/asistente-tecla soltar

   (Cambiar F9 por la tecla que se quiera.)

3) Arrancar sin iniciar sesion y habilitar el servicio:

     loginctl enable-linger \$USER
     systemctl --user daemon-reload
     systemctl --user enable --now asistente-satelite.service

   Ver el estado y el registro:

     systemctl --user status asistente-satelite.service
     journalctl --user -u asistente-satelite -f

4) Resumen de latencias:

     ${VENV_PY} ${AQUI}/latencias.py
============================================================================
FIN
