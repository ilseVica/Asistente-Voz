#pragma once

#include <Arduino.h>

// Lecturas de sensores. Cada campo puede NO tener dato: con DHT22_HABILITADO o
// MQ2_HABILITADO en false, con el DHT22 fallando tres lecturas seguidas, o con el
// MQ-2 todavia calentando. En ese caso el correspondiente indicador `hay_*` esta en
// false y el JSON sale con un null explicito, no con un valor inventado (2026-10-19).
//
// La presencia sale del PIR real con PIR_HABILITADO=true; con la bandera en false
// sigue la simulacion de SIM_PRESENCIA_PERIODO_S.

// «Sin dato» en gas_adc. El rango valido del ADC va de 0 a GAS_ADC_MAXIMO, asi que
// -1 no puede confundirse con una lectura.
constexpr int SENSORES_SIN_GAS = -1;

struct Lectura {
  // false = no hay dato en ese campo y hay que publicar null.
  bool hay_temp = false;
  bool hay_hum = false;
  bool hay_gas = false;
  float temp_c = 0.0;
  int hum_pct = 0;
  int gas_adc = 0;
  bool presencia = false;
};

// Prepara ADC, DHT22 y el calentamiento del MQ-2. Idempotente.
void sensores_iniciar();

// Publicacion de casa/sala/sensores con la regla de frecuencia del plan (2.7):
// se publica si temp_c cambia 0.3 grados, hum_pct 2 %, gas_adc 50 o presencia
// cambia; nunca mas de una vez cada 10 s; y ademas un latido cada 60 s.
// Un campo que pasa de «sin dato» a tener valor (o al reves) SI cuenta como cambio.
void sensores_tarea();

// Una lectura nueva. No bloquea: el DHT22 se lee como mucho una vez cada 2 s y el
// MQ-2 en cada llamada (el muestreo a 1 Hz lo decide gas_tarea).
Lectura sensores_leer();

// Valor forzado por nodo/prueba_gas y por el comando GAS del monitor serie (solo con
// PRUEBAS=1). Manda SOBRE la bandera MQ2_HABILITADO y sobre el calentamiento: es la
// via de prueba de la alarma (P45) y por eso funciona tambien sin sensor. -1 vuelve
// al sensor (o a «sin dato» si la bandera esta en false o el MQ-2 calienta).
void sensores_fijar_gas_prueba(int valor_adc);
int sensores_gas_prueba_actual();

// Valor forzado del PIR por el comando serie PIR <0|1> (solo con PRUEBAS=1).
// 0 o 1 simula el nivel del pin como si el sensor lo estuviera dando; -1 vuelve a
// leer el pin real. El enclavamiento funciona igual con el valor forzado.
void sensores_fijar_pir_prueba(int valor);
int sensores_pir_prueba_actual();
