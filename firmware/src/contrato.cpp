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

// nodo/config: retained, lo manda el servidor. Se aceptan los dos umbrales del gas,
// el de apertura y el de cierre.
void manejar_nodo_config(const String &payload) {
  JsonDocument documento;
  if (deserializeJson(documento, payload)) {
    log_aviso(ETIQUETA, "nodo/config con JSON ilegible, se descarta");
    return;
  }
  // El de cierre se fija ANTES que el de apertura. Al reves, un nodo/config que
  // subiera la apertura por encima del cierre que ya tiene seria rechazado por
  // `persistencia_fijar_umbral_cierre_gas`, y el servidor creeria que el gas ya no
  // puede bajar de 1550 cuando el problema es que se le pidio otra cosa.
  if (documento["gas_off_adc"].is<int>()) {
    persistencia_fijar_umbral_cierre_gas(documento["gas_off_adc"].as<int>());
  }
  if (documento["gas_on_adc"].is<int>()) {
    persistencia_fijar_umbral_gas(documento["gas_on_adc"].as<int>());
  }
}

// El payload es un entero y nada mas: opcional menos y solo digitos. Sirve para
// distinguir el texto plano del JSON y de un mensaje corrupto.
bool es_entero(const String &texto) {
  if (texto.length() == 0) {
    return false;
  }
  int inicio = texto[0] == '-' ? 1 : 0;
  if (inicio >= texto.length()) {
    return false;
  }
  for (int i = inicio; i < texto.length(); ++i) {
    if (!isDigit(texto[i])) {
      return false;
    }
  }
  return true;
}

// nodo/prueba_gas: la suscripcion a este topico solo existe con PRUEBAS=1.
//
// El payload es el JSON del contrato, {"gas_adc":3000}, el mismo que manda el
// nodo_simulado y el mismo formato de las alertas. Antes se leia como texto plano
// con `toInt()`, y el firmware era el unico de los tres que no entendia el
// contrato: mandarle el JSON del contrato no hacia nada y habia que mandar un
// numero a pelo.
//
// El texto plano se sigue aceptando, como numero entero y nada mas, para no
// romper a quien lo mande asi. Se avisa en el log, porque es la forma antigua y
// la que hay que arreglar, no la que hay que aprender.
void manejar_prueba_gas(const String &payload) {
  if (payload.isEmpty()) {
    log_aviso(ETIQUETA, "prueba_gas vacio, se descarta");
    return;
  }

  int valor = 0;
  bool desde_json = false;

  JsonDocument documento;
  if (!deserializeJson(documento, payload) && documento[JSON_CLAVE_GAS].is<int>()) {
    valor = documento[JSON_CLAVE_GAS].as<int>();
    desde_json = true;
  } else if (es_entero(payload)) {
    // `String::toInt()` del core 2.0.17 no admite el puntero de "he conversionado
    // algo", asi que el formato se comprueba antes: un payload como "12ab" tambien
    // devuelve 12, y forzar el gas a 12 porque el mensaje traia basura seria peor que
    // rechazar el mensaje.
    valor = payload.toInt();
    log_aviso(ETIQUETA,
              "prueba_gas en texto plano; el contrato es {\"%s\":N}",
              JSON_CLAVE_GAS);
  }

  if (valor <= 0 || valor > GAS_ADC_MAXIMO) {
    log_aviso(ETIQUETA, "prueba_gas '%s' fuera de 1..%d, se descarta",
              payload.c_str(), GAS_ADC_MAXIMO);
    return;
  }

  log_info(ETIQUETA, "prueba_gas (%s): gas forzado a %d (abre %d, cierra %d)",
           desde_json ? "json" : "texto plano", valor, persistencia_umbral_gas(),
           persistencia_umbral_cierre_gas());
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
