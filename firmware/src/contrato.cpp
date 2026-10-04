#include "contrato.h"

#include <ArduinoJson.h>

#include "config.h"
#include "hora.h"
#include "luz.h"
#include "persistencia.h"
#include "registro.h"
#include "sensores.h"

namespace {
constexpr char ETIQUETA[] = "contrato";

// Buffers de entrada. Los llena la tarea de ESP32MQTTClient y los vacia el loop,
// asi que van protegidos con un mutex: no es Publish contra ISR, es tarea contra
// tarea.
SemaphoreHandle_t semaforo = nullptr;
String pendiente_luz_set;
String pendiente_nodo_config;
String pendiente_prueba_gas;

void bloquear() {
  if (semaforo != nullptr) {
    xSemaphoreTake(semaforo, portMAX_DELAY);
  }
}

void desbloquear() {
  if (semaforo != nullptr) {
    xSemaphoreGive(semaforo);
  }
}

void guardar(String &destino, const String &valor) {
  bloquear();
  destino = valor;
  desbloquear();
}

// Orden de luz. Se valida aqui y se entrega ya limpia a luz.cpp.
void manejar_luz_set(const String &payload) {
  JsonDocument documento;
  if (deserializeJson(documento, payload)) {
    log_aviso(ETIQUETA, "luz/set con JSON ilegible, se descarta");
    return;
  }

  const char *estado = documento[JSON_CLAVE_ESTADO] | "";
  if (strcmp(estado, LUZ_ESTADO_ON) != 0 && strcmp(estado, LUZ_ESTADO_OFF) != 0) {
    log_aviso(ETIQUETA, "luz/set con estado '%s' desconocido, se descarta", estado);
    return;
  }
  const bool es_encendido = (strcmp(estado, LUZ_ESTADO_ON) == 0);

  // Sin ts no se puede comprobar la antiguedad: se descarta y se dice por que.
  // Una cola acumulada durante un corte no debe encender la luz minutos despues.
  if (documento[JSON_CLAVE_TS].isNull()) {
    log_aviso(ETIQUETA, "luz/set sin ts, se descarta (no se puede verificar la antiguedad)");
    return;
  }
  if (!documento[JSON_CLAVE_TS].is<int64_t>()) {
    log_aviso(ETIQUETA, "luz/set con ts no numerico, se descarta");
    return;
  }
  const int64_t ts = documento[JSON_CLAVE_TS].as<int64_t>();

  // Sin hora propia no se puede aplicar la regla de antiguedad. El plan lo dice
  // explicitamente: no se ejecuta luz/set y se registra; el boton y Alexa siguen.
  if (!hora_es_valida()) {
    log_aviso(ETIQUETA, "luz/set sin hora valida, se descarta (el boton y Alexa siguen)");
    return;
  }

  const int64_t antiguedad = hora_epoch() - ts;
  if (antiguedad > ORDEN_MAX_ANTIGUEDAD_S) {
    log_aviso(ETIQUETA, "luz/set con ts de hace %lld s (max %d), se descarta",
              static_cast<long long>(antiguedad), ORDEN_MAX_ANTIGUEDAD_S);
    return;
  }
  if (antiguedad < -30) {
    log_aviso(ETIQUETA, "luz/set con ts %lld s en el futuro, se descarta",
              static_cast<long long>(-antiguedad));
    return;
  }

  // El ESP32 hace eco del origen que traia la orden y no lo inventa. Si falta o no
  // esta en la lista, se avisa y se usa "panel", porque el unico que publica
  // luz/set es el servidor (regla de origen, 2.3).
  const char *origen = LUZ_ORIGEN_PANEL;
  const char *recibido = documento[JSON_CLAVE_ORIGEN] | "";
  if (recibido[0] == 0) {
    log_aviso(ETIQUETA, "luz/set sin origen, se asume %s", LUZ_ORIGEN_PANEL);
  } else if (strcmp(recibido, LUZ_ORIGEN_VOZ) != 0 && strcmp(recibido, LUZ_ORIGEN_ALEXA) != 0 &&
             strcmp(recibido, LUZ_ORIGEN_PANEL) != 0 && strcmp(recibido, LUZ_ORIGEN_BOTON) != 0) {
    log_aviso(ETIQUETA, "luz/set con origen '%s' desconocido, se asume %s", recibido,
              LUZ_ORIGEN_PANEL);
  } else {
    origen = recibido;
  }

  log_info(ETIQUETA, "luz/set aceptada ts=%lld origen=%s -> %s", static_cast<long long>(ts),
           origen, es_encendido ? LUZ_ESTADO_ON : LUZ_ESTADO_OFF);
  luz_aplicar_orden(es_encendido, origen);
}

// nodo/config: retained, lo manda el servidor. Solo se acepta el umbral de apertura.
void manejar_nodo_config(const String &payload) {
  JsonDocument documento;
  if (deserializeJson(documento, payload)) {
    log_aviso(ETIQUETA, "nodo/config con JSON ilegible, se descarta");
    return;
  }
  if (documento["gas_on_adc"].is<int>()) {
    persistencia_fijar_umbral_gas(documento["gas_on_adc"].as<int>());
  }
  if (documento["gas_off_adc"].is<int>()) {
    log_info(ETIQUETA, "nodo/config trae gas_off_adc=%d (la histeresis sale de config.h)",
             documento["gas_off_adc"].as<int>());
  }
}

// nodo/prueba_gas: la suscripcion a este topico solo existe con PRUEBAS=1. El valor
// forzado llega como texto plano, no JSON.
void manejar_prueba_gas(const String &payload) {
  if (payload.isEmpty()) {
    log_aviso(ETIQUETA, "prueba_gas vacio, se descarta");
    return;
  }
  const int valor = payload.toInt();
  if (valor <= 0) {
    log_aviso(ETIQUETA, "prueba_gas '%s' no es un numero valido, se descarta", payload.c_str());
    return;
  }
  log_info(ETIQUETA, "prueba_gas: gas forzado a %d (umbral %d)", valor,
           persistencia_umbral_gas());
  sensores_fijar_gas_prueba(valor);
}

}  // namespace

void contrato_iniciar() {
  semaforo = xSemaphoreCreateMutex();
  log_info(ETIQUETA, "antiguedad maxima de orden %d s; sin ts se descarta",
           ORDEN_MAX_ANTIGUEDAD_S);
}

void contrato_recibir_luz_set(const String &payload) {
  guardar(pendiente_luz_set, payload);
}

void contrato_recibir_nodo_config(const String &payload) {
  guardar(pendiente_nodo_config, payload);
}

void contrato_recibir_prueba_gas(const String &payload) {
  guardar(pendiente_prueba_gas, payload);
}

String contrato_tomar_luz_set() {
  bloquear();
  const String valor = pendiente_luz_set;
  pendiente_luz_set = "";
  desbloquear();
  return valor;
}

String contrato_tomar_nodo_config() {
  bloquear();
  const String valor = pendiente_nodo_config;
  pendiente_nodo_config = "";
  desbloquear();
  return valor;
}

String contrato_tomar_prueba_gas() {
  bloquear();
  const String valor = pendiente_prueba_gas;
  pendiente_prueba_gas = "";
  desbloquear();
  return valor;
}

void contrato_tarea() {
  // Una sola etapa: se toma el payload crudo que dejo la tarea de ESP32MQTTClient,
  // se valida aqui y se aplica. La validacion NO puede vivir en el callback porque
  // la regla de antiguedad necesita la hora, que se sincroniza aparte, y porque
  // desde la tarea de la libreria no se debe tocar la salida del rele.
  const String luz_set = contrato_tomar_luz_set();
  if (!luz_set.isEmpty()) {
    manejar_luz_set(luz_set);
  }

  const String config = contrato_tomar_nodo_config();
  if (!config.isEmpty()) {
    manejar_nodo_config(config);
  }

  const String prueba = contrato_tomar_prueba_gas();
  if (!prueba.isEmpty()) {
    manejar_prueba_gas(prueba);
  }
}
