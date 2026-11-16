#!/usr/bin/env bash
# Apoyo para operar la demostracion de 10 minutos.
#
# Uso:
#   herramientas/guion.sh            # interactivo: Enter ejecuta cada paso
#   herramientas/guion.sh --ensayo   # solo muestra los pasos, no ejecuta nada
#
# No hace nada irreversible: solo lanza los simuladores y el saludo manual, y
# al final deja el gas y el agua en lectura normal.

set -u

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$RAIZ/servidor"
SCRIPT_DIR="$RAIZ/herramientas"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
MQTT_VOZ_PASSWORD="$(cargar_var_env "$SERVER_DIR/.env" MQTT_VOZ_PASSWORD)" || {
  echo "No se pudo cargar MQTT_VOZ_PASSWORD desde $SERVER_DIR/.env" >&2
  exit 1
}
ENSAYO=0
if [[ "${1:-}" == "--ensayo" ]]; then
  ENSAYO=1
fi

paso() {
  local num="$1" titulo="$2" dice="$3" debe="$4" respaldo="${5:-}"
  printf '\n\033[1m== Paso %s — %s ==\033[0m\n' "$num" "$titulo"
  printf '   Qué se dice/hace: %s\n' "$dice"
  printf '   Qué debe pasar:   %s\n' "$debe"
  if [[ -n "$respaldo" ]]; then
    printf '   Respaldo:         %s\n' "$respaldo"
  fi
  if [[ "$ENSAYO" -eq 1 ]]; then
    printf '   (--ensayo: sin ejecutar)\n'
    return 0
  fi
  printf '   Pulse Enter para continuar (o Ctrl+C para salir)... '
  read -r _ || true
}

ejecutar() {
  if [[ "$ENSAYO" -eq 1 ]]; then
    printf '   (--ensayo: no se ejecuta: %s)\n' "$*"
    return 0
  fi
  "$@"
}

RAIZ_ABS="$RAIZ"

# Paso 1
paso 1 "El problema" \
  "Presentador: adultos mayores que viven solos y su familia lejos. Frase: «Alexa obedece; Lucía acompaña»." \
  "Contexto de la demostración." \
  ""

# Paso 2
paso 2 "Lucía y la luz" \
  "Usuario: «Lucía» → tono → «prende la luz» y después «apaga la luz». Mostrar el panel en vivo." \
  "El foco cambia y el panel se actualiza sin recargar." \
  "F9 o botón del panel; botón físico del ESP32; video de respaldo."

# Paso 3
paso 3 "Buenos días" \
  "Lucía: saludo, hora, temperatura y efeméride." \
  "Lucía dice la hora, la temperatura y una efeméride." \
  "Repetir con F9; video de respaldo."

# Paso 4
paso 4 "Saludo diario" \
  "Presentador: lanzar el saludo una sola vez con herramientas/saludo.sh y responder «no me siento bien»." \
  "Lucía pregunta «¿Cómo amaneció?», alerta en Telegram y panel; se cierra con «Ya lo atendí»." \
  "F9 o botón del panel; hotspot de respaldo; video de respaldo."
ejecutar "$RAIZ_ABS/herramientas/saludo.sh" || true

# Paso 5
paso 5 "Agua" \
  "Usuario: «¿cuánta agua hay?» con la cubeta a la mitad. Bajar el nivel para la alerta (~1 L). El agua real va primero." \
  "Lucía dice el porcentaje; aparece la alerta de agua baja." \
  "Si falla el agua real: Enter ejecuta agua.sh 30."
if [[ "$ENSAYO" -eq 0 ]]; then
  printf '   Pulse Enter para lanzar el respaldo agua.sh 30, o escriba "omitir": '
  read -r resp
  if [[ "$resp" != "omitir" ]]; then
    ejecutar "$RAIZ_ABS/herramientas/agua.sh" 30 || true
  fi
fi

# Paso 6
paso 6 "Gas" \
  "Presentador: simulación con herramientas/gas.sh 3000, sin abrir la torre." \
  "Buzzer local, aviso por voz y alerta por Telegram y panel." \
  "F9 o botón del panel; gas.sh 312 para normalizar; hotspot de respaldo; video de respaldo."
ejecutar "$RAIZ_ABS/herramientas/gas.sh" 3000 || true

# Paso 7
paso 7 "Familia ↔ sala" \
  "Un mensaje desde el panel, que Lucía dice en voz, y la respuesta por voz, que aparece en el panel." \
  "El mensaje se oye en la sala y la respuesta queda transcrita en el panel." \
  "F9 o botón del panel; video de respaldo."

# Paso 8
paso 8 "Ayuda" \
  "Usuario: «ayuda» → alerta a la familia. Mostrar también «cancela»." \
  "Llega la alerta a la familia; «cancela» la deshace como falsa alarma." \
  "F9 o botón del panel; video de respaldo."

# Paso 9
paso 9 "Desde fuera" \
  "Presentador: el panel en un teléfono con datos móviles." \
  "El panel abre en https://asistente.tail722206.ts.net y el WebSocket conecta." \
  "Hotspot de respaldo; video de respaldo."

# Paso 10
paso 10 "Cierre" \
  "Presentador: privacidad (la voz se procesa en casa, sin nube) y trabajo futuro (música por voz, «Lucía, …» de corrido)." \
  "Mensaje final de la demostración." \
  "Video de respaldo."

# Limpieza
printf '\n\033[1m== Limpieza final ==\033[0m\n'
if [[ "$ENSAYO" -eq 1 ]]; then
  printf '   (--ensayo: sin ejecutar)\n'
  printf '   - gas normal: herramientas/gas.sh 312\n'
  printf '   - agua real: esperar 60 s a que expire la simulación\n'
  printf '   - cerrar las alertas de la demo en el panel/Telegram\n'
  printf '   - confirmar que no queda ninguna alerta abierta (/alertas)\n'
else
  printf '   Normalizando gas (lectura normal)...\n'
  "$RAIZ_ABS/herramientas/gas.sh" 312 || true
  printf '   Esperando 65 s a que expiren las simulaciones de gas y agua...\n'
  sleep 65
  printf '   Cierre ahora las alertas de la demo en el panel o en Telegram.\n'
  printf '   Pulse Enter cuando haya cerrado todas... '
  read -r _ || true
  printf '   Confirme en el panel y con /alertas que no queda ninguna alerta abierta.\n'
fi

printf '\nFin del guion.\n'
