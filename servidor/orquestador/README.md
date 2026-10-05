# Orquestador

El servicio que recibe las alertas de la casa, las guarda en una base de datos y
manda un aviso a la familia por Telegram. Es la primera parte del orquestador: lo
suficiente para que una alerta de gas llegue al celular de quien tiene que
enterarse.

## Qué hace y qué no hace

**Sí:**

- Escucha `casa/sala/alerta/gas` y `casa/sala/alerta/ayuda`, y solo esas dos.
- Descarta los mensajes con un `id` que ya había procesado, para no avisar dos
  veces de lo mismo.
- Junta las peticiones de ayuda que llegan seguidas (P05): dos en menos de un
  minuto son una sola alerta, no dos.
- Guarda en SQLite cada alerta que acepta, con su estado y su próximo reintento.
- Manda a Telegram lo que está pendiente, y reintenta con espera creciente si
  falla. **Para siempre**: una alerta de gas no se descarta nunca.
- Publica en `casa/sala/satelite/decir` cuando alguien pulsa «Ya lo atendí».
- Purga sola: los `id` a los 7 días, las alertas a los 30.

**No, todavía:**

- No manda alertas de inactividad. El plan lo menciona, pero eso necesita un reloj
  de avisos que no existe.
- No publica órdenes de luz. Está leído para incluirlas en un mensaje («la luz
  sigue encendida»), pero no las manda.
- No manda alertas de inactividad, y no avisa a nadie si alguien se queda
  postrado: eso necesita un reloj de avisos que no existe.
- No habla con Alexa ni con el panel web. Con el satélite solo avisa que una
  alerta quedó atendida.

## El registro con código de un solo uso

`/registro` **no** guarda el chat de quien lo escribe. Necesita un código, y el
código lo genera el operador en el servidor:

```bash
docker compose exec orquestador python -m registro codigo
```

El código vale 10 minutos, es de un solo uso, y aguanta cinco intentos. Se manda
por un canal que no sea el chat del bot, que es justo lo que hay que hacer con un
teléfono: se lo dices a la persona por teléfono, o se lo das en la mano.

Al escribir `/registro <código>` en un chat privado, el bot guarda ese `chat_id`
en `familia.json` y responde confirmando. A partir de ahí, ese chat es el único
destino de las alertas y el único que puede pulsar «Ya lo atendí».

Sin esto, cualquiera que encontrara el bot podía escribir `/registro` y quedarse
con las alertas de gas de la casa. Que nadie pueda adivinar el código es la mitad
del trabajo; que el código caduque y se pueda usar una sola vez, la otra mitad.

## Por qué la sesión MQTT es persistente

`clean_session=False` en `mqtt_cliente.py` es la línea más importante de todo el
servicio.

MQTT 3.1.1 tiene tres formas de entregar un mensaje:

| | Entregado | Dedicado |
| --- | --- | --- |
| QoS 0, `clean_session=True` | como mucho una vez, y se puede perder | no |
| QoS 1, `clean_session=True` | como mucho una vez, y **se pierde** si no hay conexión | no |
| QoS 1, `clean_session=False` | como mucho una vez, y **se guarda** hasta entregarla | sí |

El broker encola las alertas QoS 1 que llegan mientras el orquestador está caído,
hasta `max_queued_messages` (5000) y por `persistent_client_expiration` (2 días), y
se las entrega al reconectar. Con `clean_session=True`, una alerta de gas que
llegara durante un corte se descartaría en silencio, y la familia no se enteraría.

Por eso el `client_id` es fijo: `orquestador`, el mismo que el usuario MQTT. Si
fuera aleatorio, cada arranque dejaría una sesión huérfana en el broker durante 2
días, y esas sesiones consumen memoria sin que nadie las limpie.

Una aclaración que suele confundirse, porque las dos cosas suenan a lo mismo pero
no lo son:

- **Si el ESP32 está desconectado**, el broker no puede encolar lo que no recibió.
  Si el nodo no estaba conectado, la alerta nunca existió para el broker, y no hay
  nada que recuperar. La cola del broker protege al *orquestador*, no al *nodo*.
- **Si el orquestador está desconectado**, el broker sí le encola las alertas QoS 1
  de su sesión persistente, y se las entrega cuando vuelve. Eso es lo que se
  comprueba en la prueba P26.

## Por qué hay dos tablas

`ids_procesados` es la memoria de lo ya visto, y `alertas` es la cola de trabajo.
Están separadas y **no hay llave foránea entre ellas**, a propósito:

- Los `id` se purgan a los 7 días. Si no lo fueran, serían el historial de lo que pasó en la casa.
- Las alertas se purgan a los 30 días.
- Con una llave foránea entre las dos, al purgar los `id` a los 7 días, la alerta
  de hace diez días se perdería en cascada, o la purga fallaría. Ninguna de las
  dos cosas sirve.

Ninguna necesita la relación: `ids_procesados` solo se consulta con
`SELECT 1 WHERE id = ?`, y `alertas` se consulta por su propia clave primaria.

## Por qué el `id` no se valida con el formato del contrato

El contrato dice que un `id` de alerta es `<6 hex> - b<4 hex> - <4 hex>`, y así lo
genera el ESP32. Pero el plan también dice que **el servidor genera ids**, y esos
no tienen esa forma: son cosas como `srv-0001`.

Si el orquestador exigiera el formato exacto, las alertas del propio servidor
quedarían fuera del sistema, en silencio, porque el rechazo solo se registra en el
log. Eso es peor que aceptar un id raro.

Lo que sí se comprueba, en `alertas.py`, es lo mínimo para que el `id` sea usable
como clave primaria y no rompa nada:

- que sea texto, no un número ni un objeto;
- de 1 a 64 caracteres, que es el límite de SQLite para una clave de texto;
- solo minúsculas, dígitos y guiones. Sin esta regla, un `id` con `%` o con `LIKE`
  metido se cuela en consultas que el `?` de SQLite no protege.

## El riesgo de doble envío, y por qué se acepta

**El riesgo existe y no se puede quitar del todo.**

Si el proceso muere **entre** la respuesta de Telegram y el `UPDATE` que marca la
alerta como enviada, el siguiente arranque la encuentra pendiente y la manda otra
vez. La ventana es de unos cientos de milisegundos: lo que tarda el `commit` de
SQLite después de que Telegram confirme.

No hay forma de cerrarla del todo, y conviene entender por qué:

- Telegram no tiene idempotencia. No hay forma de mandarle un `id` y que responda
  "ya te lo mandé".
- Marcar la alerta como enviada **antes** de mandar evitaría el duplicado, pero
  dejaría las alertas sin avisar si la red falla. Un aviso de gas que el sistema
  cree haber enviado y que nadie ha visto es el peor fallo posible aquí.

Se eligió el lado malo a propósito: es preferible que la familia reciba dos avisos
de gas a que no reciba ninguno.

## Puesta en marcha

```bash
cd /home/ilse/Servidores/Asistente-Voz/servidor
docker compose up -d orquestador
docker compose logs -f orquestador
```

El `.env` del servidor tiene que tener, además de lo que ya tenía:

```
TELEGRAM_BOT_TOKEN=...    # el token de BotFather
TELEGRAM_ACTIVAR_BOT=1    # sin esto el bot de /registro no arranca
PANEL_URL=                 # vacío está bien: el panel no existe todavía
```

`TELEGRAM_CHAT_ID` **no** hay que ponerlo: lo descubre el bot con `/registro`.

### Sin token

Si no hay token, el servicio **arranca igual** y funciona todo menos el envío.
Las alertas se encolan y se quedan ahí, sin consumir intentos de reintento, y en
cuanto se añade el token y se reinicia el servicio salen todas.

Esto es distinto de un fallo de red de Telegram. Un fallo de red consume un
intento y la alerta sigue pendiente para siempre. La falta de token, tampoco: es
una espera, no un fallo.

```
ERROR telegram: sin TELEGRAM_BOT_TOKEN: las alertas se encolan pero no se envian
```

## Guion de demostración

Para enseñar el sistema entero funcionando en 5 minutos hay un guion con los
comandos sueltos en `herramientas/GUION.md`, y un script que los hace por ti:

```bash
bash herramientas/guion.sh
```

Va desde encender la luz hasta cerrar un episodio de gas y disparar el siguiente, y
se para en el paso donde hace falta el móvil.

## Comandos del bot

Solo en chat privado. En un grupo se rechazan, porque un bot de la casa en un
grupo del vecindario publicaría el teléfono de la persona que lo usa.

| Comando | Qué hace |
| --- | --- |
| `/registro <código>` | Registra **este** chat con un código de un solo uso. Sin código, no hace nada |
| `/alertas` | Cuántas hay pendientes, cuántas se mandaron y las últimas cinco |

Y en cada aviso de alerta hay un botón:

| Botón | Qué hace |
| --- | --- |
| **Ya lo atendí** | Marca la alerta como `resuelta`, reescribe el mensaje para que se vea como atendida, y avisa al satélite |

Que `/registro` solo funcione con un código, y solo una vez, es lo que evita que
cualquiera que escriba al bot pueda cambiar el destino de las alertas de gas. Por
eso, cuando ya está registrado, no se responde nada: ni siquiera «ya estás
registrado», porque confirmar que el bot existe ya le dice a cualquiera que hable
con él que este es el bot de la casa.

**`/silencio` se quitó a propósito.** Hasta la versión anterior, el bot tenía un
comando que ponía una hora de silencio y contestaba que las alertas «se siguen
guardando». El problema es que el gas llegaba igual: era una mentira del bot, no
un silencio real, y encima podía aparecer sin que nadie lo
pidiera. En una casa, un silencio que no silencia el gas es peor que no tener
silencio. Quien atiende una alerta la marca con «Ya lo atendí», que sí es verdad.

El botón «Ya lo atendí» solo se реда una vez por alerta. Si se pulsa dos veces, la
segunda responde «Esta alerta ya estaba atendida» y no vuelve a avisar al
satélite.

## Los reintentos no tienen fin

Las esperas son 15, 30, 60, 120 y 300 segundos, y a partir de ahí siempre 300:

```
15, 30, 60, 120, 300, 300, 300, ...
```

**Una alerta no se descarta nunca.** Un fallo de red, un Telegram caído o el bot
suspendido un mes dejan la alerta pendiente, y sale sola cuando el problema se
arregla. La alternativa —dar la alerta por perdida tras cinco intentos— convierte
un problema temporal en una alarma que nadie recibe, que es exactamente el fallo
que este servicio existe para que no pase.

Lo que sí hay es un aviso: si una alerta lleva más de una hora sin poder mandarse,
el log lo dice cada cierto tiempo, con la hora que lleva esperando y el último
error. Un aviso al log, no un mensaje a la familia: si no hay red, no hay a quién
mandárselo.

## P05: dos ayudas seguidas son una

El Echo Pop manda `alerta/ayuda` cada vez que se activa, y una persona que pide
ayuda la pide dos veces. El orquestador junta las peticiones de ayuda que llegan
con menos de 60 segundos de diferencia y avisa una sola vez:

```
INFO alertas: alerta de ayuda 000000-b0003-0002 NO se encola por el antirrebote de 60 s (P05)
```

El `id` de la petición descartada **sí** queda registrado como visto. Si no, el
mensaje se contaría como nuevo otra vez dentro de una hora, y el efecto del
antirrebote se desharía solo.

El antirrebote es solo de las ayudas. Las de gas no se juntan con nada: si el gas
sube de verdad, que avise las veces que haga falta.

## Variables de entorno

| Variable | Por defecto | Para qué |
| --- | --- | --- |
| `MQTT_HOST` | `mosquitto` | Dónde está el broker |
| `MQTT_PUERTO` | `1883` | Puerto del broker |
| `MQTT_ORQUESTADOR_PASSWORD` | — | **Sin valor por defecto**: si falta, el servicio muere al arrancar |
| `MQTT_CLIENT_ID` | `orquestador` | El client id fijo |
| `TELEGRAM_BOT_TOKEN` | vacío | Sin token el servicio arranca y no envía |
| `TELEGRAM_ACTIVAR_BOT` | `0` | Si es `1`, arranca el bot de comandos |
| `PANEL_URL` | vacío | El botón "Abrir panel". Vacío está bien |
| `ORQUESTADOR_DB` | `/datos/orquestador.sqlite3` | La base, en el volumen |
| `NIVEL_LOG` | `INFO` | `DEBUG` para ver todo |

`MQTT_ORQUESTADOR_PASSWORD` es la única que mata el servicio si falta, y es a
propósito: es mejor que arranque y no pueda conectarse. El token es la excepción
opuesta, y por decisión explícita.

## Cosas que no se pueden probar sin el ESP32

| Prueba | Por qué no |
| --- | --- |
| El ESP32 recibe `luz/set` y enciende el relé | El firmware no está en la máquina |
| La histeresis real del ADC de gas | Es hardware |
| El Last Will del ESP32 al quitarle la corriente | Es hardware |

Todo lo demás se puede probar con el nodo simulado: la ACL, el broker, la
deduplicación, la cola, la purga y los textos de Telegram.
