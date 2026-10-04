#include "registro.h"

#include <stdarg.h>

#include <WiFi.h>
#include <time.h>

#include "config.h"

namespace {
NivelLog nivel_actual = NivelLog::Info;
}

void registrar_iniciar(NivelLog nivel) {
  nivel_actual = nivel;
}

NivelLog registrar_nivel() {
  return nivel_actual;
}

void registrar_escribir(NivelLog nivel, const char *etiqueta, const char *formato, ...) {
  if (static_cast<uint8_t>(nivel) > static_cast<uint8_t>(nivel_actual)) {
    return;
  }
  char cuerpo[256];
  va_list argumentos;
  va_start(argumentos, formato);
  vsnprintf(cuerpo, sizeof(cuerpo), formato, argumentos);
  va_end(argumentos);

  Serial.printf("[%s] %-8s %s\n", registrar_hora().c_str(), etiqueta, cuerpo);
}

String registrar_hora() {
  struct timeval ahora;
  if (gettimeofday(&ahora, nullptr) != 0 || ahora.tv_sec < 1600000000L) {
    return String("--:--:--");
  }
  struct tm descompuesto;
  localtime_r(&ahora.tv_sec, &descompuesto);
  char texto[16];
  strftime(texto, sizeof(texto), "%H:%M:%S", &descompuesto);
  return String(texto);
}

String identificador_mac_nodo() {
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  char texto[7];
  snprintf(texto, sizeof(texto), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3],
           mac[4], mac[5]);
  // El formato pide MAC[2..7], es decir los ultimos seis caracteres del MAC.
  return String(texto + 2);
}

String id_alerta_construir(uint16_t arranque, uint16_t contador) {
  char texto[32];
  snprintf(texto, sizeof(texto), "%s-b%04x-%04x", identificador_mac_nodo().c_str(),
           static_cast<unsigned>(arranque & 0xFFFF), static_cast<unsigned>(contador & 0xFFFF));
  return String(texto);
}
