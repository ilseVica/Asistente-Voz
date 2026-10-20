#include "hora.h"

#include <time.h>

#include "config.h"
#include "registro.h"

namespace {
constexpr char ETIQUETA[] = "hora";

// 2026-10-19: el NTP PRIMARIO es el de la VM (192.168.1.169, la misma red local
// de casa) y el respaldo es el servidor publico. Antes era al reves: con el de la
// VM todavia sin configurar chrony (6.5), la hora llegaba solo si habia internet,
// que es exactamente lo que la VM esta puesta para no depender.
constexpr char NTP_PRIMARIO[] = "192.168.1.169";
constexpr char NTP_RESPALDO[] = "pool.ntp.org";
constexpr char NTP_ZONA[] = "UTC0";  // se guarda UTC y se convierte al publicar

// 2024-01-01T00:00:00Z. Antes de esto, el reloj no se considera ajustado. Sin
// sincronizar, time(nullptr) en el ESP32 cuenta desde el arranque, o sea segundos
// de un dígito: el umbral no se cumple y no hace falta ningun otro indicador.
constexpr int64_t UMBRAL_HORA_VALIDA = 1704067200LL;

// Bandera SOLO para decidir cada cuanto se reintenta y para el log de transicion.
// La validez en si NO sale de aqui sino del reloj, en hora_es_valida(): si SNTP
// ajusta el reloj a mitad de intervalo, la hora deja de ser invalida en ese
// instante y no en el siguiente reintento.
bool hora_valida = false;
uint32_t proximo_intento_ms = 0;
// 5 s: el reintento es barato (solo reconfigura SNTP) y es el que permite que la
// primera hora valida llegue poco despues de conectar, no un minuto despues.
constexpr uint32_t MS_REINTENTO_SIN_HORA = 5000UL;
constexpr uint32_t MS_REINTENTO_CON_HORA = MS_60S;

void intentar_sincronizar() {
  configTime(0, 0, NTP_PRIMARIO, NTP_RESPALDO);
}
}  // namespace

void hora_iniciar() {
  setenv("TZ", ZONA_HORARIA, 1);
  tzset();
  intentar_sincronizar();
  hora_valida = hora_es_valida();
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

  // Primero se mira el reloj y despues se reconfigura SNTP: si el reloj ya quedo
  // ajustado por la respuesta que estaba en camino, no hace falta reiniciar nada.
  const bool antes = hora_valida;
  hora_valida = hora_es_valida();
  proximo_intento_ms = ahora_ms + (hora_valida ? MS_REINTENTO_CON_HORA : MS_REINTENTO_SIN_HORA);
  if (hora_valida && !antes) {
    log_info(ETIQUETA, "hora conseguida: %s", hora_iso_utc().c_str());
  } else if (!hora_valida && antes) {
    log_aviso(ETIQUETA, "se perdio la hora; luz/set se descartara");
  }
  if (!hora_valida) {
    intentar_sincronizar();
  }
}

bool hora_es_valida() {
  // El reloj se lee en CADA llamada: sin sincronizar devuelve segundos desde el
  // arranque, que es un numero pequenisimo, y el umbral de 2024 lo descarta sin
  // que haga falta ningun otro indicador. En cuanto SNTP ajusta, vale a partir de
  // ese instante, no en el siguiente reintento.
  return static_cast<int64_t>(time(nullptr)) >= UMBRAL_HORA_VALIDA;
}

int64_t hora_epoch() {
  return static_cast<int64_t>(time(nullptr));
}

int64_t hora_epoch_o_desconocida() {
  return hora_es_valida() ? hora_epoch() : -1;
}

String hora_iso_utc() {
  if (!hora_es_valida()) {
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
