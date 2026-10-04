#include "nodo_estado.h"

#include "alexa.h"
#include "alertas.h"
#include "config.h"
#include "gas.h"
#include "hora.h"
#include "luz.h"
#include "mqtt_gestor.h"
#include "registro.h"
#include "sensores.h"
#include "wifi_gestor.h"

namespace {
constexpr char ETIQUETA[] = "estado";
uint32_t proximo_latido_ms = 0;
}  // namespace

void nodo_estado_iniciar() {
  // Se publica en cuanto haya sesion, y luego cada minuto.
  proximo_latido_ms = 0;
  log_info(ETIQUETA, "latido cada %lu s",
           static_cast<unsigned long>(PERIODO_NODO_ESTADO_MS / MS_1S));
}

String nodo_estado_json() {
  String carga = "{";
  carga += "\"conectado\":true";
  carga += ",\"ts\":\"" + hora_iso_para_will() + "\"";
  carga += ",\"ip\":\"" + red_ip() + "\"";
  carga += ",\"fw\":\"" + String(NODO_ESTADO_CAMPO_VERSION) + "\"";
  carga += ",\"gas_adc\":" + String(gas_ultima_lectura());
  carga += ",\"alertas_pendientes\":" + String(alertas_pendientes());
  carga += ",\"gas_episodio\":" + String(gas_episodio_abierto() ? "true" : "false");
  carga += ",\"alexa\":" + String(alexa_habilitado() ? "true" : "false");
  carga += "}";
  return carga;
}

String nodo_estado_resumen() {
  char texto[160];
  snprintf(texto, sizeof(texto), "ip=%s gas=%d umbral_ok=%s alertas=%d gas_episodio=%s luz=%s",
           red_ip().c_str(), gas_ultima_lectura(),
           hora_es_valida() ? "ok" : "sin_hora", alertas_pendientes(),
           gas_episodio_abierto() ? "abierto" : "cerrado",
           luz_tiene_estado_conocido() ? (luz_encendida() ? "on" : "off") : "desconocido");
  return String(texto);
}

void nodo_estado_tarea() {
  if (!mqtt_conectado()) {
    proximo_latido_ms = 0;
    return;
  }

  const uint32_t ahora = millis();
  if (proximo_latido_ms != 0 && (int32_t)(ahora - proximo_latido_ms) < 0) {
    return;
  }
  proximo_latido_ms = ahora + PERIODO_NODO_ESTADO_MS;

  const String carga = nodo_estado_json();
  mqtt_publicar(String(TOPIC_NODO_ESTADO), carga, QOS_NODO_ESTADO, true);
  log_info(ETIQUETA, "%s | %s", carga.c_str(), nodo_estado_resumen().c_str());
}
