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

#define WIFI_SSID "cambia-esto"
#define WIFI_PASSWORD "cambia-esto"

// Direccion del broker. Acepta IP directa o nombre .local resuelto por mDNS.
#define MQTT_HOST "192.168.1.169"

// Credenciales del usuario "nodo" del broker. La contrasena se copia de
// servidor/.env, que ya esta ignorado por git. No volver a escribirla aqui.
#define MQTT_USUARIO "nodo"
#define MQTT_PASSWORD "cambia-esto"
