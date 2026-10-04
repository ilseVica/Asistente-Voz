#include "alexa.h"

#include <fauxmoESP.h>

#include "alertas.h"
#include "config.h"
#include "luz.h"
#include "registro.h"
#include "wifi_gestor.h"

namespace {
constexpr char ETIQUETA[] = "alexa";

constexpr char NOMBRE_LUZ[] = "Luz de la sala";
constexpr char NOMBRE_AYUDA[] = "Ayuda";

fauxmoESP fauxmo;
bool habilitado = false;
bool habilitado_en_fauxmo = false;

// La orden de Alexa NO lleva ts y por eso no puede pasar por la regla de antiguedad
// de MQTT, que descarta justo lo que no trae ts (2.3: "las ordenes de Alexa y del
// boton NO llevan ts y no pasan por esta regla: son locales"). Por eso NO se manda a
// contrato_recibir_luz_set, sino que se aplica desde el loop.
//
// onSetState corre en la tarea de fauxmoESP, asi que aqui solo se deja la peticion
// en un buffer; tocar el rele desde ahi seria escribir el estado de la luz desde
// fuera del loop.
SemaphoreHandle_t semaforo_orden = nullptr;
bool hay_orden = false;
bool orden_encender = false;

// Peticion de ayuda. Llega desde el callback de fauxmoESP, asi que tambien se
// bufferiza y se resuelve en el loop.
bool hay_peticion_ayuda = false;
unsigned char id_dispositivo_ayuda = 0;

// Ventana de 60 s. El ESP32 NO suscribe casa/sala/alerta/ayuda, asi que no ve ni
// deduplica lo que publica el servidor por voz: esa ventana la aplica el servidor.
// Aqui solo se cubren las activaciones LOCALES (2.3).
uint32_t ultima_ayuda_ms = 0;
}  // namespace

bool alexa_habilitado() {
  return habilitado;
}

void alexa_iniciar() {
  // CRITICO (1.4 y 2.12): el puerto TCP por defecto de fauxmoESP es 1901, y los
  // dispositivos gen 3 que usa Alexa buscan en el 80. Sin esto, el nodo se anuncia
  // en un puerto que nadie va a preguntar y Alexa no lo encuentra nunca.
  fauxmo.setPort(80);

  // Se llama a enable(true) cuando ya hay WiFi, no antes. fauxmoESP levanta su
  // servidor TCP aqui.
  const unsigned char id_luz = fauxmo.addDevice(NOMBRE_LUZ);
  // El plan pide identificadores unicos de dispositivo, derivados de la MAC, para
  // que Alexa no los olvide entre arranques (6.12). Con un id que cambiara en cada
  // arranque, Alexa mostraria un sinnimo nuevo cada vez.
  const String unico_luz = identificador_mac_nodo();
  fauxmo.setDeviceUniqueId(id_luz, unico_luz.c_str());

  // Segundo dispositivo: el interruptor de "ayuda". Cuando Alexa lo activa, el ESP32
  // publica la alerta una vez, apaga el interruptor y no vuelve a publicar durante
  // 60 s aunque Alexa lo deje activado.
  id_dispositivo_ayuda = fauxmo.addDevice(NOMBRE_AYUDA);
  String unico_ayuda = identificador_mac_nodo() + "a";
  fauxmo.setDeviceUniqueId(id_dispositivo_ayuda, unico_ayuda.c_str());

  fauxmo.onSetState([](unsigned char id, const char *nombre, bool estado, unsigned char valor) {
    // Corre dentro de la tarea de fauxmoESP: no se hace nada pesado aqui, solo se
    // dejan las peticiones en el buffer y se sale. Todo lo resuelve alexa_tarea(),
    // en el loop.
    if (semaforo_orden != nullptr) {
      xSemaphoreTake(semaforo_orden, portMAX_DELAY);
    }
    if (strcmp(nombre, NOMBRE_LUZ) == 0) {
      hay_orden = true;
      orden_encender = estado;
    } else if (strcmp(nombre, NOMBRE_AYUDA) == 0 && estado) {
      hay_peticion_ayuda = true;
    }
    if (semaforo_orden != nullptr) {
      xSemaphoreGive(semaforo_orden);
    }
    log_info(ETIQUETA, "Alexa movio '%s' a %s (id=%u valor=%u)", nombre, estado ? "on" : "off",
             id, valor);
  });

  semaforo_orden = xSemaphoreCreateMutex();

  // fauxmoESP exige enable(true) DESPUES de tener WiFi: enable() es lo que levanta
  // el servidor TCP y abre el socket multicast de SSDP.
  habilitado = true;
  log_info(ETIQUETA, "preparado; se habilita en cuanto haya red (puerto 80). "
                     "Dispositivos: '%s' (luz) y '%s' (ayuda)",
           NOMBRE_LUZ, NOMBRE_AYUDA);
}

void alexa_tarea() {
  // La orden se aplica antes de mirar la red: es local, y el rele no depende del
  // broker para nada.
  if (semaforo_orden != nullptr) {
    xSemaphoreTake(semaforo_orden, portMAX_DELAY);
  }
  const bool hay = hay_orden;
  const bool encender = orden_encender;
  const bool hay_ayuda = hay_peticion_ayuda;
  hay_orden = false;
  hay_peticion_ayuda = false;
  if (semaforo_orden != nullptr) {
    xSemaphoreGive(semaforo_orden);
  }
  if (hay) {
    luz_aplicar_orden(encender, LUZ_ORIGEN_ALEXA);
  }

  if (hay_ayuda) {
    const uint32_t ahora = millis();
    if (ultima_ayuda_ms != 0 &&
        (int32_t)(ahora - ultima_ayuda_ms) < (int32_t)(AYUDA_ANTIRREBOTE_S * 1000UL)) {
      log_info(ETIQUETA, "ayuda ignorada: sigue dentro de los %d s de antirrebote",
               AYUDA_ANTIRREBOTE_S);
    } else {
      ultima_ayuda_ms = ahora;
      // La alerta va por la cola, igual que la de gas: sale solo con PUBACK.
      alerta_ayuda_registrar(LUZ_ORIGEN_ALEXA);
      // Se apaga el interruptor para que Alexa lo vea como ya atendido. No hace
      // falta bandera de supresion: setState() solo actualiza el estado interno del
      // dispositivo y NO vuelve a invocar el callback (verificado en fauxmoESP.cpp).
      fauxmo.setState(id_dispositivo_ayuda, false, 0);
      log_info(ETIQUETA, "alerta de ayuda encolada e interruptor apagado");
    }
  }

  if (!habilitado) {
    return;
  }
  if (!red_conectado()) {
    return;
  }
  // Primera vez que hay red: se habilita fauxmoESP. La libreria guarda su
  // _enabled en privado y no lo expone, asi que el estado se lleva aqui.
  if (!habilitado_en_fauxmo) {
    fauxmo.enable(true);
    habilitado_en_fauxmo = true;
    log_info(ETIQUETA, "fauxmoESP habilitado, anunciando por SSDP");
  }
  // fauxmoESP usa TCP asincrono pero UDP sincrono, asi que el loop tiene que
  // pasar por handle() para atender los paquetes SSDP. Sin esta llamada no hay
  // descubrimiento.
  fauxmo.handle();
}
