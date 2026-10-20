#include "sensores.h"

// config.h va ANTES de la comprobacion de DHT22_HABILITADO: la bandera se define
// alli, y con el include despues el #if se evaluaba con la macro sin definir (0) y
// <DHT.h> no se incluia, asi que DHT no era un tipo conocido.
#include "config.h"

#if DHT22_HABILITADO
#include <DHT.h>
#endif

#include "mqtt_gestor.h"
#include "registro.h"

namespace {

int gas_prueba = -1;

#if DHT22_HABILITADO
// El DHT22 se lee como mucho una vez cada DHT22_MS_ENTRE_LECTURAS; tres lecturas
// malas seguidas y el campo sale null en vez de un numero inventado.
DHT dht_instancia(GPIO_PIN_DHT22, DHT22);
uint32_t proxima_lectura_dht_ms = 0;
int dht_fallidas_seguidas = 0;
bool hay_lectura_dht = false;
float dht_temp_c = 0.0f;
int dht_hum_pct = 0;
#endif

#if MQ2_HABILITADO
// Calentamiento del MQ-2 (MQ2_MS_CALIENTE). Mientras dura, gas_adc es null y la
// maquina de episodios no mira umbrales.
uint32_t mq2_listo_en_ms = 0;
bool mq2_log_listo = false;

// True cuando el MQ-2 real puede dar gas_adc. El calentamiento empieza en
// sensores_iniciar(); la rama de arranque aqui es por si sensores_leer se llamara
// antes. El log del fin del calentamiento se escribe una sola vez.
bool gas_lectura_real_disponible(uint32_t ahora) {
  if (mq2_listo_en_ms == 0) {
    mq2_listo_en_ms = ahora + MQ2_MS_CALIENTE;
    return false;
  }
  if ((int32_t)(ahora - mq2_listo_en_ms) < 0) {
    return false;
  }
  if (!mq2_log_listo) {
    mq2_log_listo = true;
    log_info("sensores", "MQ-2 listo tras %lu s de calentamiento",
             static_cast<unsigned long>(MQ2_MS_CALIENTE / 1000UL));
  }
  return true;
}
#else
// Con MQ2_HABILITADO=false no hay lectura real nunca: el campo sale null, salvo el
// valor forzado por nodo/prueba_gas, que no pasa por aqui.
bool gas_lectura_real_disponible(uint32_t) {
  return false;
}
#endif

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
  // El pin del MQ-2 es ADC1 y el plan exige atenuacion de 11 dB. Se configura
  // aqui, no en el arranque de la placa: analogSetPinAttenuation(pin, ADC_11db)
  // es el que fija el rango 0..4095 que aceptan el orquestador y nodo_simulado.
  analogSetPinAttenuation(GPIO_PIN_GAS_ADC, ADC_11db);

#if DHT22_HABILITADO
  dht_instancia.begin();
  proxima_lectura_dht_ms = 0;
  dht_fallidas_seguidas = 0;
  hay_lectura_dht = false;
#endif

  // El pin del PIR se deja declarado para que el cableado sea explicito.
  pinMode(GPIO_PIN_SENSOR_PIR, INPUT);

#if MQ2_HABILITADO
  // El calentamiento arranca con millis(), no con la primera lectura: asi no se
  // alarga si el MQTT tarda en conectar.
  mq2_listo_en_ms = millis() + MQ2_MS_CALIENTE;
  mq2_log_listo = false;
  log_info("sensores", "MQ-2 calentando (%lu s): gas_adc sale null hasta que este listo",
           static_cast<unsigned long>(MQ2_MS_CALIENTE / 1000UL));
#endif

  proxima_publicacion_minima_ms = 0;
  proximo_latido_ms = 0;
  hay_publicado = false;
#if PIR_HABILITADO
  pir_listo_en_ms = 0;
  pir_log_listo = false;
  pir_alto_enclavado = false;
  pir_alto_actual = false;
  pir_forzado = -1;
#endif
#if DHT22_HABILITADO && MQ2_HABILITADO && PIR_HABILITADO
  log_info("sensores", "DHT22 real en GPIO %d (cada %lu s, %d fallos y sale null), "
           "MQ-2 real en GPIO 34 con atenuacion de 11 dB (calentamiento de %lu s), "
           "PIR real en GPIO %d (calentamiento de %lu s)",
           GPIO_PIN_DHT22, static_cast<unsigned long>(DHT22_MS_ENTRE_LECTURAS / 1000UL),
           DHT22_FALLIDAS_MAX, static_cast<unsigned long>(MQ2_MS_CALIENTE / 1000UL),
           GPIO_PIN_SENSOR_PIR, static_cast<unsigned long>(PIR_CALIENTE_MS / 1000UL));
#else
  log_info("sensores", "DHT22 habilitado=%d (GPIO %d), MQ-2 habilitado=%d (GPIO 34), "
           "PIR habilitado=%d (GPIO %d). Con una bandera en false el campo se "
           "publica null; la simulacion de presencia sigue en %d s",
           DHT22_HABILITADO, GPIO_PIN_DHT22, MQ2_HABILITADO, PIR_HABILITADO,
           GPIO_PIN_SENSOR_PIR, SIM_PRESENCIA_PERIODO_S);
#endif
#if !PIR_HABILITADO
  proximo_cambio_presencia_ms = 0;
  log_info("sensores", "presencia simulada: alterna cada %d s", SIM_PRESENCIA_PERIODO_S);
#endif
}

Lectura sensores_leer() {
  const uint32_t ahora = millis();
#if !PIR_HABILITADO
  actualizar_presencia(ahora);
#endif

  Lectura lectura;

  // --- Gas. Lo primero es el valor forzado: manda sobre la bandera y sobre el
  // calentamiento, porque es la via con la que se prueba la alarma sin sensor.
  if (gas_prueba >= 0) {
    lectura.hay_gas = true;
    lectura.gas_adc = gas_prueba;
  } else if (gas_lectura_real_disponible(ahora)) {
    // ADC de 12 bits (0..4095) en el rango de la atenuacion de 11 dB fijada en
    // sensores_iniciar. Con el sensor sin cablear la lectura es ruido, y por eso
    // MQ2_HABILITADO se pone en false en vez de true: un valor ruidoso abre
    // episodios falsos.
    lectura.hay_gas = true;
    lectura.gas_adc = analogRead(GPIO_PIN_GAS_ADC);
  }

  // --- Temperatura y humedad -------------------------------------------------
#if DHT22_HABILITADO
  // El DHT22 responde en el pin y tarda unos milisegundos. Se cachea la ultima
  // lectura buena y no se vuelve a tocar el sensor hasta DHT22_MS_ENTRE_LECTURAS.
  if (proxima_lectura_dht_ms == 0 || (int32_t)(ahora - proxima_lectura_dht_ms) >= 0) {
    proxima_lectura_dht_ms = ahora + DHT22_MS_ENTRE_LECTURAS;
    const float t = dht_instancia.readTemperature();
    const float h = dht_instancia.readHumidity();
    if (isnan(t) || isnan(h)) {
      ++dht_fallidas_seguidas;
      if (dht_fallidas_seguidas >= DHT22_FALLIDAS_MAX) {
        dht_fallidas_seguidas = DHT22_FALLIDAS_MAX;
        if (hay_lectura_dht) {
          log_aviso("sensores", "DHT22 sin lectura tras %d intentos seguidos: temp y "
                    "hum saldran null hasta recuperar", DHT22_FALLIDAS_MAX);
          hay_lectura_dht = false;
        }
      }
    } else {
      if (!hay_lectura_dht || dht_fallidas_seguidas > 0) {
        log_info("sensores", "DHT22 listo: temp=%.1f hum=%d", t,
                 static_cast<int>(h + 0.5f));
      }
      dht_fallidas_seguidas = 0;
      hay_lectura_dht = true;
      dht_temp_c = t;
      dht_hum_pct = static_cast<int>(h + 0.5f);
    }
  }
  if (hay_lectura_dht) {
    lectura.hay_temp = true;
    lectura.hay_hum = true;
    lectura.temp_c = dht_temp_c;
    lectura.hum_pct = dht_hum_pct;
  }
#endif

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
//
// Un campo que pasa de tener dato a null (o al revés) cuenta como cambio: si no, un
// sensor que se desconecta dejaria el panel enseñando el ultimo valor como si
// siguiera valido.
bool hay_cambio_relevante(const Lectura &lectura) {
  if (!hay_publicado) {
    return true;
  }
  if (lectura.presencia != publicado.presencia) {
    return true;
  }
  if (lectura.hay_temp != publicado.hay_temp) {
    return true;
  }
  if (lectura.hay_hum != publicado.hay_hum) {
    return true;
  }
  if (lectura.hay_gas != publicado.hay_gas) {
    return true;
  }
  if (lectura.hay_temp && fabsf(lectura.temp_c - publicado.temp_c) >= UMBRAL_TEMP_C) {
    return true;
  }
  if (lectura.hay_hum && fabsf(lectura.hum_pct - publicado.hum_pct) >= UMBRAL_HUM_PCT) {
    return true;
  }
  if (lectura.hay_gas && abs(lectura.gas_adc - publicado.gas_adc) >= UMBRAL_GAS_ADC) {
    return true;
  }
  return false;
}

// Un campo sin dato va como null explicito en el JSON, no como 0 ni como una
// cadena vacia: el panel y el orquestador lo leen con .get() y el null es el unico
// valor que distingue «todavia no lo se» de «esta a cero».
String construir_payload(const Lectura &lectura) {
  String carga = "{";
  carga += "\"" JSON_CLAVE_TEMP "\":";
  carga += lectura.hay_temp ? String(lectura.temp_c, 1) : String("null");
  carga += ",\"" JSON_CLAVE_HUM "\":";
  carga += lectura.hay_hum ? String(lectura.hum_pct) : String("null");
  carga += ",\"" JSON_CLAVE_GAS "\":";
  carga += lectura.hay_gas ? String(lectura.gas_adc) : String("null");
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

  // El limite de una publicacion cada 10 s manda sobre todo lo demas. Con el MQ-2
  // real un cambio grande seguido de otro no puede llenar el topic.
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
