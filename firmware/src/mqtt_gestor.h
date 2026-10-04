#pragma once

#include <Arduino.h>
#include <mqtt_client.h>

// Estado del cliente MQTT, para el loop principal y para el estado que se publica.
//
// El callbacks de ESP32MQTTClient corren en su propia tarea de FreeRTOS, no en el
// loop(). Por eso este modulo solo deja el resultado en banderas protegidas por un
// mutex y el loop() las lee: ningun publish() ni ninguna escritura de estado se
// hace desde dentro del callback.

// Arranca el cliente MQTT. Retorna false si NO se pudo arrancar, para que el loop
// vuelva a intentarlo: sin esto, un fallo puntual de resolucion (mDNS todavia sin
// propagar, broker todavia levantandose) dejaba al nodo sin MQTT para siempre.
bool mqtt_iniciar();
void mqtt_tarea();

// True solo cuando hay sesion MQTT viva.
bool mqtt_conectado();

// Publica. Retorna false si el mensaje no pudo encolarse en el cliente, lo cual
// NO significa que se haya entregado. Para las alertas, la entrega se confirma
// con el PUBACK: ver alertas.h.
bool mqtt_publicar(const String &topico, const String &payload, int qos, bool retener);



// Se llama desde onMqttConnect(), que ESP32MQTTClient declara en su cabecera y no
// define: hay que implementarla aqui. Ademas es el punto donde se engancha el
// manejador de MQTT_EVENT_PUBLISHED, porque ESP32MQTTClient no expone ese evento.
void mqtt_al_conectar(esp_mqtt_client_handle_t cliente);

