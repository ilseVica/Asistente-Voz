#!/usr/bin/env bash
# Descarga el modelo de Vosk que usa el servicio de voz.
#
# El modelo NO se versiona: son decenas de MB que no cambian y que no tiene
# sentido guardar en el repositorio. Este guion lo baja, comprueba el peso y el
# SHA-256 y lo descomprime en modelos/, que esta en .gitignore. El servicio lo
# monta en solo-lectura (ver docker-compose.yml).
#
# El SHA-256 es el del zip de alphacephei (el oficial). alphacephei no publica
# checksum, asi que se fijo midiendo la descarga una vez (2026-10-11) y coincide
# con el espejo rhasspy/vosk-models. Si el archivo cambia, la verificacion falla
# y hay que revisar de donde viene el cambio antes de seguir.

set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="${AQUI}/modelos"
NOMBRE="vosk-model-small-es-0.42"
URL="https://alphacephei.com/vosk/models/${NOMBRE}.zip"
PESO_ESPERADO=39817833
SHA256_ESPERADO="09b239888f633ef2f0b4e09736e3d9936acfd810bc65d53fad45261762c6511f"

mkdir -p "${DESTINO}"

if [ -d "${DESTINO}/${NOMBRE}" ]; then
  echo "El modelo ya esta en ${DESTINO}/${NOMBRE}; nada que hacer."
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "Descargando ${URL} ..."
curl -fSL --retry 3 -o "${TMP}/${NOMBRE}.zip" "${URL}"

PESO="$(stat -c%s "${TMP}/${NOMBRE}.zip")"
if [ "${PESO}" != "${PESO_ESPERADO}" ]; then
  echo "ERROR: peso inesperado (${PESO} != ${PESO_ESPERADO} bytes)." >&2
  exit 1
fi

if ! echo "${SHA256_ESPERADO}  ${TMP}/${NOMBRE}.zip" | sha256sum -c -; then
  echo "ERROR: el SHA-256 no coincide. No se descomprime nada." >&2
  exit 1
fi

echo "Descomprimiendo en ${DESTINO}/ ..."
unzip -q "${TMP}/${NOMBRE}.zip" -d "${DESTINO}"

echo "Listo: ${DESTINO}/${NOMBRE}"

# Modelo en ingles (T-56). Solo se descarga si se va a usar; el servicio lo
# carga unicamente con VIDEO_INGLES_HABILITADO=1.
NOMBRE_EN="vosk-model-small-en-us-0.15"
URL_EN="https://alphacephei.com/vosk/models/${NOMBRE_EN}.zip"
PESO_ESPERADO_EN=41205931
SHA256_ESPERADO_EN="30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"

if [ -d "${DESTINO}/${NOMBRE_EN}" ]; then
  echo "El modelo ingles ya esta en ${DESTINO}/${NOMBRE_EN}; nada que hacer."
else
  TMP_EN="$(mktemp -d)"
  trap 'rm -rf "${TMP_EN}"' EXIT
  echo "Descargando ${URL_EN} ..."
  curl -fSL --retry 3 -o "${TMP_EN}/${NOMBRE_EN}.zip" "${URL_EN}"

  PESO_EN="$(stat -c%s "${TMP_EN}/${NOMBRE_EN}.zip")"
  if [ "${PESO_EN}" != "${PESO_ESPERADO_EN}" ]; then
    echo "ERROR: peso inesperado del modelo ingles (${PESO_EN} != ${PESO_ESPERADO_EN} bytes)." >&2
    exit 1
  fi

  if ! echo "${SHA256_ESPERADO_EN}  ${TMP_EN}/${NOMBRE_EN}.zip" | sha256sum -c -; then
    echo "ERROR: el SHA-256 del modelo ingles no coincide. No se descomprime nada." >&2
    exit 1
  fi

  echo "Descomprimiendo modelo ingles en ${DESTINO}/ ..."
  unzip -q "${TMP_EN}/${NOMBRE_EN}.zip" -d "${DESTINO}"
  echo "Listo: ${DESTINO}/${NOMBRE_EN}"
  rm -rf "${TMP_EN}"
fi
