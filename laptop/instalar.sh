#!/usr/bin/env bash
# Instalador del broker MQTT de la laptop (red del hotspot).
# Ejecutar con sudo. No imprime secretos.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Ejecuta con sudo: sudo ./laptop/instalar.sh"
    exit 1
fi

HOTSPOT_CONEXION="iPhone de Ilse"

# Comprobacion previa: la conexion existe y es la activa en la interfaz Wi-Fi.
if ! nmcli connection show "$HOTSPOT_CONEXION" >/dev/null 2>&1; then
    echo "La conexion del hotspot no existe en NetworkManager."
    exit 1
fi

IFACE="$(nmcli -t -f DEVICE,TYPE,STATE device status \
        | awk -F: '$2=="wifi" && $3=="connected" {print $1}' | head -n1)"
if [[ -z "$IFACE" ]]; then
    echo "No hay interfaz Wi-Fi conectada."
    exit 1
fi

ACTIVA="$(nmcli -t -f NAME,TYPE,DEVICE connection show --active \
        | awk -F: -v iface="$IFACE" '$2=="802-11-wireless" && $3==iface {print $1}' | head -n1)"
if [[ "$ACTIVA" != "$HOTSPOT_CONEXION" ]]; then
    echo "La conexion activa en $IFACE no es la del hotspot."
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF_SRC="$REPO_DIR/laptop/mosquitto.conf"
ACL_SRC="$REPO_DIR/laptop/acl"
BRIDGE_EXAMPLE="$REPO_DIR/laptop/bridge.conf.example"
DISPATCHER_SRC="$REPO_DIR/laptop/90-mosquitto-hotspot"

# 1. Paquetes
pacman -S --needed --noconfirm mosquitto

# 2. Archivos
install -d -m 755 /etc/mosquitto
install -d -m 755 /etc/mosquitto/conf.d
install -d -m 750 /var/lib/mosquitto
chown mosquitto:mosquitto /var/lib/mosquitto

sed "s/__WIFI_IFACE__/$IFACE/g" "$CONF_SRC" > /etc/mosquitto/mosquitto.conf
install -m 644 "$ACL_SRC" /etc/mosquitto/acl

if [[ ! -f /etc/mosquitto/conf.d/bridge.conf ]]; then
    cat "$BRIDGE_EXAMPLE" > /etc/mosquitto/conf.d/bridge.conf
    echo "AVISO: bridge.conf creado desde el ejemplo."
    echo "       Pon la contrasena de puente en remote_password antes de arrancar."
fi

# 3. Validaciones previas
if [[ ! -f /etc/mosquitto/passwd ]] || ! grep -q '^nodo:' /etc/mosquitto/passwd; then
    echo "Falta /etc/mosquitto/passwd con la linea nodo:. No se arranca."
    exit 1
fi

if grep -q '<PONER_AQUI>' /etc/mosquitto/conf.d/bridge.conf 2>/dev/null; then
    echo "bridge.conf aun tiene <PONER_AQUI>. Pon la contrasena y reintenta."
    exit 1
fi

# 4. Permisos (Mosquitto 2.1: archivos del broker propiedad de mosquitto)
chown mosquitto:mosquitto /etc/mosquitto/mosquitto.conf /etc/mosquitto/acl
chmod 644 /etc/mosquitto/mosquitto.conf
chmod 600 /etc/mosquitto/acl
chown mosquitto:mosquitto /etc/mosquitto/passwd
chmod 600 /etc/mosquitto/passwd
chown root:mosquitto /etc/mosquitto/conf.d/bridge.conf
chmod 640 /etc/mosquitto/conf.d/bridge.conf

# 5. Dispatcher de NetworkManager
install -m 755 "$DISPATCHER_SRC" /etc/NetworkManager/dispatcher.d/90-mosquitto-hotspot
chown root:root /etc/NetworkManager/dispatcher.d/90-mosquitto-hotspot
if ! systemctl is-enabled NetworkManager-dispatcher.service >/dev/null 2>&1; then
    systemctl enable NetworkManager-dispatcher.service
fi

# 6. IP fija en la conexion del hotspot
nmcli connection modify "$HOTSPOT_CONEXION" \
    ipv4.method manual \
    ipv4.addresses 172.20.10.10/28 \
    ipv4.gateway 172.20.10.1 \
    ipv4.dns 172.20.10.1
nmcli connection up "$HOTSPOT_CONEXION" >/dev/null

# 7. ufw (solo en la interfaz del hotspot)
if command -v ufw >/dev/null 2>&1; then
    ufw allow in on "$IFACE" from 172.20.10.0/28 to any port 1883 proto tcp
    ufw allow in on "$IFACE" from 172.20.10.0/28 to any port 5353 proto udp
fi

# 8. Arranque
systemctl enable --now mosquitto

echo "Instalacion terminada. Interfaz: $IFACE."
systemctl status mosquitto --no-pager -n 20
ss -ltn 'sport = :1883'
