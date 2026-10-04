#pragma once

#include <Arduino.h>

// Lecturas de sensores. Hoy TODO esta simulado porque el hardware todavia no
// existe (6.7 del plan). Cuando lleguen el MQ-2, el DHT22 y el PIR, se sustituyen
// SOLO las funciones de este modulo y no se toca nada mas del firmware.

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

// Aleatoriedad acotada, para que la simulacion no sea perfeitamente plana.
int sensores_aleatorio_entre(int minimo, int maximo);
