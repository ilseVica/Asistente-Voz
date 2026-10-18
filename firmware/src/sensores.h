#pragma once

#include <Arduino.h>

// Lecturas de sensores. La presencia ya sale del PIR real cuando PIR_HABILITADO
// esta en true (config.h); temperatura, humedad y gas siguen simulados hasta que
// lleguen el MQ-2 y el DHT22 (6.7 del plan). Cuando lleguen, se sustituyen SOLO
// las funciones de este modulo y no se toca nada mas del firmware.

struct Lectura {
  float temp_c = 0.0;
  int hum_pct = 0;
  int gas_adc = 0;
  bool presencia = false;
};

// Prepara ADC y temporizadores. Idempotente.
void sensores_iniciar();

// Publicacion de casa/sala/sensores con la regla de frecuencia del plan (2.7):
// se publica si temp_c cambia 0.3 grados, hum_pct 2 %, gas_adc 50 o presencia
// cambia; nunca mas de una vez cada 10 s; y ademas un latido cada 60 s.
void sensores_tarea();

// Una lectura nueva. No bloquea.
Lectura sensores_leer();

// Valor forzado por nodo/prueba_gas. Solo con el firmware de pruebas se puede
// cambiar; en el normal, la senal simulada se queda siempre en rango de aire
// limpio. -1 restaura el valor simulado.
void sensores_fijar_gas_prueba(int valor_adc);
int sensores_gas_prueba_actual();

// Valor forzado del PIR por el comando serie PIR <0|1> (solo con PRUEBAS=1).
// 0 o 1 simula el nivel del pin como si el sensor lo estuviera dando; -1 vuelve a
// leer el pin real. El enclavamiento funciona igual con el valor forzado.
void sensores_fijar_pir_prueba(int valor);
int sensores_pir_prueba_actual();

// Aleatoriedad acotada, para que la simulacion no sea perfeitamente plana.
int sensores_aleatorio_entre(int minimo, int maximo);
