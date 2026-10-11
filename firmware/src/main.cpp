// Nodo ESP32 del Asistente-Voz. Punto de entrada del firmware.
//
// Estructura: setup() arranca los modulos en orden y loop() es un ciclo de tareas
// cortas sin ninguna espera. Ninguna tarea bloquea, para que el boton, el sensor y
// la red sigan respondiendo mientras el broker no esta.
//
// El loop se parte en dos bloques: las tareas LOCALES (boton, gas, Alexa, luz) que
// corren siempre, y las que necesitan sesion MQTT (SNTP, latidos, cola de alertas).
// La prueba P32 del plan pide exactamente eso: con el broker parado, el boton,
// Alexa y la alarma de gas funcionan las tres.
//
// Ver firmware/README.md para compilar y cargar, y la propuesta
// "2026-10-03 - Propuesta Firmware ESP32 - Diseño del firmware del nodo ESP32 v3.txt"
// para el detalle de cada regla del contrato.

#include <Arduino.h>
#include <esp_system.h>

#include "alertas.h"
#include "alexa.h"
#include "config.h"
#include "contrato.h"
#include "gas.h"
#include "hora.h"
#include "luz.h"
#include "mqtt_gestor.h"
#include "nodo_estado.h"
#include "persistencia.h"
#include "registro.h"
#include "sensores.h"
#include "wifi_gestor.h"

namespace {
// Cadencia del loop. Es una tareas corta, no un delay: los modulos deciden si toca
// algo comparando millis().
constexpr uint32_t MS_TAREA_LOOP = 50UL;
uint32_t ultimo_ciclo_ms = 0;

// Comando de prueba por serie. Solo existe con PRUEBAS=1: en el firmware normal ni
// se lee la linea ni hay ninguna forma de disparar gas sin broker (ajuste C).
constexpr char COMANDO_GAS[] = "GAS ";

// Motivo del ultimo reinicio (V4 del reporte de pruebas). El contador de arranques
// ya lo registra persistencia_iniciar(); esto anade el POR QUE, que es lo que
// permite distinguir un reinicio por BOOT de uno por watchdog o brownout.
const char *nombre_reset_reason(esp_reset_reason_t motivo) {
  switch (motivo) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_EXT: return "externo";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    default: return "desconocido";
  }
}

#if PRUEBAS == 1
void manejar_comandos_de_prueba() {
  while (Serial.available() > 0) {
    // Se lee la linea entera de una vez. Antes se consumia caracter a caracter
    // buscando el salto y, justo despues de consumirlo, se llamaba a
    // readStringUntil('\n'), que leia la linea SIGUIENTE: el comando se perdia.
    String linea = Serial.readStringUntil('\n');
    linea.trim();
    if (linea.startsWith(COMANDO_GAS)) {
      const int valor = linea.substring(static_cast<int>(strlen(COMANDO_GAS))).toInt();
      if (valor <= 0) {
        log_aviso("pruebas", "comando '%s' sin valor valido", linea.c_str());
        continue;
      }
      log_info("pruebas", "GAS %d por serie: alerta de gas sin broker", valor);
      gas_fijar_lectura_forzada(valor);
    } else if (linea.length() > 0) {
      log_info("pruebas", "comando '%s' desconocido", linea.c_str());
    }
  }
}
#else
// En el firmware normal el monitor serie no ejecuta comandos. Se mantiene la
// funcion vacia para que main.cpp no necesite condicionales.
void manejar_comandos_de_prueba() {}
#endif

}  // namespace

void setup() {
  Serial.begin(115200);
  const uint32_t espera_serial_ms = millis() + 500;
  while (millis() < espera_serial_ms) {
    delay(10);
  }

  registrar_iniciar(static_cast<NivelLog>(NIVEL_LOG));
  const esp_reset_reason_t motivo = esp_reset_reason();
  log_info("main", "arranque, nodo %s, firmware PRUEBAS=%d, log nivel %d", PLACA_NOMBRE, PRUEBAS,
           NIVEL_LOG);
  log_info("main", "reset_reason=%d (%s)", static_cast<int>(motivo),
           nombre_reset_reason(motivo));

  // El orden importa: primero lo que no depende de la red, despues lo que si.
  persistencia_iniciar();
  hora_iniciar();
  sensores_iniciar();
  luz_iniciar();
  alertas_iniciar();
  gas_iniciar();
  contrato_iniciar();
  nodo_estado_iniciar();
  alexa_iniciar();
  red_iniciar();

  log_info("main", "listo");
}

void loop() {
  const uint32_t ahora = millis();
  if ((int32_t)(ahora - ultimo_ciclo_ms) < (int32_t)MS_TAREA_LOOP) {
    return;
  }
  ultimo_ciclo_ms = ahora;

  manejar_comandos_de_prueba();

  // ---- Tareas locales. No dependen del broker y se ejecutan SIEMPRE. ----
  //
  // El orden importa. Esta separacion es la que hace que la prueba P32 (5.6) y la
  // P45 pass: con el broker parado o sin Wi-Fi, el boton, Alexa y la alarma de gas
  // tienen que funcionar las tres. Antes de esto, un return temprano con la red
  // caida dejaba gas_tarea() sin correr, y con el gas forzado a 3000 por el comando
  // GAS de la P45 la alerta nunca se llegaba a encolar.
  botones_tarea();
  gas_tarea();
  alexa_tarea();
  luz_tarea();
  contrato_tarea();

  red_tarea();
  if (!red_conectado()) {
    return;
  }

  // mqtt_iniciar se llama hasta que arranque de verdad. Si se marcaba el intento
  // como hecho ANTES de llamar, un fallo de resolucion (mDNS sin propagar todavia,
  // broker levantandose) dejaba al nodo sin MQTT para siempre, sin reintento: la
  // bandera ya estaba en false y mqtt_iniciar no volvia a llamarse nunca.
  //
  // Y ahora ademas con espera creciente, porque antes se reintentaba en cada vuelta
  // del loop (50 ms): con el broker todavia sin resolver eran 20 lineas de ERROR por
  // segundo, y el buffer del monitor serie se llenaba en menos de un minuto.
  //
  // Esto solo cubre el ARRANQUE. Una vez que ESP32MQTTClient arranca, la libreria
  // reconecta sola con su propio mecanismo, que no es lo que se controla desde aqui.
  if (!mqtt_conectado()) {
    static bool arranque_pendiente = true;
    static uint32_t proximo_intento_ms = 0;
    static uint32_t espera_ms = MQTT_REINTENTO_INICIAL_MS;

    if (arranque_pendiente && proximo_intento_ms != 0 &&
        (int32_t)(ahora - proximo_intento_ms) < 0) {
      return;  // todavia no toca reintentar
    }
    if (arranque_pendiente) {
      if (mqtt_iniciar()) {
        arranque_pendiente = false;
        proximo_intento_ms = 0;
        espera_ms = MQTT_REINTENTO_INICIAL_MS;  // la proxima caida arranca rapido
      } else {
        // Un solo log por intento, con la espera que viene. El motivo concreto ya
        // lo escribio mqtt_iniciar() a nivel de depuracion.
        log_info("mqtt", "no se pudo arrancar; reintento en %lu s",
                 static_cast<unsigned long>(espera_ms / 1000));
        proximo_intento_ms = ahora + espera_ms;
        if (espera_ms * 2 > MQTT_REINTENTO_MAXIMO_MS) {
          espera_ms = MQTT_REINTENTO_MAXIMO_MS;
        } else {
          espera_ms *= 2;
        }
      }
    }
  }

  // ---- A partir de aqui hace falta sesion MQTT. ----
  hora_tarea();
  mqtt_tarea();
  nodo_estado_tarea();
  sensores_tarea();
  alertas_tarea();
}
