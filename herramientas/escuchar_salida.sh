#!/usr/bin/env bash
# Sirve la carpeta de salida del satelite de la VM para escucharla desde el iPad.
#
#   herramientas/escuchar_salida.sh iniciar    # levanta el servidor en segundo plano
#   herramientas/escuchar_salida.sh detener    # lo detiene
#   herramientas/escuchar_salida.sh estado     # dice si esta encendido
#
# Es TEMPORAL: se levanta para una prueba y se detiene al terminar. No es un
# servicio y no arranca solo.
#
# Escucha SOLO en la IP de la casa (192.168.1.169:8099). Nunca en 0.0.0.0, ni en
# la IP de Tailscale, ni por Funnel: los WAV son lo que dijo el asistente en la
# casa. Basta el listado de directorio de `python3 -m http.server`; el iPad
# reproduce los WAV desde Safari.

set -euo pipefail

DIRECCION="${ESCUCHAR_DIRECCION:-192.168.1.169}"
PUERTO="${ESCUCHAR_PUERTO:-8099}"
CARPETA="${ESCUCHAR_CARPETA:-${HOME}/asistente-satelite-vm/salida}"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/asistente-escuchar-salida.pid"
LOG="${XDG_RUNTIME_DIR:-/tmp}/asistente-escuchar-salida.log"

case "${DIRECCION}" in
  0.0.0.0|::|"")
    echo "ERROR: no se escucha en todas las interfaces (${DIRECCION:-vacio})." >&2
    exit 1 ;;
  100.*)
    echo "ERROR: ${DIRECCION} parece de Tailscale; esto es solo para la red de la casa." >&2
    exit 1 ;;
esac

vivo() {
  [ -f "${PIDFILE}" ] && kill -0 "$(cat "${PIDFILE}")" 2>/dev/null
}

case "${1:-}" in
  iniciar)
    if vivo; then
      echo "Ya esta encendido (pid $(cat "${PIDFILE}")): http://${DIRECCION}:${PUERTO}/"
      exit 0
    fi
    [ -d "${CARPETA}" ] || { echo "ERROR: no existe ${CARPETA}" >&2; exit 1; }
    nohup python3 -m http.server "${PUERTO}" --bind "${DIRECCION}" \
      --directory "${CARPETA}" >"${LOG}" 2>&1 &
    echo $! >"${PIDFILE}"
    sleep 0.5
    if vivo; then
      echo "Encendido (pid $(cat "${PIDFILE}")): http://${DIRECCION}:${PUERTO}/"
      echo "Para detenerlo: $0 detener"
    else
      echo "ERROR: no arranco. Registro:" >&2
      cat "${LOG}" >&2
      rm -f "${PIDFILE}"
      exit 1
    fi
    ;;
  detener)
    if vivo; then
      kill "$(cat "${PIDFILE}")"
      echo "Detenido (pid $(cat "${PIDFILE}"))."
    else
      echo "No estaba encendido."
    fi
    rm -f "${PIDFILE}" "${LOG}"
    ;;
  estado)
    if vivo; then
      echo "Encendido (pid $(cat "${PIDFILE}")): http://${DIRECCION}:${PUERTO}/"
    else
      echo "Apagado."
    fi
    ;;
  *)
    echo "uso: $0 iniciar|detener|estado" >&2
    exit 2 ;;
esac
