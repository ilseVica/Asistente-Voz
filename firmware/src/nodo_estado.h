#pragma once

#include <Arduino.h>

// Latido de nodo/estado, que el servidor usa como retenido para saber si el nodo
// esta vivo. Es el inverso del Last Will: este publica conectado=true, y el Will
// publica conectado=false si el ESP32 se cae sin cerrar la sesion (prueba P27).

void nodo_estado_iniciar();
void nodo_estado_tarea();

String nodo_estado_json();

// Campo por campo, para el log y para la prueba.
String nodo_estado_resumen();
