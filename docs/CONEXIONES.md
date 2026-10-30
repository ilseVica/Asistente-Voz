# Conexiones del nodo ESP32

Leído de `firmware/include/config.h` y de `fase4_md/Fase_4_Desarrollo_e_Integracion.md`.

| Componente | Pin del componente | GPIO ESP32 | Alimentación | Resistencias / notas |
| --- | --- | --- | --- | --- |
| DHT22 | VCC / DATA / GND | 32 (DATA) | 3,3 V | Pull-up del DHT22: 4,7–10 kΩ (solo si el módulo no la trae) |
| MQ-2 | VCC / A0 / GND | 34 (A0, ADC1_CH6) | 5 V | Divisor 10 kΩ / 20 kΩ |
| PIR HC-SR501 | VCC / OUT / GND | 35 (OUT, solo entrada) | 5 V | Sin resistencia (salida push-pull 3,3 V) |
| Relé SONGLE SRD-05VDC-SL-C (2 canales) | IN1 / (IN2 sin conectar) | 26 (IN1) | Bobina 5 V; VCC del módulo 3,3 V | Se activa en BAJO. Foco USB en COM1/NO1 |
| Buzzer | Base del 2N2222 | 27 | 5 V | Resistencia 1 kΩ en la base. Diodo 1N4148 **pendiente** (raya en J50, otra pata en I53). Si la raya queda al revés, el buzzer no suena y el transistor se calienta. |
| Botón BOOT | — | 0 | — | Luz (antirrebote 50 ms) |
| Botón externo | — | 33 | — | Deshabilitado por defecto |
| LED integrado | — | 2 | — | Activo en nivel alto |

Notas:
- MQ-2: calentamiento de 3 minutos; durante ese tiempo `gas_adc` se publica `null`.
- PIR HC-SR501: calentamiento de 60 s.
- IN2 del relé queda sin conectar (reserva).
