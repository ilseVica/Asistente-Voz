# Conexiones del nodo ESP32

Leído de `firmware/include/config.h` y de `fase4_md/Fase_4_Desarrollo_e_Integracion.md`.

| Componente | Pin del componente | GPIO ESP32 | Alimentación | Resistencias / notas |
| --- | --- | --- | --- | --- |
| DHT22 | VCC / DATA / GND | 32 (DATA) | 3,3 V | Pull-up del DHT22: 4,7–10 kΩ (solo si el módulo no la trae) |
| MQ-2 | VCC / A0 / GND | 34 (A0, ADC1_CH6) | 5 V | Divisor 10 kΩ / 20 kΩ |
| PIR HC-SR501 | VCC / OUT / GND | 35 (OUT, solo entrada) | 5 V | Sin resistencia (salida push-pull 3,3 V) |
| HC-SR04 / JSN-SR04T (nivel de agua) | VCC / TRIG / ECHO / GND | 13 (TRIG, salida), 36 (ECHO, solo entrada) | 5 V (riel + izquierdo de la placa 2) | VCC naranja → riel + izq. placa 2; GND negro → riel − izq. placa 2; TRIG blanco (cable 10) → GPIO 13 directo; ECHO amarillo → H3 placa 2 → divisor R4 1 kΩ (fila 3 a fila 6) y R5 2 kΩ (J6 al riel −) → F6 → cable 9 morado → GPIO 36 (SVP). JSN-SR04T: zona ciega ~20 cm (AGUA_ZONA_CIEGA_CM). |
| Relé SONGLE SRD-05VDC-SL-C (2 canales) | IN1 / (IN2 sin conectar) | 26 (IN1) | Bobina 5 V; VCC del módulo 3,3 V | Se activa en BAJO. Foco USB en COM1/NO1 |
| Buzzer | Base del 2N2222 | 27 | 5 V | Resistencia 1 kΩ en la base. Diodo 1N4148 **puesto** (probado 29 oct): raya (cátodo) en el riel + de la placa 1 a la altura de las filas 51-52; la otra pata en la fila del − del buzzer (cable verde que va al colector). Si la raya queda al revés, el buzzer no suena y el transistor se calienta. |
| Botón BOOT | — | 0 | — | Luz (antirrebote 50 ms) |
| Botón externo | — | 33 | — | Deshabilitado por defecto |
| LED integrado | — | 2 | — | Activo en nivel alto |

Notas:
- Nivel de agua: TRIG GPIO 13 directo (cable blanco 10); ECHO GPIO 36 (SVP, input-only) por divisor 1 kΩ / 2 kΩ (R4 fila 3→6, R5 J6→riel −, salida F6, cable 9 morado). VCC naranja y GND negro al riel izquierdo de la placa 2 (5 V).
- MQ-2: calentamiento de 3 minutos; durante ese tiempo `gas_adc` se publica `null`.
- PIR HC-SR501: calentamiento de 60 s.
- IN2 del relé queda sin conectar (reserva).
