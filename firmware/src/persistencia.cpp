#include "persistencia.h"

#include <Preferences.h>

#include "config.h"
#include "registro.h"

namespace {
constexpr char ETIQUETA[] = "nvs";
constexpr char ESPACIO[] = "nodo";
constexpr char CLAVE_ARRANQUE[] = "arranque";
constexpr char CLAVE_CONTADOR[] = "contador";
constexpr char CLAVE_UMBRAL[] = "umbral_gas";
// NVS limita las claves a 15 caracteres (NVS_KEY_NAME_MAX_SIZE) y rechaza las
// mas largas con KEY_TOO_LONG sin persistir: antes se usaba "umbral_cierre_gas"
// (17) y el write fallaba en silencio. Esta clave nunca llego a grabarse, asi
// que renombrarla no requiere migrar datos.
constexpr char CLAVE_UMBRAL_CIERRE[] = "cierre_gas";
constexpr char CLAVE_AGUA_VACIO[] = "agua_vacio";
constexpr char CLAVE_AGUA_LLENO[] = "agua_lleno";
constexpr char CLAVE_HOST[] = "host_previo";
constexpr char CLAVE_RR_CNT[] = "rr_cnt";
constexpr char CLAVE_RR_EPOCH[] = "rr_epoch";

Preferences nvs;
uint16_t arranque_actual = 0;
uint16_t contador_alerta = 0;
int umbral_gas = 0;
int umbral_cierre_gas = 0;
int agua_vacio_cm = 0;
int agua_lleno_cm = 0;
String host_previo;
uint8_t reinicios_red_cnt = 0;
uint32_t reinicios_red_epoch = 0;
}  // namespace

void persistencia_iniciar() {
  if (!nvs.begin(ESPACIO, false)) {
    log_error(ETIQUETA, "no se pudo abrir NVS, se siguen los valores de config.h");
    arranque_actual = 0;
    contador_alerta = 0;
    umbral_gas = GAS_UMBRAL_APERTURA;
    umbral_cierre_gas = GAS_UMBRAL_CIERRE;
    agua_vacio_cm = AGUA_VACIO_CM_DEFECTO;
    agua_lleno_cm = AGUA_LLENO_CM_DEFECTO;
    return;
  }

  // El contador de arranques se incrementa en cada arranque y es la parte del id
  // que cambia al reiniciar (prueba P28 del plan).
  arranque_actual = static_cast<uint16_t>(nvs.getULong(CLAVE_ARRANQUE, 0) + 1);
  nvs.putULong(CLAVE_ARRANQUE, arranque_actual);

  contador_alerta = static_cast<uint16_t>(nvs.getUShort(CLAVE_CONTADOR, 0));
  umbral_gas = nvs.getInt(CLAVE_UMBRAL, GAS_UMBRAL_APERTURA);
  // El cierre se guarda aparte. En una placa que ya tinha el umbral viejo, esta
  // clave no existe y sale el valor por defecto del contrato, que es lo correcto:
  // antes el cierre venia de restar 150 y ahora sale de aqui.
  umbral_cierre_gas = nvs.getInt(CLAVE_UMBRAL_CIERRE, GAS_UMBRAL_CIERRE);
  agua_vacio_cm = nvs.getInt(CLAVE_AGUA_VACIO, AGUA_VACIO_CM_DEFECTO);
  agua_lleno_cm = nvs.getInt(CLAVE_AGUA_LLENO, AGUA_LLENO_CM_DEFECTO);
  // Un cierre que no sea menor que la apertura dejaria el episodio abierto para
  // siempre: nunca se podria cumplir «menor que el de cierre». Se corrige aqui, en
  // el arranque, en vez de solo al escribir el valor.
  if (agua_lleno_cm <= 0 || agua_lleno_cm >= agua_vacio_cm) {
    log_aviso(ETIQUETA, "calibracion agua invalida (%d/%d), se usan defaults",
              agua_vacio_cm, agua_lleno_cm);
    agua_vacio_cm = AGUA_VACIO_CM_DEFECTO;
    agua_lleno_cm = AGUA_LLENO_CM_DEFECTO;
  }
  if (umbral_cierre_gas >= umbral_gas) {
    log_aviso(ETIQUETA, "cierre %d no es menor que apertura %d, se usa %d",
              umbral_cierre_gas, umbral_gas, GAS_UMBRAL_CIERRE);
    umbral_cierre_gas = GAS_UMBRAL_CIERRE < umbral_gas ? GAS_UMBRAL_CIERRE
                                                        : umbral_gas - 1;
  }
  host_previo = nvs.getString(CLAVE_HOST, "").c_str();
  reinicios_red_cnt = nvs.getUChar(CLAVE_RR_CNT, 0);
  reinicios_red_epoch = nvs.getULong(CLAVE_RR_EPOCH, 0);

  nvs.end();
  log_info(ETIQUETA, "arranque=%u contador=%u umbral_gas=%d cierre_gas=%d agua=%d/%d",
           arranque_actual, contador_alerta, umbral_gas, umbral_cierre_gas,
           agua_vacio_cm, agua_lleno_cm);
}

uint16_t persistencia_arranque_actual() {
  return arranque_actual;
}

uint16_t persistencia_siguiente_contador_alerta() {
  ++contador_alerta;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putUShort(CLAVE_CONTADOR, contador_alerta);
    nvs.end();
  }
  return contador_alerta;
}

int persistencia_umbral_gas() {
  return umbral_gas;
}

void persistencia_fijar_umbral_gas(int valor) {
  if (valor <= 0) {
    log_aviso(ETIQUETA, "umbral %d invalido, se conserva %d", valor, umbral_gas);
    return;
  }
  umbral_gas = valor;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putInt(CLAVE_UMBRAL, valor);
    nvs.end();
  }
  log_info(ETIQUETA, "umbral_gas ahora %d", valor);
}

int persistencia_umbral_cierre_gas() {
  return umbral_cierre_gas;
}

void persistencia_fijar_umbral_cierre_gas(int valor) {
  // Tiene que ser un numero de ADC y menor que el de apertura. Si no lo es, se
  // conserva el que hay: un cierre mayor que la apertura deja el episodio que no
  // se puede cerrar nunca, que es peor que no cambiar el umbral.
  if (valor <= 0 || valor > GAS_ADC_MAXIMO) {
    log_aviso(ETIQUETA, "cierre %d invalido, se conserva %d", valor,
              umbral_cierre_gas);
    return;
  }
  if (valor >= umbral_gas) {
    log_aviso(ETIQUETA, "cierre %d no es menor que la apertura %d, se conserva %d",
              valor, umbral_gas, umbral_cierre_gas);
    return;
  }
  umbral_cierre_gas = valor;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putInt(CLAVE_UMBRAL_CIERRE, valor);
    nvs.end();
  }
  log_info(ETIQUETA, "umbral_cierre_gas ahora %d", valor);
}


int persistencia_agua_vacio_cm() {
  return agua_vacio_cm;
}

void persistencia_fijar_agua_vacio_cm(int valor) {
  if (valor <= 0 || valor > AGUA_DISTANCIA_MAX_CM) {
    log_aviso(ETIQUETA, "vacio %d invalido, se conserva %d", valor, agua_vacio_cm);
    return;
  }
  if (agua_lleno_cm > 0 && valor <= agua_lleno_cm) {
    log_aviso(ETIQUETA, "vacio %d no es mayor que lleno %d, se conserva %d",
              valor, agua_lleno_cm, agua_vacio_cm);
    return;
  }
  agua_vacio_cm = valor;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putInt(CLAVE_AGUA_VACIO, valor);
    nvs.end();
  }
  log_info(ETIQUETA, "agua_vacio_cm ahora %d", valor);
}

int persistencia_agua_lleno_cm() {
  return agua_lleno_cm;
}

void persistencia_fijar_agua_lleno_cm(int valor) {
  if (valor <= 0 || valor >= agua_vacio_cm) {
    log_aviso(ETIQUETA, "lleno %d invalido, se conserva %d", valor, agua_lleno_cm);
    return;
  }
  agua_lleno_cm = valor;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putInt(CLAVE_AGUA_LLENO, valor);
    nvs.end();
  }
  log_info(ETIQUETA, "agua_lleno_cm ahora %d", valor);
}

String persistencia_host_previo() {
  return host_previo;
}

void persistencia_fijar_host_previo(const String &host) {
  if (host.isEmpty() || host == host_previo) {
    return;
  }
  host_previo = host;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putString(CLAVE_HOST, host);
    nvs.end();
  }
  log_info(ETIQUETA, "host previo guardado: %s", host.c_str());
}

uint8_t persistencia_reinicios_red_cnt() {
  return reinicios_red_cnt;
}

uint32_t persistencia_reinicios_red_epoch() {
  return reinicios_red_epoch;
}

void persistencia_fijar_reinicios_red(uint8_t cnt, uint32_t epoch) {
  reinicios_red_cnt = cnt;
  reinicios_red_epoch = epoch;
  if (nvs.begin(ESPACIO, false)) {
    nvs.putUChar(CLAVE_RR_CNT, cnt);
    nvs.putULong(CLAVE_RR_EPOCH, epoch);
    nvs.end();
  }
}
