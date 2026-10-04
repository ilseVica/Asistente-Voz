#pragma once

#include <Arduino.h>

// Alexa con fauxmoESP.
//
// Verificacion importante (1.4 y 2.12): fauxmoESP descubre por SSDP, con
// multicast en UDP 1900, NO por mDNS. Su puerto TCP por defecto es 1901, asi que
// hay que llamar setPort(80) de forma explicita o el dispositivo se anuncia en un
// puerto que Alexa no busca.

// Boton de reinicio: pulsacion larga en un pin, para recuperar el nodo sin
// desenchufarlo. Es util durante la puesta en marcha, cuando todavia no hay
// servidor que mande ordenes.

void alexa_iniciar();
void alexa_tarea();

bool alexa_habilitado();
