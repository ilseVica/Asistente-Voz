# Broker MQTT de la laptop (red del hotspot)

Version probada: Mosquitto 2.1.2 (Arch).

## Instalacion
1. Copia a la laptop la carpeta `laptop/` del repositorio.
2. Paso 2 (linea de nodo):
   sudo -v
   ssh ilse@villaca "grep '^nodo:' ~/Servidores/asistente-voz-secretos/mosquitto/passwd" | sudo tee /etc/mosquitto/passwd >/dev/null
   sudo grep -c '^nodo:' /etc/mosquitto/passwd   (debe dar 1)
3. Paso 3 (bridge):
   sudo install -d -m 755 /etc/mosquitto/conf.d
   sudo install -m 640 -o root -g mosquitto laptop/bridge.conf.example /etc/mosquitto/conf.d/bridge.conf
   PASS="$(ssh ilse@villaca 'sed -n "s/^MQTT_PUENTE_PASSWORD=//p" ~/Servidores/Asistente-Voz/servidor/.env')"
   PASS="${PASS%\"}"; PASS="${PASS#\"}"
   if printf '%s' "$PASS" | grep -q '[|&\\]'; then echo "La contraseña tiene | & o \\"; else sudo sed -i "s|<PONER_AQUI>|$PASS|" /etc/mosquitto/conf.d/bridge.conf; fi
   unset PASS
   sudo grep -c '<PONER_AQUI>' /etc/mosquitto/conf.d/bridge.conf   (debe dar 0)
   sudo awk '/^remote_password/{print (length($0) > 16 ? "contraseña puesta" : "VACÍA")}' /etc/mosquitto/conf.d/bridge.conf
4. Ejecuta `sudo ./laptop/instalar.sh`.

## Prueba
   P="$(ssh ilse@villaca "sed -n 's/^#define MQTT_PASSWORD[[:space:]]*\"\(.*\)\".*/\1/p' ~/Servidores/Asistente-Voz/firmware/include/secrets.h")"
   mosquitto_sub -h 127.0.0.1 -u nodo -P "$P" -t casa/sala/luz/set -v
   (mover la luz desde el panel o Alexa; Ctrl+C) y unset P

## Antes de la demo
- Conectar la laptop al hotspot ANTES que el Echo y el ESP32.
- Comprobar con `ss -ltn 'sport = :1883'` que escucha en 172.20.10.10.

## Desinstalar / revertir IP fija
- `sudo systemctl disable --now mosquitto`
- `sudo nmcli connection modify "iPhone de Ilse" ipv4.method auto`
- `sudo nmcli connection up "iPhone de Ilse"`
- Eliminar las reglas de ufw añadidas.

## Riesgo
Si cae el bridge, el panel sigue mostrando el nodo conectado (mensaje retenido).
