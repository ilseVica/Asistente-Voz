# Guion de demostración (10 minutos)

Tabla para el equipo. Columnas: minuto · quién · qué se dice o se hace · qué debe
pasar · si falla. El tono es directo porque es para el equipo, no para el usuario.

Si «Lucía» no responde en algún paso: usar F9 o el botón «Mantener para hablar»
del panel. Si la luz no responde: botón físico del ESP32. Si cae la red: hotspot de
respaldo. Último recurso: el video de respaldo.

| Minuto | Quién | Qué se dice o se hace | Qué debe pasar | Si falla |
| --- | --- | --- | --- | --- |
| 0:00 | Presentador | El problema: adultos mayores que viven solos y su familia lejos. Frase: «Alexa obedece; Lucía acompaña». | Contexto de la demostración. | Video de respaldo. |
| 1:00 | Usuario | «Lucía» → tono → «prende la luz» y después «apaga la luz». Mostrar que el panel cambia en vivo. | El foco cambia y el panel se actualiza sin recargar. | F9 o botón del panel; botón físico del ESP32; video de respaldo. |
| 2:30 | Lucía | Buenos días: saludo, hora, temperatura y efeméride. | Lucía dice la hora, la temperatura y una efeméride. | Repetir con F9; video de respaldo. |
| 4:00 | Presentador | Lanzar el saludo diario con `herramientas/saludo.sh`; responder «bien, gracias» al saludo diario y probar también «no me siento bien». | Lucía pregunta «¿Cómo amaneció?», se envía alerta a Telegram y al panel; se cierra con «Ya lo atendí». | F9 o botón del panel; hotspot de respaldo; video de respaldo. |
| 5:30 | Usuario | «¿cuánta agua hay?» con la cubeta a la mitad. Bajar el nivel para mostrar la alerta de agua baja (con ~1 L). | Lucía dice el porcentaje; aparece la alerta de agua baja. | `herramientas/agua.sh`; video de respaldo. |
| 7:00 | Presentador | Alarma de gas con el método de simulación existente (`herramientas/gas.sh 3000`), sin abrir la torre. | Buzzer local, aviso por voz y alerta por Telegram y panel. | `herramientas/gas.sh 0`; hotspot de respaldo; video de respaldo. |
| 8:00 | Familia ↔ sala | Un mensaje desde el panel, que Lucía dice en voz, y la respuesta por voz, que aparece en el panel. | El mensaje se oye en la sala y la respuesta queda transcrita en el panel. | F9 o botón del panel; video de respaldo. |
| 8:45 | Usuario | «ayuda» → alerta a la familia. Mostrar también «cancela». | Llega la alerta a la familia; «cancela» la deshace como falsa alarma. | F9 o botón del panel; video de respaldo. |
| 9:15 | Presentador | El panel en un teléfono con datos móviles. | El panel abre en `https://asistente.tail722206.ts.net` y el WebSocket conecta. | Hotspot de respaldo; video de respaldo. |
| 9:45 | Presentador | Cierre: privacidad (la voz se procesa en casa, sin nube) y trabajo futuro (música por voz, «Lucía, …» de corrido). | Mensaje final de la demostración. | Video de respaldo. |

## Lista de verificación 15 minutos antes

- [ ] En la VM: `docker compose ps`, con los 5 servicios Up (el 15 nov, tras encender el servidor, Mosquitto no arrancó y todo quedó caído).
- [ ] En el panel: la sala conectada y el ESP32 con datos.
- [ ] En la laptop: «MQTT conectado» en el journal del satélite.
- [ ] Todos los equipos conectados (VM, laptop, ESP32, Echo si aplica).
- [ ] Nivel de agua listo para la demo (cubeta a mitad y ~1 L para la alerta).
- [ ] Sin alertas abiertas en el panel ni en Telegram.
- [ ] Volumen de bocinas y micrófono comprobado.
- [ ] `MUSICA_HABILITADO=0` en `servidor/.env`.
- [ ] `SALUDO_DIARIO_ACTIVO` según lo previsto para la demo.
- [ ] F9 probada.
- [ ] Satélite conectado (panel → Equipos) y ESP32 «con hora».
- [ ] Hotspot en 2.4 GHz («Maximizar compatibilidad») si se usa el respaldo.
- [ ] Video de respaldo disponible y probado.

## `herramientas/guion.sh` y este guion

Se revisó `herramientas/guion.sh` contra este guion nuevo. El script actual es la
demostración técnica de 5 minutos y no coincide con el guion de 10 minutos:

- Usa el nodo simulado (`sim-nodo`), no el ESP32 real; el guion nuevo usa el
  dispositivo real y los simuladores `gas.sh` y `agua.sh`.
- Sus pasos 5 y 6 (cierre del episodio de gas y cambio de umbrales con
  `nodo/config`) son comprobaciones técnicas válidas, pero no aparecen en el
  guion de 10 minutos.
- No cubre saludo diario, agua, mensajes familia ↔ sala, «ayuda»/«cancela» ni el
  panel con datos móviles.
- No referencia música (correcto: está apagada) ni video.

Propuesta (pendiente de su «aprobado»): mantener el script actual como
«guion técnico» o reescribirlo para seguir el flujo nuevo usando
`gas.sh 3000`, `agua.sh 30` y `saludo.sh`, con pausas para las acciones manuales.
No se cambia sin su «aprobado».
