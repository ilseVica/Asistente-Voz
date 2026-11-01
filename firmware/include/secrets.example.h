#pragma once

// Plantilla de secrets.h.
//
// ESTE ARCHIVO NO ES EL QUE SE USA. Copielo a secrets.h y cambiale los valores:
//
//     cp firmware/include/secrets.example.h firmware/include/secrets.h
//
// secrets.h NO se versiona (esta en firmware/.gitignore). secrets.example.h si se
// versiona, pero con valores de ejemplo evidentemente falsos.
//
// Que va aqui y que no, segun la regla de 3.4:
//   secrets.h   credenciales de Wi-Fi, credenciales de MQTT y direccion del broker
//   config.h    todo lo demas, ver include/config.h
//
// Ojo con la direccion del broker: al estar aqui, cambiar de instalacion a
// presentacion SI exige recompilar. Es a proposito, para que ningun binario
// compilado pueda apuntar al broker equivocado sin darme cuenta.

// Red 1: red principal de la casa. Broker = VM (192.168.1.169).
#define WIFI1_SSID "cambia-esto"
#define WIFI1_PASS "cambia-esto"
#define MQTT1_HOST "192.168.1.169"

// Red 3: repetidor de la casa. Broker = VM (misma IP que MQTT1).
#define WIFI3_SSID "cambia-esto"
#define WIFI3_PASS "cambia-esto"
#define MQTT3_HOST "192.168.1.169"

// Red 2: hotspot del celular. Broker = Mosquitto de la laptop en el hotspot.
// MQTT2_HOST puede ser un nombre .local (mDNS) de la laptop; si no resuelve
// en ~5 s se usa MQTT2_HOST_RESPALDO (IP fija de la laptop en el hotspot).
#define WIFI2_SSID "cambia-esto"
#define WIFI2_PASS "cambia-esto"
#define MQTT2_HOST "cambia-esto"
#define MQTT2_HOST_RESPALDO "cambia-esto"

// Credenciales del usuario "nodo" del broker. La contrasena se copia de
// servidor/.env, que ya esta ignorado por git. No volver a escribirla aqui.
// Son las mismas para las dos redes (mismo usuario "nodo" del Mosquitto).
#define MQTT_USUARIO "nodo"
#define MQTT_PASSWORD "cambia-esto"
