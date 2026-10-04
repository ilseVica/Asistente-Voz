#pragma once

#include <Arduino.h>

// Contrato: validacion de los mensajes que llegan por MQTT.
//
// Reglas que se aplican aqui, todas del plan:
//   - una orden de luz con ts mas viejo que ORDEN_MAX_ANTIGUEDAD_S se descarta y
//     se anota en el registro;
//   - una orden SIN ts tambien se descarta, porque no se puede verificar su
//     antiguedad y una cola acumulada durante un corte no debe encender la luz
//     minutos despues;
//   - una orden con ts ILEGIBLE tambien se descarta;
//   - si el ESP32 no tiene hora valida, NO ejecuta luz/set y lo registra; el boton
//     de luz y Alexa siguen funcionando.
//
// El ESP32 no se suscribe a casa/sala/alerta/ayuda: la deduplicacion de la alerta
// de ayuda la hace el servidor.

void contrato_iniciar();

// Entradas que entrega mqtt_gestor desde la tarea del cliente MQTT. No se procesan
// aqui: se guardan y se vacian en contrato_tarea(), que corre en el loop.
void contrato_recibir_luz_set(const String &payload);
void contrato_recibir_nodo_config(const String &payload);
void contrato_recibir_prueba_gas(const String &payload);

// Vacia lo pendiente y lo aplica. Corre en el loop, no en el callback.
void contrato_tarea();

String contrato_tomar_luz_set();
String contrato_tomar_nodo_config();
String contrato_tomar_prueba_gas();
