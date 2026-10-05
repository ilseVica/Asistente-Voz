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

// Epoch en segundos, o -1 si todavia no hay hora valida. Es lo que va en el campo
// "ts" de nodo/estado, para que sea el MISMO tipo que usan luz/set y
// satelite/decir (epoch entero). Devolver -1 y no 0 es a proposito: 0 en epoch
// significa 1970, que es una hora valida, y no "sin hora".
int64_t hora_epoch_o_desconocida();

// ISO 8601 en UTC. Solo para el registro local del monitor serie. Vacio si la hora
// no es valida.
String hora_iso_utc();

// "2026-10-03T17:42:11Z", para el Last Will, que el broker publica retenido.
String hora_iso_para_will();
