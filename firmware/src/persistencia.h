#pragma once

#include <Arduino.h>

// Preferencias en NVS. Lo unico que sobrevive a un reinicio.
//
// NO se guarda aqui la cola de alertas pendientes, a proposito: si el ESP32 se
// reinicia durante una caida, lo pendiente se pierde y es aceptable, porque el
// reinicio genera un id nuevo y el servidor lo trataria como alerta nueva.

void persistencia_iniciar();

// Contador de arranques, incrementado una vez por arranque. Va en el bloque
// central del id de alerta.
uint16_t persistencia_arranque_actual();

// Siguiente valor del contador de alertas. SOLO se llama al crear una alerta
// nueva, nunca al reenviar una pendiente.
uint16_t persistencia_siguiente_contador_alerta();

// Umbral de gas de APERTURA, y su persistencia (lo puede cambiar nodo/config).
int persistencia_umbral_gas();
void persistencia_fijar_umbral_gas(int valor);

// Umbral de gas de CIERRE, y su persistencia. Es un numero propio, no
// «el de apertura menos la histeresis»: asi lo fija el contrato y asi lo puede
// cambiar nodo/config. Tiene que ser menor que el de apertura.
int persistencia_umbral_cierre_gas();
void persistencia_fijar_umbral_cierre_gas(int valor);

// Ultimo host del broker que funciono, como respaldo si secrets.h no resuelve.
// Se guarda en claro: no es un secreto.
String persistencia_host_previo();
void persistencia_fijar_host_previo(const String &host);
