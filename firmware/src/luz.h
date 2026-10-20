#pragma once

#include <Arduino.h>

// Estado de la luz, del LED y de los botones.
//
// Reglas del contrato que se aplican aqui:
//   - Hay UN SOLO estado interno. MQTT, Alexa y el boton escriben en el; el rele y
//     el LED leen de el, asi que no pueden quedar desincronizados (2.5).
//   - luz/estado se publica retenido al cambiar, no en un latido: el ESP32 publica,
//     no consulta (regla "un solo estado con dos caminos", 2.3).
//   - El origen se hace eco del de la orden. No es lo mismo que la regla de
//     antiguedad: una orden de luz SIN ts se descarta (va por MQTT), pero las
//     ordenes de Alexa y del boton son locales y NO llevan ts, asi que no pasan por
//     esa comprobacion. La unica excepcion es «arranque», que pone el propio nodo
//     cuando la luz nace apagada al arrancar en frio: no es una orden de nadie.
//   - Sin broker no hay orden de luz por MQTT: la luz conserva su ultimo estado.
//     El boton y Alexa siguen funcionando.

void luz_iniciar();
void luz_tarea();

// Aplica una orden ya validada y publica luz/estado con el origen que corresponde.
// origen_actual es uno de LUZ_ORIGEN_VOZ, LUZ_ORIGEN_ALEXA, LUZ_ORIGEN_PANEL o
// LUZ_ORIGEN_BOTON. «arranque» no se acepta aqui: es el que pone luz_iniciar(), y
// contrato.cpp lo rechaza si llega metido dentro de un luz/set.
void luz_aplicar_orden(bool es_encendido, const char *origen_actual);

bool luz_encendida();
bool luz_tiene_estado_conocido();
uint32_t luz_ultimo_cambio_ms();

// JSON completo de luz/estado para (re)publico retenido: lleva el origen que
// corresponda, o «arranque» si nadie ha cambiado la luz desde el arranque en frio.
// Se usa tambien en mqtt_al_conectar() para la regla "Reconexion".
String luz_estado_json();

// Boton de luz y reinicio. GPIO 0 (BOOT) hace de boton de luz en esta entrega y
// ademas reinicia con pulsacion larga (P31). GPIO 33 queda cableado con antirrebote
// pero desactivado con BOTON_EXTERNO_HABILITADO = false, para no responder a un pin
// flotante.
void botones_tarea();

// Polaridad del LED: hay que respetar LED_ACTIVO_EN_NIVEL_BAJO de config.h.
void led_escribir(bool encendido);
