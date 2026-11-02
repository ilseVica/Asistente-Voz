#include "wifi_gestor.h"
#include "mqtt_gestor.h"

#include <ESPmDNS.h>
#include <esp_attr.h>
#include <WiFi.h>

#include <time.h>

#include "config.h"
#include "persistencia.h"
#include "registro.h"
#include "secrets.h"

namespace {
constexpr char ETIQUETA[] = "wifi";
constexpr char MDNS_NOMBRE[] = "easier-asistente";

constexpr uint32_t MS_ESPERA_MINIMA = 1000UL;
constexpr uint32_t MS_ESPERA_MAXIMA = 30000UL;

// Orden de preferencia (mayor a menor): 1 (principal), 3 (repetidor), 2 (hotspot).
struct APConocido {
  int red;
  const char *ssid;
  const char *pass;
};

const APConocido APS[] = {
    {1, WIFI1_SSID, WIFI1_PASS},
    {3, WIFI3_SSID, WIFI3_PASS},
    {2, WIFI2_SSID, WIFI2_PASS},
};
constexpr size_t NUM_APS = sizeof(APS) / sizeof(APS[0]);

bool conectado = false;
int red_actual = 0;
int red_ultima_conectada = 0;
bool red_cambio_pendiente = false;
RTC_NOINIT_ATTR static uint32_t rtc_magico = 0;
RTC_NOINIT_ATTR static int8_t rtc_red_forzada = 0;
RTC_NOINIT_ATTR static uint32_t rtc_marca_reinicio = 0;
static constexpr uint32_t RTC_MAGICO_VAL = 0xA5A55A5AU;
static constexpr uint32_t RTC_MARCA_REINICIO_VAL = 0x52454401U;
static uint32_t forzado_inicio_ms = 0;
bool mdns_activo = false;
uint32_t espera_actual_ms = MS_ESPERA_MINIMA;
uint32_t proximo_intento_ms = 0;

int red_de_ssid(const String &ssid) {
  for (size_t i = 0; i < NUM_APS; ++i) {
    if (ssid == String(APS[i].ssid)) {
      return APS[i].red;
    }
  }
  return 0;
}

// Devuelve el numero de red preferida disponible en el escaneo, o 0 si ninguna.
const APConocido *ap_por_red(int red);
int escanear_preferida() {
  const int forzada = red_forzado();
  if (forzada != 0) {
    const APConocido *ap = ap_por_red(forzada);
    if (ap != nullptr) {
      const int n = WiFi.scanNetworks();
      if (n > 0) {
        for (int i = 0; i < n; ++i) {
          String ssid_scan;
          int32_t rssi_scan;
          uint8_t sec_scan;
          uint8_t *bssid_scan;
          int32_t chan_scan;
          WiFi.getNetworkInfo(i, ssid_scan, sec_scan, rssi_scan, bssid_scan, chan_scan);
          if (ssid_scan == String(ap->ssid)) {
            WiFi.scanDelete();
            return forzada;
          }
        }
      }
      WiFi.scanDelete();
    }
    return 0;
  }

  const int n = WiFi.scanNetworks();
  if (n <= 0) {
    WiFi.scanDelete();
    return 0;
  }
  // Para cada AP en orden de preferencia, ver si aparece en el escaneo.
  for (size_t p = 0; p < NUM_APS; ++p) {
    for (int i = 0; i < n; ++i) {
      String ssid_scan;
      int32_t rssi_scan;
      uint8_t sec_scan;
      uint8_t *bssid_scan;
      int32_t chan_scan;
      WiFi.getNetworkInfo(i, ssid_scan, sec_scan, rssi_scan, bssid_scan, chan_scan);
      if (ssid_scan == String(APS[p].ssid)) {
        WiFi.scanDelete();
        return APS[p].red;
      }
    }
  }
  WiFi.scanDelete();
  return 0;
}

const APConocido *ap_por_red(int red) {
  for (size_t i = 0; i < NUM_APS; ++i) {
    if (APS[i].red == red) {
      return &APS[i];
    }
  }
  return nullptr;
}

void manejar_cambio_de_red(int nuevo) {
  if (red_ultima_conectada != 0 && red_ultima_conectada != nuevo) {
    red_cambio_pendiente = true;
  }
  red_ultima_conectada = nuevo;
}
}  // namespace


int red_forzado() {
  if (rtc_magico != RTC_MAGICO_VAL) {
    return 0;
  }
  if (rtc_red_forzada < 0 || rtc_red_forzada > 3) {
    return 0;
  }
  return rtc_red_forzada;
}

void red_forzar(int red) {
  if (red <= 0 || red > 3) {
    rtc_magico = 0;
    rtc_red_forzada = 0;
  } else {
    rtc_magico = RTC_MAGICO_VAL;
    rtc_red_forzada = static_cast<int8_t>(red);
  }
  forzado_inicio_ms = (red > 0) ? millis() : 0;
  log_info(ETIQUETA, "red forzada: %d (%s)", red > 0 ? red : 0,
           red > 0 ? "forzada" : "auto");
}

void red_aplicar_seleccion(int red) {
  const int anterior = red_forzado();
  if (red <= 0 || red > 3) {
    red_forzar(0);
    if (anterior != 0) {
      red_marcar_reinicio_por_red();
    }
  } else {
    red_forzar(red);
    if (anterior != red) {
      red_marcar_reinicio_por_red();
    }
  }
}

void red_marcar_reinicio_por_red() {
  rtc_marca_reinicio = RTC_MARCA_REINICIO_VAL;
  red_cambio_pendiente = true;
  log_info(ETIQUETA, "marcado reinicio por RED");
}

void red_iniciar() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(false);
  WiFi.setHostname(MDNS_NOMBRE);

  const uint32_t ahora = millis();
  proximo_intento_ms = ahora + MS_ESPERA_MINIMA;
  if (rtc_marca_reinicio == RTC_MARCA_REINICIO_VAL) {
    log_info(ETIQUETA, "arranque por RED: no cuenta para el limite");
    rtc_marca_reinicio = 0;
  }
  if (red_forzado() != 0) {
    forzado_inicio_ms = millis();
  }
  log_info(ETIQUETA, "conectando (3 redes, por preferencia)");
}

void red_tarea() {
  if (red_cambio_pendiente) {
    red_cambio_pendiente = false;
    const bool manual = (rtc_marca_reinicio == RTC_MARCA_REINICIO_VAL);
    if (!manual) {
      uint8_t cnt = persistencia_reinicios_red_cnt();
      uint32_t epoch_guardado = persistencia_reinicios_red_epoch();
      const uint32_t ahora_epoch = static_cast<uint32_t>(time(nullptr));
      bool dentro_ventana = (epoch_guardado != 0 && ahora_epoch >= epoch_guardado &&
                             (ahora_epoch - epoch_guardado) <= 300U);
      uint8_t nuevo_cnt = dentro_ventana ? static_cast<uint8_t>(cnt + 1) : 1U;
      if (nuevo_cnt >= 3U) {
        log_aviso(ETIQUETA,
                  "limite de reinicios por cambio de red alcanzado (%u en 5 min); "
                  "me quedo en la red actual",
                  static_cast<unsigned>(nuevo_cnt));
        persistencia_fijar_reinicios_red(nuevo_cnt, ahora_epoch);
        return;
      }
      persistencia_fijar_reinicios_red(nuevo_cnt, ahora_epoch);
      log_info(ETIQUETA, "cambio de red detectado; reiniciando (reinicio %u/3)",
               static_cast<unsigned>(nuevo_cnt));
    } else {
      log_info(ETIQUETA, "cambio manual de red; reiniciando sin contar");
    }
    delay(200);
    ESP.restart();
    return;
  }

  const bool ahora_conectado = (WiFi.status() == WL_CONNECTED);
  if (ahora_conectado) {
    if (!conectado) {
      conectado = true;
      espera_actual_ms = MS_ESPERA_MINIMA;
      red_actual = red_de_ssid(WiFi.SSID());
      manejar_cambio_de_red(red_actual);
      log_info(ETIQUETA, "conectado, red %d, IP %s", red_actual, red_ip().c_str());
      red_iniciar_mdns();
      red_anunciar_mdns();
    }
    return;
  }

  if (conectado) {
    conectado = false;
    red_actual = 0;
    proximo_intento_ms = millis() + (MS_ESPERA_MINIMA * 2);
    log_aviso(ETIQUETA, "sin red, reintento en %lu ms",
              static_cast<unsigned long>(MS_ESPERA_MINIMA * 2));
    return;
  }

  // Seguro del modo forzado: si no conecta (Wi-Fi + broker) en 60 s, vuelve a AUTO.
  if (red_forzado() != 0) {
    if (conectado && mqtt_conectado() && red_actual == red_forzado()) {
      forzado_inicio_ms = 0;
    } else {
      if (forzado_inicio_ms == 0) {
        forzado_inicio_ms = millis();
      } else if (millis() - forzado_inicio_ms >= 60000UL) {
        log_aviso(ETIQUETA, "red forzada %d no conecto en 60 s; vuelvo a AUTO",
                  red_forzado());
        red_forzar(0);
        red_marcar_reinicio_por_red();
        forzado_inicio_ms = 0;
      }
    }
  }

  const uint32_t ahora = millis();
  if ((int32_t)(ahora - proximo_intento_ms) < 0) {
    return;
  }

  log_info(ETIQUETA, "reintentando conexion");
  const int preferida = escanear_preferida();
  if (preferida == 0) {
    log_aviso(ETIQUETA, "ninguna red conocida disponible");
    proximo_intento_ms = millis() + espera_actual_ms;
    if (espera_actual_ms < MS_ESPERA_MAXIMA) {
      espera_actual_ms *= 2;
      if (espera_actual_ms > MS_ESPERA_MAXIMA) espera_actual_ms = MS_ESPERA_MAXIMA;
    }
    return;
  }
  const APConocido *ap = ap_por_red(preferida);
  if (ap == nullptr) {
    proximo_intento_ms = millis() + espera_actual_ms;
    return;
  }
  WiFi.begin(ap->ssid, ap->pass);
  const uint32_t inicio = millis();
  uint8_t estado = WiFi.status();
  while (estado != WL_CONNECTED && estado != WL_NO_SSID_AVAIL &&
         estado != WL_CONNECT_FAILED && (millis() - inicio) <= 8000UL) {
    delay(10);
    estado = WiFi.status();
  }
  if (estado == WL_CONNECTED) {
    // se reporta en el siguiente ciclo al transicionar
  } else {
    log_aviso(ETIQUETA, "fallo al conectar a red %d", preferida);
  }

  proximo_intento_ms = millis() + espera_actual_ms;
  if (espera_actual_ms < MS_ESPERA_MAXIMA) {
    espera_actual_ms *= 2;
    if (espera_actual_ms > MS_ESPERA_MAXIMA) espera_actual_ms = MS_ESPERA_MAXIMA;
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

int red_indice_actual() {
  return red_actual;
}

void red_iniciar_mdns() {
  if (mdns_activo) {
    return;
  }
  if (MDNS.begin(MDNS_NOMBRE)) {
    mdns_activo = true;
    log_info(ETIQUETA, "mDNS activo, el nodo es %s.local", MDNS_NOMBRE);
  } else {
    log_aviso(ETIQUETA, "mDNS no arranco; se usara la IP directa");
  }
}

void red_anunciar_mdns() {
  MDNS.addService("http", "tcp", 80);
}

String red_resolver_broker(const String &host) {
  if (host.isEmpty()) {
    return String("");
  }

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

  String nombre = host;
  if (nombre.endsWith(".local")) {
    nombre = nombre.substring(0, nombre.length() - 6);
  }
  IPAddress resuelta = MDNS.queryHost(nombre.c_str(), 2000);
  if (resuelta != INADDR_NONE) {
    const String direccion(resuelta.toString());
    log_info(ETIQUETA, "broker %s resuelto a %s", host.c_str(), direccion.c_str());
    persistencia_fijar_host_previo(host);
    return direccion;
  }

  const String previo = persistencia_host_previo();
  if (!previo.isEmpty() && previo != host) {
    log_aviso(ETIQUETA, "no se pudo resolver %s, se usa el host previo %s", host.c_str(),
              previo.c_str());
    return previo;
  }

  log_error(ETIQUETA, "no se pudo resolver el broker '%s' y no hay host previo", host.c_str());
  return String("");
}
