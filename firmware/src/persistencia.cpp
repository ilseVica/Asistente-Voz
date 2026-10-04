#include "persistencia.h"

#include <Preferences.h>

#include "config.h"
#include "registro.h"

namespace {
constexpr char ETIQUETA[] = "nvs";
constexpr char ESPACIO[] = "nodo";
constexpr char CLAVE_ARRANQUE[] = "arranque";
constexpr char CLAVE_CONTADOR[] = "contador";
constexpr char CLAVE_UMBRAL[] = "umbral_gas";
constexpr char CLAVE_HOST[] = "host_previo";

Preferences nvs;
uint16_t arranque_actual = 0;
uint16_t contador_alerta = 0;
int umbral_gas = 0;
String host_previo;
}  // namespace

void persistencia_iniciar() {
  if (!nvs.begin(ESPACIO, false)) {
    log_error(ETIQUETA, "no se pudo abrir NVS, se siguen los valores de config.h");
    arranque_actual = 0;
    contador_alerta = 0;
    umbral_gas = GAS_UMBRAL_APERTURA;
    return;
  }

  // El contador de arranques se incrementa en cada arranque y es la parte del id
  // que cambia al reiniciar (prueba P28 del plan).
  arranque_actual = static_cast<uint16_t>(nvs.getULong(CLAVE_ARRANQUE, 0) + 1);
  nvs.putULong(CLAVE_ARRANQUE, arranque_actual);

  contador_alerta = static_cast<uint16_t>(nvs.getUShort(CLAVE_CONTADOR, 0));
  umbral_gas = nvs.getInt(CLAVE_UMBRAL, GAS_UMBRAL_APERTURA);
  host_previo = nvs.getString(CLAVE_HOST, "").c_str();

  nvs.end();
  log_info(ETIQUETA, "arranque=%u contador=%u umbral_gas=%d", arranque_actual, contador_alerta,
           umbral_gas);
}

uint16_t persistencia_arranque_actual() {
  return arranque_actual;
}

uint16_t persistencia_siguiente_contador_alerta() {
  ++contador_alerta;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putUShort(CLAVE_CONTADOR, contador_alerta);
    nvs.end();
  }
  return contador_alerta;
}

int persistencia_umbral_gas() {
  return umbral_gas;
}

void persistencia_fijar_umbral_gas(int valor) {
  if (valor <= 0) {
    log_aviso(ETIQUETA, "umbral %d invalido, se conserva %d", valor, umbral_gas);
    return;
  }
  umbral_gas = valor;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putInt(CLAVE_UMBRAL, valor);
    nvs.end();
  }
  log_info(ETIQUETA, "umbral_gas ahora %d", valor);
}

String persistencia_host_previo() {
  return host_previo;
}

void persistencia_fijar_host_previo(const String &host) {
  if (host.isEmpty() || host == host_previo) {
    return;
  }
  host_previo = host;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putString(CLAVE_HOST, host);
    nvs.end();
  }
  log_info(ETIQUETA, "host previo guardado: %s", host.c_str());
}
