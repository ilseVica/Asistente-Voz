#pragma once

#include <Arduino.h>

// Hora del sistema.
//
// El plan pide SNTP al arrancar, primero con el NTP de la VM (192.168.1.169, la
// red local de casa) y como respaldo con servidores publicos, para que la regla de
// antiguedad de luz/set funcione tambien en modo instalacion sin internet.
//
// Criterio de hora valida: el reloj esta mas alla de 2024-01-01. Ese criterio se
// aplica LEYENDO el reloj en cada llamada a hora_es_valida(), no con una bandera
// guardada: sin sincronizar, time(nullptr) cuenta segundos desde el arranque y el
// umbral lo descarta; en cuanto SNTP ajusta, la hora deja de ser invalida en ese
// mismo instante.

void hora_iniciar();
void hora_tarea();

// true si el reloj ya supera el umbral de 2024-01-01. No depende de ningun estado
// guardado: lee time(nullptr) en cada llamada.
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
