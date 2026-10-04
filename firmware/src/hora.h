#pragma once

#include <Arduino.h>

// Hora del sistema.
//
// El plan pide SNTP al arrancar, primero con servidores publicos y, como respaldo,
// con el NTP que la VM sirve a la red local, para que la regla de antiguedad de
// luz/set funcione tambien en modo instalacion sin internet.
//
// Criterio de hora valida: el reloj esta mas alla de 2024-01-01. Con eso, un
// arranque sin NTP no se confunde con una hora valida y solo se descarta luz/set.

void hora_iniciar();
void hora_tarea();

bool hora_es_valida();
int64_t hora_epoch();

// ISO 8601 en UTC, para nodo/estado. Vacio si la hora no es valida.
String hora_iso_utc();

// "2026-10-03T17:42:11Z", para el Last Will, que el broker publica retenido.
String hora_iso_para_will();
