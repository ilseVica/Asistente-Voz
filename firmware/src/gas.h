#pragma once

#include <Arduino.h>

// Episodios de gas y la maquina que los cierra.
//
// Reglas del plan:
//   - el episodio ABRE cuando hay GAS_LECTURAS_PARA_ABRIR lecturas seguidas con
//     gas_adc mayor o igual al umbral;
//   - el episodio CIERRA cuando gas_adc baja de (umbral menos histeresis) y se
//     mantiene asi durante GAS_CIERRE_SOSTENIDO_S;
//   - dentro de un episodio hay UN solo aviso. Si el valor vuelve a subir sin
//     llegar a cerrar el episodio, no hay alerta nueva;
//   - el buzzer suena durante TODO el episodio, sin interruptirse, y el resto del
//     sistema sigue funcionando;
//   - la retencion del valor la llevan los latidos de sensores y nodo/estado, que
//     son los unicos topicos donde el plan mete gas_adc.
//
// El aviso va a la cola de alertas (alertas.h) y de ahi sale solo cuando el broker
// lo confirma.

void gas_iniciar();
void gas_tarea();

// Valor forzado, solo para el comando GAS del monitor serie en PRUEBAS=1 y para
// nodo/prueba_gas. -1 devuelve el simulador.
void gas_fijar_lectura_forzada(int valor_adc);

bool gas_episodio_abierto();
int gas_ultima_lectura();
