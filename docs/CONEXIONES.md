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

## Reacomodo de cables (9 nov)

Orientación: placa 1 atrás, placa 2 al frente, fila 1 a la derecha; rieles
«de arriba» y «de abajo». Los pines del ESP32 no cambiaron; solo dónde va cada
cable en la protoboard.

- Relé: R+ en el riel + de abajo de la placa 1 (fila 42), R− en el riel − de
  abajo de la placa 1 (fila 44), I− en la fila 47. El cable 7 (VCC del lado IN)
  va al riel + de abajo de la placa 2 (fila 25, 3,3 V).
- MQ-2: VCC al riel + de abajo de la placa 1 (fila 23), GND al riel − de
  abajo de la placa 1 (fila 26), AOUT (cable 6) a H18.
- PIR HC-SR501: VCC al riel + de abajo de la placa 1 (fila 37), GND al riel −
  de abajo de la placa 1 (fila 39).
- Agua (HC-SR04/JSN-SR04T): VCC al riel + de arriba de la placa 2 (fila 1),
  GND al riel − de arriba de la placa 2 (fila 3), ECHO a H3.
  `AGUA_HABILITADO=true`, calibración 20/5 cm.
- DHT22: GND en A10, DATA en A12, VCC en A13. E10 → riel − de arriba de la
  placa 2; B13 → riel + de abajo de la placa 2 (3,3 V). El riel − de abajo de
  la placa 2 ya no se usa.
- Puentes: placa 1 arriba↔abajo en las filas 2 (+) y 3 (−), y vuelta en 59–60.
  Placa 1 → placa 2 en las filas 14 y 21 (5 V) y 16 y 19 (GND).
- Cable 1: A11 → riel + de abajo de la placa 2, fila 9.

Sin cambios: divisores (R4 I3–I6, R5 J6 → riel −, R2 I18–I22, R3 J22 → riel −),
buzzer y pines del ESP32.
