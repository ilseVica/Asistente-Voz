#include "hora.h"

#include <time.h>

#include "config.h"
#include "registro.h"

namespace {
constexpr char ETIQUETA[] = "hora";

// El respaldo es el NTP de la VM, que hoy NO escucha en el puerto 123 (6.5). El
// firmware funciona sin el porque los publicos van primero; cuando el usuario
// aplique la configuracion de chrony, este empieza a responder y la prueba P44 pasa.
constexpr char NTP_PRIMARIO[] = "pool.ntp.org";
constexpr char NTP_RESPALDO[] = "192.168.1.169";
constexpr char NTP_ZONA[] = "UTC0";  // se guarda UTC y se convierte al publicar

// 2024-01-01T00:00:00Z. Antes de esto, el reloj no se considera ajustado.
constexpr int64_t UMBRAL_HORA_VALIDA = 1704067200LL;

bool hora_valida = false;
uint32_t proximo_intento_ms = 0;
constexpr uint32_t MS_REINTENTO_SIN_HORA = 60000UL;
constexpr uint32_t MS_REINTENTO_CON_HORA = MS_60S;

bool intentar_sincronizar() {
  configTime(0, 0, NTP_PRIMARIO, NTP_RESPALDO);
  time_t ahora = time(nullptr);
  if (ahora < UMBRAL_HORA_VALIDA) {
    return false;
  }
  return true;
}
}  // namespace

void hora_iniciar() {
  setenv("TZ", ZONA_HORARIA, 1);
  tzset();
  hora_valida = intentar_sincronizar();
  proximo_intento_ms = millis() + (hora_valida ? MS_REINTENTO_CON_HORA : MS_REINTENTO_SIN_HORA);
  if (hora_valida) {
    log_info(ETIQUETA, "hora valida: %s", hora_iso_utc().c_str());
  } else {
    log_aviso(ETIQUETA, "sin hora valida todavia; luz/set se descartara hasta tenerla");
  }
}

void hora_tarea() {
  const uint32_t ahora_ms = millis();
  if ((int32_t)(ahora_ms - proximo_intento_ms) < 0) {
    return;
  }
  proximo_intento_ms = ahora_ms + (hora_valida ? MS_REINTENTO_CON_HORA : MS_REINTENTO_SIN_HORA);

  const bool antes = hora_valida;
  hora_valida = intentar_sincronizar();
  if (hora_valida && !antes) {
    log_info(ETIQUETA, "hora conseguida: %s", hora_iso_utc().c_str());
  } else if (!hora_valida && antes) {
    log_aviso(ETIQUETA, "se perdio la hora; luz/set se descartara");
  }
}

bool hora_es_valida() {
  return hora_valida;
}

int64_t hora_epoch() {
  return static_cast<int64_t>(time(nullptr));
}

int64_t hora_epoch_o_desconocida() {
  return hora_valida ? hora_epoch() : -1;
}

String hora_iso_utc() {
  if (!hora_valida) {
    return String("");
  }
  const time_t ahora = time(nullptr);
  struct tm descompuesto;
  gmtime_r(&ahora, &descompuesto);
  char texto[32];
  strftime(texto, sizeof(texto), "%Y-%m-%dT%H:%M:%SZ", &descompuesto);
  return String(texto);
}

String hora_iso_para_will() {
  return hora_iso_utc();
}
