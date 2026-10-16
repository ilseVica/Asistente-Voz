#!/usr/bin/env bash
# Guion de demostracion de 5 minutos: gas, ayuda y "Ya lo atendi".
#
# No es un test: deja las cosas a la vista, en el terminal, para que se vea lo que
# pasa y no solo el resultado. Los comandos sueltos estan en `GUION.md`, para poder
# copiar y pegar uno a uno.
#
#   cd servidor
#   bash ../herramientas/guion.sh
#
# Los pasos:
#   0. Prepara: comprueba que el broker, el orquestador y el simulador estan de pie.
#   1. Enciende la luz y muestra lo que el nodo responde.
#   2. Provoca una alerta de gas y espera a que llegue a Telegram.
#   3. Pide ayuda dos veces seguidas, y muestra que solo avisa una (P05).
#   4. Espera a que la familia pulse "Ya lo atendi".
#   5. Explica por que el episodio de gas hay que cerrarlo, y lo comprueba: un
#      disparo repetido NO da alerta nueva, y uno nuevo si, con id nuevo.
#   6. Cambia los umbrales con nodo/config y comprueba que el nodo los aplica.
#
# No lleva reloj: espera a que pase lo que tiene que pasar, y por eso los pasos
# tardan lo que tardan. Un guion con tiempos fijos falla cuando la maquina va justa,
# y ese dia es justo cuando hay gente mirando.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ/servidor"

# Carga el .env sin ejecutarlo: lee KEY=VALOR literal (los espacios no rompen
# nada) y quita las comillas dobles o simples que rodeen al valor.
if [ -f .env ]; then
  while IFS= read -r linea; do
    case "$linea" in ''|\#*) continue ;; esac
    clave=${linea%%=*}
    valor=${linea#*=}
    case "$valor" in
      \"*\") valor=${valor#\"}; valor=${valor%\"} ;;
      \'*\') valor=${valor#\'}; valor=${valor%\'} ;;
    esac
    export "$clave=$valor"
  done < .env
fi

PUENTE="${MQTT_PUENTE_PASSWORD:?falta MQTT_PUENTE_PASSWORD en servidor/.env}"
SIM="${GUION_SIMULADOR:-sim-nodo}"

paso() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
esperar() { sleep "$1"; }

mqtt_pub() {
  docker compose exec -T mosquitto mosquitto_pub -h 127.0.0.1 -p 1883 \
    -u puente -P "$PUENTE" -q 1 -t "$1" -m "$2"
}

mqtt_sub() {  # topico, cuantos mensajes
  docker compose exec -T mosquitto mosquitto_sub -h 127.0.0.1 -p 1883 \
    -u puente -P "$PUENTE" -q 0 -C "$2" -t "$1"
}

# El episodio solo se cierra cuando el gas lleva 10 segundos seguidos por debajo del
# umbral de cierre (1550 por defecto). Son 12: los 10 del firmware mas 2 de margen,
# para que el guion no dependa de si el reloj va atrasado.
constante_cierre() { echo "${GUION_ESPERA_CIERRE:-12}"; }

paso "0. Estado de partida"
docker compose ps --format "   {{.Name}}  {{.Status}}"
if ! docker exec "$SIM" true 2>/dev/null; then
  cat <<AVISO

   No esta el contenedor del simulador ($SIM). El guion puede seguir, pero sin el no
   hay alertas de gas que provocar: solo se vera lo que ya haya en el broker.

   Para levantarlo:
     cd $RAIZ
     docker run -d --name $SIM --network host \\
       -v "$RAIZ/nodo_simulado:/nodo" -e MQTT_HOST=127.0.0.1 \\
       -e MQTT_NODO_PASSWORD="\$MQTT_NODO_PASSWORD" -e PYTHONUNBUFFERED=1 \\
       nodo-simulador:local
AVISO
fi

paso "1. La luz: el puente manda la orden y el nodo responde con el estado"
mqtt_pub casa/sala/luz/set "{\"estado\":\"on\",\"origen\":\"panel\",\"ts\":$(date +%s)}"
mqtt_sub casa/sala/luz/estado 1
echo "   El nodo tambien lo deja retenido, asi que cualquier suscriptor nuevo lo ve."

paso "2. Alerta de gas: el episodio abre con 3 lecturas seguidas a 1 Hz"
echo "   Mira el Telegram. En unos 3 segundos llega el mensaje con el boton."
mqtt_pub casa/sala/nodo/prueba_gas '{"gas_adc":3000}'
esperar 6
docker logs "$SIM" 2>&1 | grep -E "EPISODIO|\[alerta\]" | tail -3 || true
docker compose logs --since=20s orquestador 2>&1 | grep -E "ALERTA GAS|enviada a la familia" || true

paso "3. Peticion de ayuda, dos veces con 10 s de diferencia (P05)"
echo "   Solo debe llegar UNA a Telegram: la segunda cae dentro de la ventana de 60 s."
mqtt_pub casa/sala/alerta/ayuda '{"id":"guion-b0001-0001","origen":"alexa"}'
esperar 10
mqtt_pub casa/sala/alerta/ayuda '{"id":"guion-b0001-0002","origen":"alexa"}'
esperar 4
docker compose logs --since=30s orquestador 2>&1 | grep -E "ALERTA AYUDA|antirrebote" || true

paso "4. Pulsa 'Ya lo atendi' en el Telegram del movil"
echo "   Mientras, con /alertas en el bot se ve el estado, y el servidor registra el"
echo "   aviso en satelite/decir."
read -r -p "   Cuando lo hayas pulsado, pulsa Intro para seguir... " _ || true
docker compose logs --since=120s orquestador 2>&1 | grep -E "resuelta|satelite/decir" || \
  echo "   (sin registro: nadie ha pulsado todavia. Si lo pulsas ahora, el aviso sale"
echo "    en el log del servicio y el aviso al satelite en satelite/decir.)"

paso "5. Por que hay que CERRAR el episodio antes de disparar otra vez"
echo "   5a. Se dispara gas otra vez sin cerrar. NO debe salir ninguna alerta nueva:"
mqtt_pub casa/sala/nodo/prueba_gas '{"gas_adc":3000}'
esperar 6
docker compose logs --since=20s orquestador 2>&1 | grep -E "ALERTA GAS" | tail -2 || \
  echo "      correcto: ninguna alerta nueva, porque es el mismo episodio"
echo "   5b. Ahora se cierra: por debajo de 1550 y 10 s seguidos."
mqtt_pub casa/sala/nodo/prueba_gas '{"gas_adc":312}'
esperar "$(constante_cierre)"
docker logs "$SIM" 2>&1 | grep "EPISODIO CERRADO" | tail -1 || \
  echo "      (no hay cierre todavia: falta tiempo por debajo del umbral)"
echo "   5c. Y con el episodio cerrado, el mismo disparo ya avisa, con id nuevo:"
mqtt_pub casa/sala/nodo/prueba_gas '{"gas_adc":3000}'
esperar 6
docker logs "$SIM" 2>&1 | grep -E "\[alerta\]" | tail -1 || true

paso "6. nodo/config: los umbrales del nodo, en caliente"
echo "   Se abren a 1500/1300, y con eso 1600 ya dispararia:"
mqtt_pub casa/sala/nodo/config '{"gas_on_adc":1500,"gas_off_adc":1300}'
esperar 3
docker logs "$SIM" 2>&1 | grep "nodo/config" | tail -2 || true
echo "   (para volver a los de fabrica, nodo/config con 1800/1550)"

paso "Fin del guion"
cat <<RESUMEN
   Que deberia haber pasado:
     - La luz se encendio y el nodo contesto con el estado.
     - Una sola alerta de gas, con el boton "Ya lo atendi".
     - Una sola peticion de ayuda, no dos (P05).
     - Al pulsar el boton, el mensaje cambio y el satelite recibio el aviso.
     - El gas repetido sin cerrar NO avisa; cerrado, vuelve a avisar con id nuevo.
     - nodo/config cambio los umbrales del nodo sin reiniciarlo.

   Lo que NO demuestra, porque necesita la placa:
     - La histeresis real del MQ-2, el pin de la luz y el buzzer.
RESUMEN
