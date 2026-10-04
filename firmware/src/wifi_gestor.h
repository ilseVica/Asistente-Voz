#pragma once

#include <Arduino.h>

// Red. Conexion no bloqueante con espera creciente entre reintentos, para no
// bloquear el loop mientras no hay red.
//
// La direccion del broker viene de secrets.h. Si ahi hay un nombre .local, se
// resuelve con mDNS; si no resuelve, se usa el ultimo host que funciono, guardado
// en NVS. Ver 2.10.

void red_iniciar();
void red_tarea();

bool red_conectado();
String red_ip();

// mDNS, para resolver el nombre del broker y para que el nodo sea
// easier-asistente.local en la red.
void red_iniciar_mdns();
void red_anunciar_mdns();

// Resuelve MQTT_HOST. Devuelve la direccion usable o cadena vacia.
String red_resolver_broker(const String &host);
