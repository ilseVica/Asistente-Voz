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
  // El ts va como ENTERO epoch en segundos, el mismo tipo que llevan luz/set y
  // satelite/decir. Antes iba como texto ISO 8601, y con eso el mismo campo era un
  // entero en un topico y una cadena en otro: el consumidor tenia que ramificar
  // segun de donde saliera. Sin hora valida sale null, que es un valor explicito de
  // "todavia no lo se" y se distingue de un 0 (que en epoch es 1970).
  const int64_t ts = hora_epoch_o_desconocida();
  if (ts < 0) {
    carga += ",\"ts\":null";
  } else {
    carga += ",\"ts\":" + String(static_cast<long long>(ts));
  }
  carga += ",\"ip\":\"" + red_ip() + "\"";
  carga += ",\"fw\":\"" + String(NODO_ESTADO_CAMPO_VERSION) + "\"";
  // gas_adc sin dato sale como null, igual que «ts»: -1 no es un valor del ADC y
  // meterlo en el JSON dejaria al consumidor creyendo que hubo una lectura.
  const int gas = gas_ultima_lectura();
  if (gas < 0) {
    carga += ",\"gas_adc\":null";
  } else {
    carga += ",\"gas_adc\":" + String(gas);
  }
  carga += ",\"alertas_pendientes\":" + String(alertas_pendientes());
  carga += ",\"gas_episodio\":" + String(gas_episodio_abierto() ? "true" : "false");
  carga += ",\"alexa\":" + String(alexa_habilitado() ? "true" : "false");
  carga += "}";
  return carga;
}

String nodo_estado_resumen() {
  char texto_gas[12];
  const int gas = gas_ultima_lectura();
  if (gas < 0) {
    snprintf(texto_gas, sizeof(texto_gas), "sin_dato");
  } else {
    snprintf(texto_gas, sizeof(texto_gas), "%d", gas);
  }
  char texto[160];
  snprintf(texto, sizeof(texto), "ip=%s gas=%s umbral_ok=%s alertas=%d gas_episodio=%s luz=%s",
           red_ip().c_str(), texto_gas, hora_es_valida() ? "ok" : "sin_hora",
           alertas_pendientes(), gas_episodio_abierto() ? "abierto" : "cerrado",
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
