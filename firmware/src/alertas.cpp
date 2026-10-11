#include "alertas.h"

#include <string>

// mqtt_client.h es la cabecera de esp-mqtt que trae esp_mqtt_event_handle_t,
// MQTT_EVENT_PUBLISHED y event->msg_id.
#include <mqtt_client.h>


#include "config.h"
#include "mqtt_gestor.h"
#include "persistencia.h"
#include "registro.h"

namespace {

constexpr char ETIQUETA[] = "alertas";
constexpr uint8_t CAPACIDAD_COLA = 8;
constexpr uint32_t MS_VIGILANTE_SIN_CONFIRMAR = 8000UL;  // menor que los 30 s del outbox

// Una entrada de la cola. Guarda el payload ya construido, porque al reenviar se
// tiene que publicar EXACTAMENTE lo mismo, con el mismo id. Reconstruirlo podria
// cambiar el contador y romper la deduplicacion del servidor.
struct Entrada {
  bool ocupada = false;
  String topico;
  String payload;
  String id;
  // Regla del contrato: la alerta se reenvia MIENTRAS haya sesion MQTT y hasta que
  // el broker confirme, NUNCA se descarta por numero de intentos. `intentos` es
  // solo para el log; por eso ya no hay tope (antes, a los 3, la alerta se tiraba).
  uint16_t intentos = 0;
  uint32_t ultimo_intento_ms = 0;
  // msg_id devuelto por esp_mqtt_client_publish() en el ultimo envio y en el
  // anterior. El PUBACK (MQTT_EVENT_PUBLISHED) se acepta solo si su msg_id coincide
  // con uno de estos dos: asi un PUBACK rezagado de otra alerta no libera esta.
  int msg_id_esperado = -1;
  int msg_id_anterior = -1;
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
  cola[libre].msg_id_esperado = -1;
  cola[libre].msg_id_anterior = -1;
  desbloquear();
  log_info(ETIQUETA, "encolada id=%s topico=%s pendientes=%d", id.c_str(), topico.c_str(),
           alertas_pendientes());
}

}  // namespace

// Recibe el MQTT_EVENT_PUBLISHED que ya enruto handleMQTT (el camino unico de
// esp-mqtt). El evento llega como void* porque alertas.h no arrastra esp-mqtt.
// Solo mira confirmaciones; el resto de eventos los sigue atendiendo
// ESP32MQTTClient con su propio switch. Vive FUERA del namespace anonimo (debe
// ser simbolo externo visible para mqtt_gestor.cpp); las variables del modulo
// siguen siendo visibles en este TU despues de su declaracion.
void alertas_evento_publicado(void *datos) {
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

  // Correlacion por msg_id: solo se libera la entrada si el PUBACK corresponde a
  // una de las publicaciones de ESTA alerta (el ultimo envio o el anterior). Un
  // PUBACK rezagado de otra alerta ya descartada no puede liberar la actual.
  Entrada &entrada = cola[en_vuelo];
  if (msg_id != entrada.msg_id_esperado && msg_id != entrada.msg_id_anterior) {
    const int esperado = entrada.msg_id_esperado;
    desbloquear();
    log_aviso(ETIQUETA, "PUBLICADO msg_id=%d no es de la alerta en vuelo (esperado %d), se ignora",
              msg_id, esperado);
    return;
  }

  String id = entrada.id;
  entrada.ocupada = false;
  entrada.intentos = 0;
  entrada.msg_id_esperado = -1;
  entrada.msg_id_anterior = -1;
  indice_en_vuelo = -1;
  desbloquear();

  ultimo_msg_id = msg_id;
  log_info(ETIQUETA, "CONFIRMADA msg_id=%d id=%s (la cola baja a %d)", msg_id, id.c_str(),
           alertas_pendientes());
}

void alertas_iniciar() {
  semaforo_cola = xSemaphoreCreateMutex();
  if (semaforo_cola == nullptr) {
    log_error(ETIQUETA, "no se pudo crear el mutex de la cola");
  }
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
    // El watchdog actua a los 8 s, mucho antes de que el outbox de esp-mqtt
    // descarte el mensaje a los 30 s, y se republica con el MISMO id. No hay tope
    // de intentos: por contrato la alerta se reenvia hasta que el broker confirme.
    const int msg_id = mqtt_publicar_qos_con_msg_id(en_vuelo.topico, en_vuelo.payload,
                                                    QOS_ALERTA_GAS, false);
    if (msg_id < 0) {
      // Sin sesion real en este instante: no cuenta como intento; se reintenta en
      // el siguiente ciclo. Los reintentos solo cuentan con conexion MQTT.
      desbloquear();
      return;
    }
    if (en_vuelo.intentos < 0xFFFF) {
      en_vuelo.intentos += 1;
    }
    en_vuelo.msg_id_anterior = en_vuelo.msg_id_esperado;
    en_vuelo.msg_id_esperado = msg_id;
    en_vuelo.ultimo_intento_ms = ahora;
    log_aviso(ETIQUETA, "id=%s sin PUBACK en %lu ms, reintento %u (msg_id=%d)",
              en_vuelo.id.c_str(), static_cast<unsigned long>(transcurrido),
              static_cast<unsigned>(en_vuelo.intentos), msg_id);
    desbloquear();
    return;
  }

  // Hay hueco en vuelo: se manda la primera alerta de la cola.
  for (uint8_t i = 0; i < CAPACIDAD_COLA; ++i) {
    if (!cola[i].ocupada) {
      continue;
    }
    const int msg_id = mqtt_publicar_qos_con_msg_id(cola[i].topico, cola[i].payload,
                                                    QOS_ALERTA_GAS, false);
    if (msg_id < 0) {
      desbloquear();
      return;
    }
    cola[i].intentos = 1;
    cola[i].msg_id_anterior = -1;
    cola[i].msg_id_esperado = msg_id;
    cola[i].ultimo_intento_ms = millis();
    indice_en_vuelo = i;
    log_info(ETIQUETA, "ENVIANDO id=%s topico=%s intento=%d msg_id=%d", cola[i].id.c_str(),
             cola[i].topico.c_str(), cola[i].intentos, msg_id);
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
