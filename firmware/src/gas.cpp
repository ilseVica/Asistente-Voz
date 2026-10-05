#include "gas.h"

#include "alertas.h"
#include "config.h"
#include "persistencia.h"
#include "registro.h"
#include "sensores.h"

namespace {
constexpr char ETIQUETA[] = "gas";

bool episodio_abierto = false;
int lecturas_sobre_umbral = 0;
int ultima_lectura = SIM_GAS_POR_DEFECTO;

// Cierre: cuenta los segundos seguidos por debajo del umbral de cierre.
uint32_t inicio_bajo_umbral_ms = 0;
bool hay_cierre_en_curso = false;

// Buzzer. El plan pide que suene durante TODO el episodio sin interruptirse (2.3) y
// que el patron de pitidos lo genere el firmware (2.4). El patron es un pitido de
// 120 ms cada 500 ms, actualizado a la cadencia del loop.
constexpr uint32_t MS_PITIDO_ON = 120UL;
constexpr uint32_t MS_ENTRE_PITIDOS = 500UL;

// Muestreo del MQ-2 a 1 Hz. El plan dice que el aviso sale "a los ~3 s, con 3
// lecturas seguidas" (5.9), asi que leer en cada vuelta del loop (50 ms) seria
// contar las tres lecturas en 150 ms y abriria el episodio con un solo pico.
uint32_t proxima_lectura_ms = 0;

// Cuanto lleva abierto el episodio, para el estado.
uint32_t inicio_episodio_ms = 0;

// Cuando termina el pitido en curso; 0 significa "no hay pitido en curso".
uint32_t ultimo_pitido_fin_ms = 0;
}  // namespace

void gas_iniciar() {
  ultima_lectura = SIM_GAS_POR_DEFECTO;
  proxima_lectura_ms = 0;
  ultimo_pitido_fin_ms = 0;
#if BUZZER_HABILITADO
  pinMode(GPIO_PIN_BUZZER, OUTPUT);
  digitalWrite(GPIO_PIN_BUZZER, LOW);
#endif
  log_info(ETIQUETA, "vigilando, apertura=%d cierre=%d lectura_sostenida=%d s",
           GAS_UMBRAL_APERTURA, GAS_UMBRAL_CIERRE,
           static_cast<int>(GAS_CIERRE_SOSTENIDO_S));
}

void gas_fijar_lectura_forzada(int valor_adc) {
  sensores_fijar_gas_prueba(valor_adc);
}

bool gas_episodio_abierto() {
  return episodio_abierto;
}

int gas_ultima_lectura() {
  return ultima_lectura;
}

namespace {

// El valor con retencion va dentro de nodo/estado, que lo arma nodo_estado.cpp. Aqui
// no se publica un topico propio: el plan no define uno para el gas, asi que la
// retencion del valor durante el episodio la hace el latido de nodo/estado.
void buzzer_escribir(bool sounding) {
#if BUZZER_HABILITADO
  digitalWrite(GPIO_PIN_BUZZER, sounding ? HIGH : LOW);
#else
  (void)sounding;
#endif
}

// El patron corre mientras el episodio este abierto. Se apaga en cuanto se cierra,
// aunque no toquePublication.
void buzzer_tarea(uint32_t ahora) {
#if !BUZZER_HABILITADO
  (void)ahora;
  return;
#else
  if (!episodio_abierto) {
    if (ultimo_pitido_fin_ms != 0) {
      buzzer_escribir(false);
      ultimo_pitido_fin_ms = 0;
    }
    return;
  }

  if (ultimo_pitido_fin_ms == 0) {
    buzzer_escribir(true);
    ultimo_pitido_fin_ms = ahora + MS_PITIDO_ON;
    return;
  }
  if ((int32_t)(ahora - ultimo_pitido_fin_ms) >= 0) {
    buzzer_escribir(false);
    ultimo_pitido_fin_ms = ahora + (MS_ENTRE_PITIDOS - MS_PITIDO_ON);
  }
#endif
}

void anotar_retencion() {
  log_depurar(ETIQUETA, "retencion gas=%d umbral=%d", ultima_lectura,
              persistencia_umbral_gas());
}

void abrir_episodio(int gas_adc) {
  episodio_abierto = true;
  inicio_episodio_ms = millis();
  inicio_bajo_umbral_ms = 0;
  hay_cierre_en_curso = false;
  // UN aviso por episodio. El id lo genera alertas.cpp y NO se incrementa al
  // reenviar, para que un corte de red no genere una alerta nueva en el servidor.
  alerta_gas_registrar(gas_adc);
  log_aviso(ETIQUETA, "EPISODIO ABIERTO con gas=%d (umbral %d)", gas_adc,
            persistencia_umbral_gas());
}

void cerrar_episodio(int gas_adc) {
  episodio_abierto = false;
  inicio_bajo_umbral_ms = 0;
  hay_cierre_en_curso = false;
  anotar_retencion();
  log_info(ETIQUETA, "EPISODIO CERRADO, gas=%d", gas_adc);
}

}  // namespace

void gas_tarea() {
  const uint32_t ahora = millis();

  // El buzzer va ANTES del muestreo, a la cadencia del loop (50 ms). Si fuera
  // despues del gate de 1 Hz, un pitido de 120 ms no tendria resolucion: el pin se
  // pondria HIGH y se bajaria un segundo entero despues.
  buzzer_tarea(ahora);

  if (proxima_lectura_ms != 0 && (int32_t)(ahora - proxima_lectura_ms) < 0) {
    return;
  }
  proxima_lectura_ms = ahora + PERIODO_LECTURA_GAS_MS;

  ultima_lectura = sensores_leer().gas_adc;

  // Los dos umbrales se leen de la persistencia, y el de cierre es un numero
  // propio. Antes era `umbral - GAS_HISTERESIS`, o sea 1650 en vez de los 1550 del
  // contrato, y no lo podia cambiar nodo/config: el servidor podia pedir un
  // cierre en 1600 y el nodo seguia cerrando en 1650, en silencio.
  const int umbral = persistencia_umbral_gas();
  const int umbral_cierre = persistencia_umbral_cierre_gas();

  if (!episodio_abierto) {
    // Apertura: cuenta lecturas seguidas sobre el umbral. Asi un pico suelto de una
    // sola lectura no dispara nada.
    if (ultima_lectura >= umbral) {
      ++lecturas_sobre_umbral;
      if (lecturas_sobre_umbral >= GAS_LECTURAS_PARA_ABRIR) {
        lecturas_sobre_umbral = 0;
        abrir_episodio(ultima_lectura);
        return;
      }
    } else {
      lecturas_sobre_umbral = 0;
    }

    return;
  }

  // Episodio abierto: el valor vigente se ve en el latido de sensores y en el de
  // nodo/estado, que son los dos unicos topicos donde el plan mete gas_adc. No hay
  // un topico propio de gas, asi que no se inventa uno.
  anotar_retencion();

  // Cierre: hay que estar por debajo del umbral de cierre de forma sostenida.
  if (ultima_lectura < umbral_cierre) {
    if (inicio_bajo_umbral_ms == 0) {
      inicio_bajo_umbral_ms = ahora;
      hay_cierre_en_curso = true;
    }
    if (hay_cierre_en_curso &&
        (ahora - inicio_bajo_umbral_ms) >= (GAS_CIERRE_SOSTENIDO_S * 1000UL)) {
      cerrar_episodio(ultima_lectura);
    }
    return;
  }

  // Vuelve a subir: se cancela el cierre en curso, pero NO hay alerta nueva, porque
  // el episodio sigue siendo el mismo.
  inicio_bajo_umbral_ms = 0;
  hay_cierre_en_curso = false;
}
