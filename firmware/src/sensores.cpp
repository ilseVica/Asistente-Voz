#include "sensores.h"

#include <WiFi.h>

#include "config.h"
#include "mqtt_gestor.h"
#include "registro.h"

namespace {

uint32_t semilla = 0;
int gas_prueba = -1;

// --- Presencia -------------------------------------------------------------
// Con PIR_HABILITADO la presencia sale del HC-SR501 en GPIO 35. Con la bandera
// en false sigue la simulacion de siempre: alterna sola cada
// SIM_PRESENCIA_PERIODO_S, independiente de prueba_gas.
#if PIR_HABILITADO
// Calentamiento: los primeros PIR_CALIENTE_MS la salida del sensor no es
// confiable, y un arranque con la presencia en true produciria una alerta de
// inactividad falsa apenas el orquestador estuviera listo.
uint32_t pir_listo_en_ms = 0;
bool pir_log_listo = false;

// Enclavamiento. La regla es: si el PIR estuvo en alto en CUALQUIER momento
// desde la ultima publicacion, la presencia publicada es true. Un pulso de
// movimiento mas corto que el limite de 10 s se pierde si se lee solo al
// publicar, y un movimiento corto no es un movimiento inexistente: por eso el
// pin se muestrea en cada vuelta del loop y el enclavamiento se limpia solo
// despues de un publish exitoso.
bool pir_alto_enclavado = false;
bool pir_alto_actual = false;

// Forzado por el comando de prueba PIR <0|1> (solo con PRUEBAS=1). -1 = pin real.
int pir_forzado = -1;
#else
bool presencia_alterna = false;
uint32_t proximo_cambio_presencia_ms = 0;
#endif

// Marca para que el log avise del cambio de presencia una sola vez, no cada lectura.
bool presencia_cambio_pendiente = false;

// Estado de la ultima lectura PUBLISHADA. La regla de frecuencia (2.7) compara
// contra esto, no contra la lectura anterior: si no, bastaria con que la lectura
// oscillara entre dos valores dentro del margen para no publicar nunca.
bool hay_publicado = false;
Lectura publicado;

// Tiempos de la regla de frecuencia.
uint32_t proxima_publicacion_minima_ms = 0;
uint32_t proximo_latido_ms = 0;

// Umbrales de la regla de frecuencia de sensores (2.7).
constexpr float UMBRAL_TEMP_C = 0.3f;
constexpr float UMBRAL_HUM_PCT = 2.0f;
constexpr int UMBRAL_GAS_ADC = 50;

#if PIR_HABILITADO

// Muestrea el PIR y mantiene el enclavamiento. Se llama en CADA vuelta del loop,
// ANTES del limite de 10 s de publicacion: si se leyera solo al publicar, un
// pulso de movimiento mas corto que ese limite se perderia.
void muestrear_pir(uint32_t ahora) {
  if (pir_listo_en_ms == 0) {
    pir_listo_en_ms = ahora + PIR_CALIENTE_MS;
    log_info("sensores", "PIR calentando");
    return;
  }
  if ((int32_t)(ahora - pir_listo_en_ms) < 0) {
    return;  // calentando: la presencia se queda en false hasta «PIR listo»
  }
  if (!pir_log_listo) {
    pir_log_listo = true;
    log_info("sensores", "PIR listo");
  }
  const bool alto =
      (pir_forzado >= 0) ? (pir_forzado != 0) : (digitalRead(GPIO_PIN_SENSOR_PIR) == HIGH);
  if (alto && !pir_alto_enclavado) {
    log_info("sensores", "PIR: movimiento");
  }
  pir_alto_actual = alto;
  if (alto) {
    pir_alto_enclavado = true;
  }
}

// La presencia que se publica. False durante el calentamiento; despues, true si
// el PIR esta en alto ahora o lo estuvo desde la ultima publicacion.
bool presencia_del_pir(uint32_t ahora) {
  if (pir_listo_en_ms == 0 || (int32_t)(ahora - pir_listo_en_ms) < 0) {
    return false;
  }
  return pir_alto_enclavado || pir_alto_actual;
}

#else  // !PIR_HABILITADO: la simulacion de siempre

void actualizar_presencia(uint32_t ahora) {
  if (proximo_cambio_presencia_ms == 0) {
    proximo_cambio_presencia_ms = ahora + (SIM_PRESENCIA_PERIODO_S * 1000UL);
    return;
  }
  if ((int32_t)(ahora - proximo_cambio_presencia_ms) < 0) {
    return;
  }
  presencia_alterna = !presencia_alterna;
  proximo_cambio_presencia_ms = ahora + (SIM_PRESENCIA_PERIODO_S * 1000UL);
  presencia_cambio_pendiente = true;
}

#endif

}  // namespace

void sensores_iniciar() {
  // Semilla derivada de la MAC, no de millis() ni de micros(): dos ESP32 del mismo
  // lote tienen que comportarse distinto (2.4). Con micros() dos placas que
  // arrancaran en el mismo instante darían exactamente la misma serie.
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  semilla = 0;
  for (uint8_t i = 0; i < 6; ++i) {
    semilla = semilla * 131UL + mac[i];
  }
  semilla = semilla * 1103515245UL + 12345UL;
  log_depurar("sensores", "semilla de simulacion derivada de la MAC");

  // El pin del MQ-2 es ADC1 y el plan exige atenuacion de 11 dB. Se configura
  // aunque hoy no haya sensor fisico, para que el cableado este listo.
  analogSetPinAttenuation(GPIO_PIN_GAS_ADC, ADC_11db);

  // El DHT22 y el PIR quedan declarados para que el pin sea explicito. No se
  // inicializa ninguna libreria porque no hay componente.
  pinMode(GPIO_PIN_DHT22, INPUT);
  pinMode(GPIO_PIN_SENSOR_PIR, INPUT);

  proxima_publicacion_minima_ms = 0;
  proximo_latido_ms = 0;
  hay_publicado = false;
#if PIR_HABILITADO
  pir_listo_en_ms = 0;
  pir_log_listo = false;
  pir_alto_enclavado = false;
  pir_alto_actual = false;
  pir_forzado = -1;
  log_info("sensores", "presencia por PIR real en GPIO %d (calentamiento de %lu s), "
           "gas simulado fijo %d",
           GPIO_PIN_SENSOR_PIR,
           static_cast<unsigned long>(PIR_CALIENTE_MS / 1000UL), SIM_GAS_POR_DEFECTO);
#else
  proximo_cambio_presencia_ms = 0;
  log_info("sensores", "simulacion activa (gas fijo %d, presencia cada %d s)", SIM_GAS_POR_DEFECTO,
           SIM_PRESENCIA_PERIODO_S);
#endif
}

int sensores_aleatorio_entre(int minimo, int maximo) {
  if (maximo <= minimo) {
    return minimo;
  }
  semilla = semilla * 1103515245UL + 12345UL;
  const uint32_t rango = static_cast<uint32_t>(maximo - minimo) + 1UL;
  return minimo + static_cast<int>((semilla >> 8) % rango);
}

Lectura sensores_leer() {
  const uint32_t ahora = millis();
#if !PIR_HABILITADO
  actualizar_presencia(ahora);
#endif

  Lectura lectura;

  // El gas simulado es una CONSTANTE en rango de aire limpio. Ver la nota larga de
  // config.h: ni por debajo del umbral (alertas falsas) ni oscilando dentro del
  // rango (topic lleno de sensores). El unico camino para moverlo es
  // nodo/prueba_gas.
  lectura.gas_adc = (gas_prueba >= 0) ? gas_prueba : SIM_GAS_POR_DEFECTO;

  // Temperatura y humedad: variaciones lentas y concentricas, no ruido.
  lectura.temp_c = SIM_TEMP_BASE_C +
                   (sensores_aleatorio_entre(0, 100) - 50) * (SIM_TEMP_VARIACION_C / 50.0);
  lectura.hum_pct =
      SIM_HUM_BASE_PCT + sensores_aleatorio_entre(0, 100) * (SIM_HUM_VARIACION_PCT / 100.0);

#if PIR_HABILITADO
  // La presencia sale del PIR enclavado; el muestreo del pin lo hace
  // sensores_tarea en cada vuelta del loop, no aqui.
  lectura.presencia = presencia_del_pir(ahora);
#else
  // La presencia alterna sola cada 3 minutos, independiente de prueba_gas.
  lectura.presencia = presencia_alterna;
#endif

  if (presencia_cambio_pendiente) {
    presencia_cambio_pendiente = false;
    log_info("sensores", "presencia ahora %s", lectura.presencia ? "true" : "false");
  }

  return lectura;
}

void sensores_fijar_gas_prueba(int valor_adc) {
  gas_prueba = valor_adc;
}

int sensores_gas_prueba_actual() {
  return gas_prueba;
}

void sensores_fijar_pir_prueba(int valor) {
#if PIR_HABILITADO
  // 0 o 1 fuerza la lectura como si el pin estuviera en ese nivel; -1 restaura
  // el pin real. El enclavamiento no se toca aqui: el valor forzado se muestrea
  // igual que el pin, y un 1 forzado enclava presencia true como cualquier
  // movimiento.
  pir_forzado = valor;
#else
  (void)valor;
#endif
}

int sensores_pir_prueba_actual() {
#if PIR_HABILITADO
  return pir_forzado;
#else
  return -1;
#endif
}

namespace {

// Por que se publica. La regla de frecuencia (2.7) tiene tres disparadores:
// un cambio que pase los margenes, el limite de una publicacion cada 10 s, y el
// latido completo cada 60 s aunque no haya cambiado nada.
bool hay_cambio_relevante(const Lectura &lectura) {
  if (!hay_publicado) {
    return true;
  }
  if (lectura.presencia != publicado.presencia) {
    return true;
  }
  if (fabsf(lectura.temp_c - publicado.temp_c) >= UMBRAL_TEMP_C) {
    return true;
  }
  if (fabsf(lectura.hum_pct - publicado.hum_pct) >= UMBRAL_HUM_PCT) {
    return true;
  }
  return abs(lectura.gas_adc - publicado.gas_adc) >= UMBRAL_GAS_ADC;
}

String construir_payload(const Lectura &lectura) {
  String carga = "{";
  carga += "\"" JSON_CLAVE_TEMP "\":" + String(lectura.temp_c, 1);
  carga += ",\"" JSON_CLAVE_HUM "\":" + String(lectura.hum_pct);
  carga += ",\"" JSON_CLAVE_GAS "\":" + String(lectura.gas_adc);
  carga += ",\"" JSON_CLAVE_PRESENCIA "\":" + String(lectura.presencia ? "true" : "false");
  carga += "}";
  return carga;
}

}  // namespace

void sensores_tarea() {
  const uint32_t ahora = millis();

#if PIR_HABILITADO
  // El PIR se muestrea en cada vuelta del loop, ANTES del limite de 10 s y antes
  // de mirar la sesion: un pulso de movimiento no se pierde aunque la publicacion
  // no toque aun, y con el enclavado acumulado el primer publish al reconectar ya
  // refleja si hubo movimiento mientras no habia sesion.
  muestrear_pir(ahora);
#endif

  if (!mqtt_conectado()) {
    // Sin sesion no hay retained que valga. Se sueltan los relojes para que al
    // reconectar se publique de inmediato en vez de esperar al margen.
    proxima_publicacion_minima_ms = 0;
    proximo_latido_ms = 0;
    return;
  }

  // El limite de una publicacion cada 10 s manda sobre todo lo demas. Con el gas
  // simulado constante nunca se llega aqui, pero con el MQ-2 real un cambio
  // grande seguido de otro no puede llenar el topic.
  if (proxima_publicacion_minima_ms != 0 && (int32_t)(ahora - proxima_publicacion_minima_ms) < 0) {
    return;
  }

  const Lectura lectura = sensores_leer();

  const bool toco_latido = proximo_latido_ms == 0 || (int32_t)(ahora - proximo_latido_ms) >= 0;
  if (!toco_latido && !hay_cambio_relevante(lectura)) {
    return;
  }

  const String carga = construir_payload(lectura);
  if (!mqtt_publicar(String(TOPIC_SENSORES), carga, QOS_SENSORES, false)) {
    return;  // se reintentara en el siguiente ciclo, sin haber consumido el margen
  }

  proxima_publicacion_minima_ms = ahora + PERIODO_SENSORES_MINIMO_MS;
  proximo_latido_ms = ahora + PERIODO_SENSORES_LATIDO_MS;
  publicado = lectura;
  hay_publicado = true;
#if PIR_HABILITADO
  // El enclavamiento cubre desde la publicacion ANTERIOR; con esta hecha, empieza
  // uno nuevo. Si el pin sigue en alto, el siguiente muestreo vuelve a enclavar.
  pir_alto_enclavado = false;
#endif

  char motivo[32];
  if (toco_latido && hay_publicado) {
    // Latido: no habia cambio, solo renovacion del estado vivo.
    strcpy(motivo, "latido");
  } else {
    strcpy(motivo, "cambio");
  }
  log_info("sensores", "%s: %s", motivo, carga.c_str());
}
