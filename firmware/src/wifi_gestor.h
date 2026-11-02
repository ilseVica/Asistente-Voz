#pragma once

#include <Arduino.h>

// Red. Conexion no bloqueante con espera creciente entre reintentos, para no
// bloquear el loop mientras no hay red. Con WiFiMulti se elige la red de mejor
// señal entre las dos configuradas (red 1: casa, red 2: hotspot).
//
// La direccion del broker viene de secrets.h. Si ahi hay un nombre .local, se
// resuelve con mDNS; si no resuelve, se usa el ultimo host que funciono, guardado
// en NVS. Ver 2.10.

void red_iniciar();
void red_tarea();

bool red_conectado();
String red_ip();

// Indice de la red a la que esta conectado: 1 (casa) o 2 (hotspot). 0 si no
// hay conexion. Se usa para elegir el broker MQTT y el servidor NTP.
int red_indice_actual();
// Forzado de red por comando serie (solo RAM/RTC, no NVS).
int red_forzado();            // 0 = AUTO, 1/2/3 = forzada
void red_forzar(int red);     // 0 borra el forzado
void red_marcar_reinicio_por_red(); // marca RTC y solicita reinicio

// mDNS, para resolver el nombre del broker y para que el nodo sea
// easier-asistente.local en la red.
void red_iniciar_mdns();
void red_anunciar_mdns();

// Resuelve MQTT_HOST. Devuelve la direccion usable o cadena vacia.
String red_resolver_broker(const String &host);
