#include "luz.h"

#include "config.h"
#include "mqtt_gestor.h"
#include "registro.h"

namespace {
constexpr char ETIQUETA[] = "luz";

bool encendida = false;
bool estado_conocido = false;
uint32_t ultimo_cambio_ms = 0;

// Origen del ULTIMO cambio. Se guarda porque el retained se puede tener que
// reintentar mas tarde, y hay que reintentarlo con el mismo origen.
const char *origen_actual = LUZ_ORIGEN_PANEL;

// El cambio se aplico pero no se pudo publicar (no habia broker). Mientras este
// indicador siga en true, luz_tarea() reintenta el retained en cuanto haya sesion,
// para que el broker no quede con el valor viejo.
bool publicacion_pendiente = false;

// Unico punto donde se traduce encendido/apagado al nivel del pin del rele. Si el
// modulo cambiara de polaridad, basta con cambiar RELE_ACTIVO_BAJO en config.h y no
// hay que tocar ninguna otra linea. Con un modulo activo en bajo, el nivel de
// APAGADO es ALTO y el de encendido BAJO.
void rele_escribir(bool encendido) {
#if RELE_HABILITADO
  const bool nivel = RELE_ACTIVO_BAJO ? !encendido : encendido;
  digitalWrite(GPIO_PIN_RELE, nivel ? HIGH : LOW);
#else
  (void)encendido;
#endif
}

// El rele y el LED leen del MISMO estado (2.5). Por eso quien escribe el estado
// llama a esta funcion y no a cada pin por su cuenta.
void escribir_salidas() {
  led_escribir(encendida);
  rele_escribir(encendida);
}

// Pulsacion larga del boton BOOT para reiniciar (4 s). El umbral alto es para no
// reiniciar sin querer al tocarlo mientras se enciende o apaga la luz.
constexpr uint32_t MS_PULSON_REINICIO = 4000UL;

// Estado de un boton con antirrebote por tiempo y por lecturas estables (2.5).
// GPIO es pull-up, asi que pulsado es LOW.
struct Boton {
  int pin = -1;
  bool disponible = false;
  bool nivel_anterior = HIGH;
  bool nivel_estable = HIGH;
  uint8_t lecturas_estables = 0;
  bool hay_pulsacion = false;
  uint32_t inicio_pulsacion_ms = 0;
  bool ya_reportado = false;
};

Boton boton_boot;
Boton boton_externo;

// Devuelve true en el flanco de PULSADO (no mientras se mantiene pulsado), para que
// una pulsacion larga sea un solo toggle y no un tren de toggles.
bool boton_detectar_pulsacion(Boton &boton, uint32_t ahora) {
  if (!boton.disponible) {
    return false;
  }

  const bool nivel = digitalRead(boton.pin) == LOW ? LOW : HIGH;

  // Se exige el mismo nivel en BOTON_ESTABLE_LECTURAS lecturas seguidas antes de
  // darlo por bueno.
  if (nivel != boton.nivel_anterior) {
    boton.nivel_anterior = nivel;
    boton.lecturas_estables = 1;
    return false;
  }
  if (boton.lecturas_estables < BOTON_ESTABLE_LECTURAS) {
    ++boton.lecturas_estables;
    return false;
  }
  if (nivel == boton.nivel_estable) {
    return false;
  }
  boton.nivel_estable = nivel;

  if (nivel == LOW) {
    boton.inicio_pulsacion_ms = ahora;
    boton.ya_reportado = false;
    boton.hay_pulsacion = true;
    return false;
  }

  // Flanco de subida: termina la pulsacion.
  const uint32_t duracion = ahora - boton.inicio_pulsacion_ms;
  boton.hay_pulsacion = false;
  return duracion >= BOTON_PULSO_MIN_MS;
}

void boton_preparar(Boton &boton, int pin) {
  boton.pin = pin;
  boton.nivel_anterior = HIGH;
  boton.nivel_estable = HIGH;
  boton.lecturas_estables = BOTON_ESTABLE_LECTURAS;
  boton.hay_pulsacion = false;
  boton.inicio_pulsacion_ms = 0;
}

}  // namespace

void led_escribir(bool encendido) {
  const bool nivel = LED_ACTIVO_EN_NIVEL_BAJO ? !encendido : encendido;
  digitalWrite(GPIO_PIN_LED, nivel ? HIGH : LOW);
}

void luz_iniciar() {
  pinMode(GPIO_PIN_LED, OUTPUT);
  boton_preparar(boton_boot, GPIO_PIN_BOTON_BOOT);
  // DIAG-2026-10-08: el boton BOOT quedaba rellenado pero deshabilitado
  // (disponible=false), asi que GPIO 0 nunca se leia. Se habilita con pull-up
  // interno y la logica de antirrebote/4 s de botones_tarea().
  pinMode(GPIO_PIN_BOTON_BOOT, INPUT_PULLUP);
  boton_boot.disponible = true;

  // GPIO 26 y 27 quedan cableados. El rele ya es real; el buzzer todavia no (6.7).
#if RELE_HABILITADO
  // El nivel de APAGADO se escribe ANTES de pinMode(OUTPUT). Al reves (pinMode y
  // despues el nivel) el pin sale un instante en bajo, y con un modulo activo en
  // bajo (RELE_ACTIVO_BAJO 1) ese instante es un clic y el LED del rele encendido
  // al arrancar. Pre-cargando el latch y habilitando la salida despues, el pin ya
  // sale con el nivel de apagado y no hay parpadeo.
  digitalWrite(GPIO_PIN_RELE, RELE_ACTIVO_BAJO ? HIGH : LOW);
  pinMode(GPIO_PIN_RELE, OUTPUT);
#endif
  pinMode(GPIO_PIN_BUZZER, OUTPUT);
  digitalWrite(GPIO_PIN_BUZZER, LOW);

#if BOTON_EXTERNO_HABILITADO
  boton_preparar(boton_externo, GPIO_PIN_BOTON_EXTERNO);
  boton_externo.disponible = true;
  log_info(ETIQUETA, "boton externo habilitado en GPIO %d", GPIO_PIN_BOTON_EXTERNO);
#else
  log_info(ETIQUETA, "boton externo cableado en GPIO %d pero DESHABILITADO hasta que llegue el "
                     "hardware",
           GPIO_PIN_BOTON_EXTERNO);
#endif

  escribir_salidas();

  // Arranque en frio (2026-10-19): la luz nace apagada por el propio firmware,
  // sin que ninguna orden la haya cambiado, y ese ES su origen. Antes el estado
  // se quedaba «desconocido» y luz_estado_json() le atribuia «boton», o sea un
  // actor que no hizo nada. Con esto el primer luz/estado retenido que publica
  // mqtt_al_conectar() ya trae {"estado":"off","origen":"arranque"}.
  estado_conocido = true;
  encendida = false;
  origen_actual = LUZ_ORIGEN_ARRANQUE;
  ultimo_cambio_ms = 0;
  publicacion_pendiente = false;

  log_info(ETIQUETA, "luz nace apagada (origen %s); boton BOOT (GPIO %d) habilitado: "
           "corto alterna la luz, %lu ms reinicia",
           LUZ_ORIGEN_ARRANQUE, GPIO_PIN_BOTON_BOOT,
           static_cast<unsigned long>(MS_PULSON_REINICIO));
}

String luz_estado_json() {
  // Estado de la luz con origen valido del contrato. Siempre hay estado conocido,
  // porque luz_iniciar() ya deja la luz apagada con origen «arranque»; la rama sin
  // conocer se queda solo como red de seguridad y usa «boton», el unico de los
  // origenes que no atribuye el estado a un actor remoto inexistente.
  const bool estado = estado_conocido ? encendida : false;
  const char *origen = estado_conocido ? origen_actual : LUZ_ORIGEN_BOTON;
  return String("{\"" JSON_CLAVE_ESTADO "\":\"") + (estado ? LUZ_ESTADO_ON : LUZ_ESTADO_OFF) +
         "\",\"" JSON_CLAVE_ORIGEN_VOZ "\":\"" + origen + "\"}";
}

void luz_aplicar_orden(bool es_encendido, const char *origen) {
  if (origen == nullptr) {
    origen = LUZ_ORIGEN_PANEL;
  }

  if (estado_conocido && encendida == es_encendido) {
    // Sin novelty no se publica nada: un retained que se reescribe con el mismo
    // valor solo genera trafico.
    log_info(ETIQUETA, "orden sin cambio, sigue %s (origen %s)",
             es_encendido ? "encendida" : "apagada", origen);
    return;
  }

  encendida = es_encendido;
  estado_conocido = true;
  origen_actual = origen;
  ultimo_cambio_ms = millis();
  escribir_salidas();

  // El ESP32 publica el estado en el MISMO ciclo en que lo aplica, no en el
  // siguiente latido (2.5).
  const String payload = String("{\"" JSON_CLAVE_ESTADO "\":\"") +
                         (encendida ? LUZ_ESTADO_ON : LUZ_ESTADO_OFF) + "\",\"" JSON_CLAVE_ORIGEN_VOZ
                         "\":\"" + origen + "\"}";
  publicacion_pendiente =
      !mqtt_publicar(String(TOPIC_LUZ_ESTADO), payload, QOS_LUZ_ESTADO, true);

  log_info(ETIQUETA, "luz %s por %s -> %s %s", encendida ? "ENCENDIDA" : "APAGADA", origen,
           TOPIC_LUZ_ESTADO,
           publicacion_pendiente ? "(sin broker: se reintentara al reconectar)" : "publicado");
}

bool luz_encendida() {
  return encendida;
}

bool luz_tiene_estado_conocido() {
  return estado_conocido;
}

uint32_t luz_ultimo_cambio_ms() {
  return ultimo_cambio_ms;
}

void luz_tarea() {
  // luz/estado NO tiene latido: se escribe al aplicar el cambio, en
  // luz_aplicar_orden(). Aqui solo se recupera el unico caso que se pierde: que el
  // cambio ocurriera sin broker, con lo cual el retained del broker se queda con
  // el valor anterior.
  if (!publicacion_pendiente || !estado_conocido || !mqtt_conectado()) {
    return;
  }

  const String payload = String("{\"" JSON_CLAVE_ESTADO "\":\"") +
                         (encendida ? LUZ_ESTADO_ON : LUZ_ESTADO_OFF) + "\",\"" JSON_CLAVE_ORIGEN_VOZ
                         "\":\"" + origen_actual + "\"}";
  if (mqtt_publicar(String(TOPIC_LUZ_ESTADO), payload, QOS_LUZ_ESTADO, true)) {
    publicacion_pendiente = false;
    log_info(ETIQUETA, "retained de %s recuperado tras reconectar", TOPIC_LUZ_ESTADO);
  }
}

void botones_tarea() {
  const uint32_t ahora = millis();

  // BOOT: pulsacion larga reinicia, pulsacion normal enciende o apaga.
  if (boton_detectar_pulsacion(boton_boot, ahora)) {
    const uint32_t duracion = ahora - boton_boot.inicio_pulsacion_ms;
    if (duracion >= MS_PULSON_REINICIO) {
      log_aviso(ETIQUETA, "reiniciando por pulsacion larga del boton BOOT");
      delay(50);
      ESP.restart();
      return;
    }
    log_info(ETIQUETA, "boton BOOT: %lu ms, alterna la luz",
             static_cast<unsigned long>(duracion));
    luz_aplicar_orden(!estado_conocido || !encendida, LUZ_ORIGEN_BOTON);
  }

#if BOTON_EXTERNO_HABILITADO
  if (boton_detectar_pulsacion(boton_externo, ahora)) {
    log_info(ETIQUETA, "boton externo: alterna la luz");
    luz_aplicar_orden(!estado_conocido || !encendida, LUZ_ORIGEN_BOTON);
  }
#endif
}
