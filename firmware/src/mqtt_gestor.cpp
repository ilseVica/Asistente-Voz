#include <ESP32MQTTClient.h>
#include <esp_idf_version.h>
#include <esp_log.h>

#include "alertas.h"
#include "config.h"
#include "contrato.h"
#include "hora.h"
#include "luz.h"
#include "mqtt_gestor.h"
#include "nodo_estado.h"
#include "registro.h"
#include "secrets.h"
#include "wifi_gestor.h"

namespace {
constexpr char ETIQUETA[] = "mqtt";

ESP32MQTTClient cliente;
bool sesion_activa = false;
String broker_usado;
// Handle de esp-mqtt del cliente de la libreria, capturado en onMqttConnect(). Se
// usa para publicar con esp_mqtt_client_publish() y poder leer el msg_id que
// devuelve (correlacion de PUBACK en alertas.cpp). Null hasta la primera conexion.
esp_mqtt_client_handle_t cliente_esp_actual = nullptr;

const char *construir_will() {
  // Last Will retenido en nodo/estado con conectado=false. El broker lo publica
  // si el ESP32 se cae sin cerrar la sesion (prueba P27 del plan).
  return "{\"conectado\":false}";
}

// Suscripciones SIN callback propio (nullptr): el enrutado por topico lo hace
// setOnMessageCallback de arriba, que es el unico camino. Devuelve true si las
// tres llamadas encolaron su SUBSCRIBE.
bool mqtt_suscribir() {
  MessageReceivedCallback sin_callback = nullptr;
  const bool ok_luz = cliente.subscribe(TOPIC_LUZ_SET, sin_callback, QOS_LUZ_SET_SUSCRIPCION);
  const bool ok_cfg = cliente.subscribe(TOPIC_NODO_CONFIG, sin_callback, QOS_NODO_CONFIG_SUSCRIPCION);
  bool ok = ok_luz && ok_cfg;
#if PRUEBAS == 1
  // Este topic solo existe en el firmware de pruebas (6.10).
  const bool ok_prueba =
      cliente.subscribe(TOPIC_PRUEBA_GAS, sin_callback, QOS_PRUEBA_GAS_SUSCRIPCION);
  ok = ok && ok_prueba;
  log_info(ETIQUETA, "PRUEBAS=1: suscrito a %s y comandos GAS por serie", TOPIC_PRUEBA_GAS);
#endif
  return ok;
}

}  // namespace

bool mqtt_iniciar() {
  const String broker = red_resolver_broker(String(MQTT_HOST));
  if (broker.isEmpty()) {
    // A nivel de depuracion: el motivo concreto. En nivel Info, quien lo ve es el
    // log de espera de main.cpp, que es una sola linea por intento y no se repite
    // cada 50 ms.
    log_depurar(ETIQUETA, "sin broker resoluble");
    return false;
  }
  broker_usado = broker;
  // Client id FIJO, sin partes aleatorias ni derivadas del hostname. El plan lo
  // pide de forma explicita: con un id aleatorio, cada reinicio dejaria una sesion
  // abierta en el broker durante 2 dias (Fase 4, "Client id fijo"). Con id fijo, el
  // broker reemplaza la sesion anterior en el mismo arranque.
  const char *client_id = MQTT_CLIENT_ID_BASE;

  char uri[128];
  snprintf(uri, sizeof(uri), "mqtt://%s:%d", broker.c_str(), MQTT_PORTO);
  cliente.setURI(uri, MQTT_USUARIO, MQTT_PASSWORD);

  // Tiene que ir antes de loopStart(): la libreria copia este valor al armar
  // _mqtt_config y de ahi al identificador que ve el broker.
  cliente.setMqttClientName(client_id);

  // Sesion persistente: protege las ordenes que van HACIA el ESP32. NO protege
  // las alertas que publica el ESP32, para eso esta la cola en RAM (2.6).
  cliente.enablePersistence();

  cliente.setKeepAlive(30);
  cliente.enableLastWillMessage(TOPIC_NODO_ESTADO, construir_will(), true, QOS_NODO_ESTADO);

  // Un SOLO camino de entrada para los mensajes. Antes se-tenian dos: este callback
  // global y ademas un callback por topico en cada subscribe(). Con los dos, cada
  // mensaje llegaba dos veces a contrato y se validaba dos veces. Las suscripciones
  // de mas abajo van sin callback y dejan que este enrute por topico.
  cliente.setOnMessageCallback([](const std::string &topico, const std::string &carga) {
    // Esta corre en la tarea de ESP32MQTTClient, no en el loop. Solo se copian
    // datos a los buffers del modulo de contrato, que esta protegido con mutex.
    const String t(topico.c_str());
    const String p(carga.c_str());
    if (t == TOPIC_LUZ_SET) {
      contrato_recibir_luz_set(p);
    } else if (t == TOPIC_NODO_CONFIG) {
      contrato_recibir_nodo_config(p);
    }
#if PRUEBAS == 1
    else if (t == TOPIC_PRUEBA_GAS) {
      contrato_recibir_prueba_gas(p);
    }
#endif
  });

  if (!cliente.loopStart()) {
    log_depurar(ETIQUETA, "loopStart fallo");
    return false;
  }
  log_info(ETIQUETA, "iniciado contra %s:%d como %s (%s, keepalive 30)", broker.c_str(), MQTT_PORTO,
           client_id, MQTT_USUARIO);
  return true;
}

void mqtt_al_conectar(esp_mqtt_client_handle_t cliente_esp) {
  if (!cliente.isMyTurn(cliente_esp)) {
    return;
  }

  // Se guarda el handle para poder publicar por la API de esp-mqtt y leer el
  // msg_id (correlacion de PUBACK). Es el mismo handle que usa la libreria.
  cliente_esp_actual = cliente_esp;

  (void)mqtt_suscribir();

  sesion_activa = true;

  // Estado retenido con conectado=true, que es lo que el servidor lee para saber
  // que el nodo esta vivo (inverso del Last Will).
  //
  // Se publica con la MISMA forma completa que el latido de nodo_estado_tarea(), no
  // con una version reducida. Antes aqui iban solo conectado y ts, y como el retenido
  // lo relee cualquiera, un consumidor podia encontrarse con tres formas distintas
  // del mismo topico segun cuando llegara: la de dos campos de este punto, la de ocho
  // campos del latido, y la de un solo campo del Last Will.
  const String estado = nodo_estado_json();
  cliente.publish(std::string(TOPIC_NODO_ESTADO), std::string(estado.c_str()), QOS_NODO_ESTADO,
                  true);
  log_info(ETIQUETA, "conectado al broker; %s", estado.c_str());

  // Regla "Reconexion" del contrato: al reconectarse se re-publica tambien el
  // estado de la luz (retenido) y los sensores siguen su latido normal (2.5).
  const String estado_luz = luz_estado_json();
  cliente.publish(std::string(TOPIC_LUZ_ESTADO), std::string(estado_luz.c_str()), QOS_LUZ_ESTADO,
                  true);
  log_info(ETIQUETA, "luz/estado al conectar: %s", estado_luz.c_str());
}

void mqtt_tarea() {
  const bool ahora_conectado = cliente.isConnected();
  if (ahora_conectado && !sesion_activa) {
    sesion_activa = true;
  } else if (!ahora_conectado && sesion_activa) {
    sesion_activa = false;
    // El handle deja de ser valido para publicar mientras no haya sesion; se
    // vuelve a capturar en el proximo onMqttConnect().
    cliente_esp_actual = nullptr;
    log_aviso(ETIQUETA, "sesion caida; las alertas pendientes siguen en la cola");
  }
}

bool mqtt_conectado() {
  return sesion_activa && cliente.isConnected();
}

bool mqtt_publicar(const String &topico, const String &payload, int qos, bool retener) {
  if (!mqtt_conectado()) {
    return false;
  }
  // Retorna true cuando el mensaje entra en el outbox de esp-mqtt, NO cuando el
  // broker lo recibe. Para las alertas, la confirmacion llega por
  // MQTT_EVENT_PUBLISHED (ver alertas.cpp).
  return cliente.publish(std::string(topico.c_str()), std::string(payload.c_str()), qos, retener);
}

int mqtt_publicar_qos_con_msg_id(const String &topico, const String &payload, int qos,
                                 bool retener) {
  if (!mqtt_conectado() || cliente_esp_actual == nullptr) {
    return -1;
  }
  // Misma llamada que ESP32MQTTClient::publish(), pero se conserva el msg_id que
  // devuelve esp-mqtt (>= 0) para que alertas.cpp pueda confirmar exactamente esa
  // publicacion cuando llegue su MQTT_EVENT_PUBLISHED. Devuelve -1 si no se pudo
  // encolar.
  return esp_mqtt_client_publish(cliente_esp_actual, topico.c_str(), payload.c_str(),
                                 static_cast<int>(payload.length()), qos, retener);
}

// ESP32MQTTClient la declara en su cabecera y no la define. Es el punto de
// extension por el que se registra el manejador de confirmaciones.
void onMqttConnect(esp_mqtt_client_handle_t cliente_esp) {
  mqtt_al_conectar(cliente_esp);
}

// La libreria tambien declara handleMQTT y no la define, y la necesita para el
// core 2.0.17 (IDF 4.x), donde se pasa en _mqtt_config.event_handle en vez de
// registrarse con esp_mqtt_client_register_event(). Sin esto no enlaza.
//
// Este es el CAMINO UNICO por el que pasan todos los eventos del cliente. Su
// trabajo habitual es pasarle el evento al switch de la libreria (CONNECTED,
// DATA, DISCONNECTED y ERROR), que la libreria atiende con onEventCallback().
//
// OJO: el switch de la libreria NO incluye MQTT_EVENT_PUBLISHED, que es justo el
// evento que hace falta para confirmar la entrega de una alerta (3.5). Antes este
// proyecto lo registraba aparte con alertas_registrar_manejador() ->
// esp_mqtt_client_register_event(), y eso era la causa raiz de DIAG-2026-10-08:
// en esp-mqtt v4.x esa funcion pone a NULL client->config.event_handle y deja
// pasar todos los eventos por el bucle de eventos, donde solo habia manejador
// para PUBLISHED; desde ahi MQTT_EVENT_DATA se publicaba al bucle sin nadie que
// lo recogiera y la entrada MQTT moria en silencio. Por eso PUBLISHED se enruta
// aqui, en el mismo punto directo por el que pasan los demas eventos.
#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 0, 0)
esp_err_t handleMQTT(esp_mqtt_event_handle_t evento) {
  if (evento->event_id == MQTT_EVENT_PUBLISHED) {
    // El evento llega como void* a proposito: alertas.h no arrastra esp-mqtt.
    alertas_evento_publicado(evento);
  } else {
    cliente.onEventCallback(evento);
  }
  return ESP_OK;
}
#else
void handleMQTT(void *argumentos, esp_event_base_t base, int32_t evento_id, void *datos) {
  (void)base;
  (void)evento_id;
  auto *instancia = static_cast<ESP32MQTTClient *>(argumentos);
  instancia->onEventCallback(static_cast<esp_mqtt_event_handle_t>(datos));
}
#endif
