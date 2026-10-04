#pragma once

#include <Arduino.h>

// Alertas de gas y de ayuda, con la cola de pendientes en RAM.
//
// REGLA CENTRAL (ajuste A del usuario): una alerta sale de la cola unicamente
// cuando el broker confirma su recepcion, con el PUBACK del QoS 1. Nunca porque se
// haya llamado a publish(). Ver la seccion 3.5 de la propuesta.
//
// Por que no basta la bandeja de salida de esp-mqtt: publish() devuelve true en
// cuanto el mensaje entra en el outbox, no cuando el broker lo recibe, y ademas
// esp-mqtt descarta a los 30 s lo que no se confirmo. Por eso el outbox NO es el
// mecanismo de reintento: el reintento lo hace este modulo con su propio watchdog.

void alertas_iniciar();

// Publica una alerta de gas. El id lo genera aqui con el contador interno, que
// SOLO se incrementa al crear una alerta nueva, nunca al reenviarla.
void alerta_gas_registrar(int gas_adc);

// Publica una alerta de ayuda con el antirrebote de 60 s.
void alerta_ayuda_registrar(const char *origen);

// Intenta vaciar la cola. Se llama desde el loop, no desde los callbacks.
void alertas_tarea();

// Numero de alertas esperando confirmacion, para el estado y la prueba P45.
uint8_t alertas_pendientes();

// Lines de estado del modulo, para el log y para la prueba P45.
String alertas_estado_texto();

// Callback de esp-mqtt. ESP32MQTTClient no expone MQTT_EVENT_PUBLISHED, asi que
// este modulo registra su propio manejador en esp_mqtt_client_register_event().
//
// Se recibe el cliente como void* a proposito: alertas.h no quiere arrastrar la
// cabecera de esp-mqtt, y quien llama (mqtt_gestor.cpp) si la tiene.
void alertas_registrar_manejador(void *cliente_esp_mqtt);

// Ultimo msg_id publicado por este modulo, para poder correlacionar en el log.
int alertas_ultimo_msg_id();
