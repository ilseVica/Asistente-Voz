#include "alertas.h"

#include <string>

// mqtt_client.h es la cabecera de esp-mqtt que trae esp_mqtt_client_handle_t,
// MQTT_EVENT_PUBLISHED, event->msg_id y esp_mqtt_client_register_event().
#include <mqtt_client.h>


#include "config.h"
#include "mqtt_gestor.h"
#include "persistencia.h"
#include "registro.h"

namespace {

constexpr char ETIQUETA[] = "alertas";
constexpr uint8_t CAPACIDAD_COLA = 8;
constexpr uint32_t MS_VIGILANTE_SIN_CONFIRMAR = 8000UL;  // menor que los 30 s del outbox
constexpr uint8_t INTENTOS_MAXIMOS = 3;

// Una entrada de la cola. Guarda el payload ya construido, porque al reenviar se
// tiene que publicar EXACTAMENTE lo mismo, con el mismo id. Reconstruirlo podria
// cambiar el contador y romper la deduplicacion del servidor.
struct Entrada {
  bool ocupada = false;
  String topico;
  String payload;
  String id;
  uint8_t intentos = 0;
  uint32_t ultimo_intento_ms = 0;
  // msg_id NO se puede rellenar: ESP32MQTTClient::publish() devuelve bool y no el
  // msg_id que esp-mqtt asigno. Por eso la correlacion se hace de otra forma, en
  // manejador_publicado().
};

// Solo hay una alerta en vuelo a la vez. Es lo que permite que cada
// MQTT_EVENT_PUBLISHED corresponda sin ambiguedad a la alerta esperada: el ESP32
// publica con QoS 1 unicamente en los topicos de alerta (luz/estado, sensores y
// nodo/estado son QoS 0), y nunca hay dos publicaciones QoS 1 simultaneas.
Entrada cola[CAPACIDAD_COLA];
int16_t indice_en_vuelo = -1;
int ultimo_msg_id = -1;
// esp-mqtt llama al manejador en SU tarea y el loop llama desde la tarea de
// Arduino, asi que el cruce se protege con un mutex. No sirve un candado de
// interrupcion: esto no es Publish contra ISR, es tarea contra tarea.
SemaphoreHandle_t semaforo_cola = nullptr;

void construir_json_gas(String &destino, const String &id, int gas_adc) {
  destino = "{";
  destino += "\"" JSON_CLAVE_ID "\":\"" + id + "\"";
  destino += ",\"" JSON_CLAVE_GAS "\":" + String(gas_adc);
  destino += "}";
}

void construir_json_ayuda(String &destino, const String &id, const char *origen) {
  destino = "{";
  destino += "\"" JSON_CLAVE_ID "\":\"" + id + "\"";
  destino += ",\"" JSON_CLAVE_ORIGEN "\":\"" + String(origen) + "\"";
  destino += "}";
}

void bloquear() {
  if (semaforo_cola != nullptr) {
    xSemaphoreTake(semaforo_cola, portMAX_DELAY);
  }
}

void desbloquear() {
  if (semaforo_cola != nullptr) {
    xSemaphoreGive(semaforo_cola);
  }
}

uint16_t siguiente_contador() {
  uint16_t contador = persistencia_siguiente_contador_alerta();
  return contador;
}

void encolar(const String &topico, const String &payload, const String &id) {
  bloquear();
  int16_t libre = -1;
  for (uint8_t i = 0; i < CAPACIDAD_COLA; ++i) {
    if (!cola[i].ocupada) {
      libre = i;
      break;
    }
  }
  if (libre < 0) {
    // Se descarta la mas antigua, que es la de indice 0 porque la cola es FIFO.
    // OJO con el caso en que la mas antigua es la que esta EN VUELO: si se liberara
    // su hueco sin limpiar indice_en_vuelo, el siguiente PUBACK confirmaria la
    // alerta que acaba de ocupar ese hueco, y se perderia la que si iba en vuelo.
    log_aviso(ETIQUETA, "cola llena (%d), se descarta la mas antigua id=%s", CAPACIDAD_COLA,
              cola[0].id.c_str());
    if (indice_en_vuelo == 0) {
      indice_en_vuelo = -1;
      log_aviso(ETIQUETA, "la que estaba en vuelo se ha descartado; su PUBACK llegara sin "
                          "destino y se ignorara");
    }
    cola[0].ocupada = false;
    libre = 0;
  }
  cola[libre].ocupada = true;
  cola[libre].topico = topico;
  cola[libre].payload = payload;
  cola[libre].id = id;
  cola[libre].intentos = 0;
  cola[libre].ultimo_intento_ms = 0;
  desbloquear();
  log_info(ETIQUETA, "encolada id=%s topico=%s pendientes=%d", id.c_str(), topico.c_str(),
           alertas_pendientes());
}

// El manejador que registra el modulo contra esp-mqtt. Solo mira confirmaciones;
// el resto de eventos los sigue atendiendo ESP32MQTTClient con su propio switch.
//
// La firma es la de ESP-IDF 4.x, que es la que trae el core 2.0.17: cuatro
// argumentos, void de retorno, y los datos llegan en event_data. En ESP-IDF 5.x
// cambia a esp_err_t (*)(esp_mqtt_event_handle_t), igual que hace ESP32MQTTClient
// con sus dos firmas condicionadas por ESP_IDF_VERSION.
void manejador_publicado(void *argumentos, esp_event_base_t base, int32_t evento_id,
                          void *datos) {
  (void)argumentos;
  (void)base;
  if (evento_id != MQTT_EVENT_PUBLISHED) {
    return;
  }

  auto evento = static_cast<esp_mqtt_event_handle_t>(datos);
  if (evento == nullptr) {
    return;
  }

  const int msg_id = evento->msg_id;
  bloquear();
  const int16_t en_vuelo = indice_en_vuelo;
  if (en_vuelo < 0) {
    desbloquear();
    // No deberia pasar: solo se publica con QoS 1 desde este modulo, y cada
    // publicacion abre la ventana de vuelo. Si aparece, es un PUBACK tardio de un
    // reintento que ya se habia dado por perdido. Se ignora y se avisa.
    log_aviso(ETIQUETA, "PUBLICADO msg_id=%d sin nada en vuelo, se ignora", msg_id);
    return;
  }

  // Correlacion sin comparar msg_ids, y por que es correcta: ESP32MQTTClient solo
  // publica con QoS 1 en los topicos de alerta, y este modulo nunca tiene mas de
  // una alerta en vuelo. Por lo tanto TODO MQTT_EVENT_PUBLISHED que llega es el
  // PUBACK de la alerta que esta esperando. Si el ESP32 llegara a publicar con
  // QoS 1 en otro topico, esta regla dejaria de valer y habria que pasar a
  // comparar msg_ids, lo que exigiria leer el msg_id de esp_mqtt_client_publish().
  String id = cola[en_vuelo].id;
  cola[en_vuelo].ocupada = false;
  cola[en_vuelo].intentos = 0;
  indice_en_vuelo = -1;
  desbloquear();

  ultimo_msg_id = msg_id;
  log_info(ETIQUETA, "CONFIRMADA msg_id=%d id=%s (la cola baja a %d)", msg_id, id.c_str(),
           alertas_pendientes());
}

}  // namespace

void alertas_iniciar() {
  semaforo_cola = xSemaphoreCreateMutex();
  if (semaforo_cola == nullptr) {
    log_error(ETIQUETA, "no se pudo crear el mutex de la cola");
  }
}

void alertas_registrar_manejador(void *cliente_esp_mqtt) {
  if (cliente_esp_mqtt == nullptr) {
    log_error(ETIQUETA, "no se puede registrar el manejador: cliente nulo");
    return;
  }
  auto cliente = static_cast<esp_mqtt_client_handle_t>(cliente_esp_mqtt);
  esp_err_t resultado = esp_mqtt_client_register_event(cliente, MQTT_EVENT_PUBLISHED,
                                                       manejador_publicado, nullptr);
  if (resultado != ESP_OK) {
    log_error(ETIQUETA, "no se pudo registrar MQTT_EVENT_PUBLISHED (%d)", resultado);
    return;
  }
  log_info(ETIQUETA, "manejador de MQTT_EVENT_PUBLISHED registrado");
}

void alerta_gas_registrar(int gas_adc) {
  String id = id_alerta_construir(persistencia_arranque_actual(), siguiente_contador());
  String payload;
  construir_json_gas(payload, id, gas_adc);
  encolar(TOPIC_ALERTA_GAS, payload, id);
}

void alerta_ayuda_registrar(const char *origen) {
  String id = id_alerta_construir(persistencia_arranque_actual(), siguiente_contador());
  String payload;
  construir_json_ayuda(payload, id, origen);
  encolar(TOPIC_ALERTA_AYUDA, payload, id);
}

void alertas_tarea() {
  if (!mqtt_conectado()) {
    return;
  }

  bloquear();

  if (indice_en_vuelo >= 0) {
    const uint32_t ahora = millis();
    Entrada &en_vuelo = cola[indice_en_vuelo];
    const uint32_t transcurrido = ahora - en_vuelo.ultimo_intento_ms;
    if (transcurrido < MS_VIGILANTE_SIN_CONFIRMAR) {
      desbloquear();
      return;
    }
    if (en_vuelo.intentos >= INTENTOS_MAXIMOS) {
      log_error(ETIQUETA, "id=%s sin confirmar tras %d intentos, se descarta", en_vuelo.id.c_str(),
                en_vuelo.intentos);
      en_vuelo.ocupada = false;
      indice_en_vuelo = -1;
      desbloquear();
      return;
    }
    // El watchdog actua a los 8 s, mucho antes de que el outbox de esp-mqtt
    // descarte el mensaje a los 30 s, y se republica con el MISMO id.
    log_aviso(ETIQUETA, "id=%s sin PUBACK en %lu ms, reintento %d", en_vuelo.id.c_str(),
              static_cast<unsigned long>(transcurrido), en_vuelo.intentos + 1);
    en_vuelo.intentos += 1;
    en_vuelo.ultimo_intento_ms = ahora;
    bool enviado = mqtt_publicar(en_vuelo.topico, en_vuelo.payload, QOS_ALERTA_GAS, false);
    if (!enviado) {
      en_vuelo.intentos = 0;  // fallo local, se reintentara en el siguiente ciclo
    }
    desbloquear();
    return;
  }

  // Hay hueco en vuelo: se manda la primera alerta de la cola.
  for (uint8_t i = 0; i < CAPACIDAD_COLA; ++i) {
    if (!cola[i].ocupada) {
      continue;
    }
    cola[i].intentos = 1;
    cola[i].ultimo_intento_ms = millis();
    indice_en_vuelo = i;
    const bool enviado = mqtt_publicar(cola[i].topico, cola[i].payload, QOS_ALERTA_GAS, false);
    if (!enviado) {
      cola[i].intentos = 0;
      cola[i].ultimo_intento_ms = 0;
      indice_en_vuelo = -1;
      desbloquear();
      return;
    }
    log_info(ETIQUETA, "ENVIANDO id=%s topico=%s intento=%d", cola[i].id.c_str(),
             cola[i].topico.c_str(), cola[i].intentos);
    desbloquear();
    return;
  }

  desbloquear();
}

uint8_t alertas_pendientes() {
  bloquear();
  uint8_t cuenta = 0;
  for (uint8_t i = 0; i < CAPACIDAD_COLA; ++i) {
    if (cola[i].ocupada) {
      ++cuenta;
    }
  }
  desbloquear();
  return cuenta;
}

String alertas_estado_texto() {
  char texto[96];
  snprintf(texto, sizeof(texto), "pendientes=%d en_vuelo=%d msg_id=%d",
           alertas_pendientes(), indice_en_vuelo, ultimo_msg_id);
  return String(texto);
}

int alertas_ultimo_msg_id() {
  return ultimo_msg_id;
}
