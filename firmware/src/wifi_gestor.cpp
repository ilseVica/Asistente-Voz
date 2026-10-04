#include "wifi_gestor.h"

#include <ESPmDNS.h>
#include <WiFi.h>

#include "config.h"
#include "persistencia.h"
#include "registro.h"
#include "secrets.h"

namespace {
constexpr char ETIQUETA[] = "wifi";
constexpr char MDNS_NOMBRE[] = "easier-asistente";

// Espera creciente: 1 s, 2 s, 4 s... hasta 30 s. Asi un router que tarda en
// arrancar no se martillea, pero una red estable reconecta casi de inmediato.
constexpr uint32_t MS_ESPERA_MINIMA = 1000UL;
constexpr uint32_t MS_ESPERA_MAXIMA = 30000UL;

bool conectado = false;
uint32_t espera_actual_ms = MS_ESPERA_MINIMA;
uint32_t proximo_intento_ms = 0;
}  // namespace

void red_iniciar() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // la red es de casa, no conviene dormir
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // El hostname no es un secreto y va en config.h. El client id del broker se
  // deriva de el mas adelante, en mqtt_gestor.
  WiFi.setHostname(MDNS_NOMBRE);

  const uint32_t ahora = millis();
  proximo_intento_ms = ahora + MS_ESPERA_MINIMA;
  log_info(ETIQUETA, "conectando a %s", WIFI_SSID);
}

void red_tarea() {
  const bool ahora_conectado = (WiFi.status() == WL_CONNECTED);
  if (ahora_conectado) {
    if (!conectado) {
      conectado = true;
      espera_actual_ms = MS_ESPERA_MINIMA;
      log_info(ETIQUETA, "conectado, IP %s", red_ip().c_str());
      red_iniciar_mdns();
      red_anunciar_mdns();
    }
    return;
  }

  if (conectado) {
    conectado = false;
    proximo_intento_ms = millis() + (MS_ESPERA_MINIMA * 2);
    log_aviso(ETIQUETA, "sin red, reintento en %lu ms",
              static_cast<unsigned long>(MS_ESPERA_MINIMA * 2));
    return;
  }

  const uint32_t ahora = millis();
  if ((int32_t)(ahora - proximo_intento_ms) < 0) {
    return;
  }

  log_info(ETIQUETA, "reintentando conectar a %s", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // La espera crece hasta el tope y se queda ahi.
  proximo_intento_ms = ahora + espera_actual_ms;
  if (espera_actual_ms < MS_ESPERA_MAXIMA) {
    espera_actual_ms *= 2;
    if (espera_actual_ms > MS_ESPERA_MAXIMA) {
      espera_actual_ms = MS_ESPERA_MAXIMA;
    }
  }
}

bool red_conectado() {
  return conectado && (WiFi.status() == WL_CONNECTED);
}

String red_ip() {
  if (!red_conectado()) {
    return String("0.0.0.0");
  }
  return String(WiFi.localIP().toString().c_str());
}

void red_iniciar_mdns() {
  if (MDNS.begin(MDNS_NOMBRE)) {
    log_info(ETIQUETA, "mDNS activo, el nodo es %s.local", MDNS_NOMBRE);
  } else {
    log_aviso(ETIQUETA, "mDNS no arranco; se usara la IP directa");
  }
}

void red_anunciar_mdns() {
  // mDNS es SOLO para nombres. El descubrimiento de Alexa lo hace fauxmoESP por
  // SSDP, que es un protocolo distinto (1.4 y 2.12).
  MDNS.addService("http", "tcp", 80);
}

String red_resolver_broker(const String &host) {
  if (host.isEmpty()) {
    return String("");
  }

  // Si ya parece una IP, no hay nada que resolver.
  if (host.indexOf('.') > 0 && host.indexOf(':') < 0) {
    int octetos = 0;
    bool valida = true;
    int inicio = 0;
    for (int i = 0; i <= host.length(); ++i) {
      if (i == host.length() || host[i] == '.') {
        const int largo = i - inicio;
        if (largo < 1 || largo > 3) {
          valida = false;
          break;
        }
        for (int j = inicio; j < i; ++j) {
          if (!isdigit(static_cast<unsigned char>(host[j]))) {
            valida = false;
            break;
          }
        }
        ++octetos;
        inicio = i + 1;
      }
    }
    if (valida && octetos == 4) {
      return host;
    }
  }

  IPAddress resuelta = MDNS.begin(host.c_str());
  if (resuelta != INADDR_NONE) {
    const String direccion(resuelta.toString());
    log_info(ETIQUETA, "broker %s resuelto a %s", host.c_str(), direccion.c_str());
    persistencia_fijar_host_previo(host);
    return direccion;
  }

  // No resuelve por mDNS: se intenta el host que ya funciono antes.
  const String previo = persistencia_host_previo();
  if (!previo.isEmpty() && previo != host) {
    log_aviso(ETIQUETA, "no se pudo resolver %s, se usa el host previo %s", host.c_str(),
              previo.c_str());
    return previo;
  }

  log_error(ETIQUETA, "no se pudo resolver el broker '%s' y no hay host previo", host.c_str());
  return String("");
}
