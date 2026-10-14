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
- Guarda en SQLite cada alerta que acepta, y **un envío por cada familiar
  registrado** en el momento de encolarla, cada uno con su propio contador de
  intentos.
- Manda a Telegram lo que está pendiente, y reintenta con espera creciente si
  falla. **Para siempre**: una alerta de gas no se descarta nunca. Y si a un
  familiar no le sale, a los demás les sale igual.
- Publica en `casa/sala/satelite/decir` cuando alguien pulsa «Ya lo atendí».
- Purga sola: los `id` a los 7 días, las alertas a los 30.

**No, todavía:**

- No manda alertas de inactividad. El plan lo menciona, pero eso necesita un reloj
  de avisos que no existe.
- No publica órdenes de luz. Está leído para incluirlas en un mensaje («la luz
  sigue encendida»), pero no las manda.
- No manda alertas de inactividad, y no avisa a nadie si alguien se queda
  postrado: eso necesita un reloj de avisos que no existe.
- No habla con Alexa ni con el panel web. Con el panel solo entrega el enlace de
  un solo uso cuando alguien escribe `/panel`; los datos y las alertas del panel
  salen de la misma base, no del bot. Con el satélite solo avisa que una
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

Al escribir `/registro <código>` en un chat privado, el bot da de alta ese `chat_id`
en la tabla `familiares` de SQLite y responde confirmando. **Cada chat que se
registra se suma**: el segundo familiar no sustituye al primero, y a partir de ahí
los dos reciben las alertas nuevas.

Sin esto, cualquiera que encontrara el bot podía escribir `/registro` y quedarse
con las alertas de gas de la casa. Que nadie pueda adivinar el código es la mitad
del trabajo; que el código caduque y se pueda usar una sola vez, la otra mitad.

Un chat **ya registrado** que escribe `/registro` no recibe nada. Ni siquiera un
«ya estás registrado»: confirmar que el bot existe ya le dice a cualquiera que
hable con él que este es el bot de la casa, y eso solo se le dice a quien ya está
dentro.

### Ver y dar de baja a los familiares

La lista está en SQLite, no en un archivo, así que se pregunta a la base:

```bash
docker compose exec orquestador python -m registro familiares
docker compose exec orquestador python -m registro baja CHAT_ID
```

`familiares` dice el `chat_id`, el nombre, cuándo se dio de alta y cuántos avisos
le quedaron sin mandar. `baja` lo quita y **cancela lo que le quedaba por
recibir**; los avisos que ya tenía entregados no se pueden deshacer, porque esa
persona ya los vio.

El `chat_id` entero sale por la consola del operador y **nunca por el log**: en
los logs va enmascarado (`...2570`), porque `docker logs` lo lee cualquiera con
acceso al servidor y un `chat_id` es el número de una persona.

### `familia.json`

Ya no se usa. Era el diseño de un solo familiar: un archivo con un `chat_id`. La
primera vez que arranca esta versión, el familiar de ese archivo se copia a la
tabla y el archivo se renombra a `familia.json.migrado` —se renombra, no se borra,
por si el servicio se muere entre la copia y el renombrado—.

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

## Las tablas, y por qué el estado no está en `alertas`

Hay cuatro tablas:

| Tabla | Para qué |
| --- | --- |
| `ids_procesados` | la memoria de lo ya visto, para no avisar dos veces |
| `familiares` | quién recibe las alertas |
| `alertas` | qué pasó, con su JSON y sus horas |
| `envios` | **quién lo tiene que recibir, y cómo va** |

La que manda es `envios`: una fila por `(alerta_id, chat_id)`, con su `intentos`, su
`proximo_intento_en` y su `enviado_en`. **`alertas` ya no tiene una columna de
estado**, y el estado sale de los envíos (`db.CASE_ESTADO`).

La razón es que con dos familiares «el estado de la alerta» no existe como un
dato: la misma alerta puede estar entregada a una persona y esperando a otra que
tenía el móvil sin cobertura. Si el estado fuera una columna de `alertas`, habría
que elegir cuál de los dos escribir, y la elección siempre pierde a alguien. Con
una fila por persona, las dos cosas son verdad a la vez y `/alertas` puede decir
«1 de 2», que es justo la información que hace falta cuando alguien pregunta por
qué su hermano no ha visto el aviso.

Por eso, un fallo de red **no descarta nada**: se anota en la fila del envío que
falló y los demás siguen su camino. Lo único que se descarta es un `carga_json`
ilegible, porque reintentar un texto que no se puede leer no lo arregla nunca.

`ids_procesados` y `alertas` **no tienen llave foránea entre ellas**, a propósito:
los `id` se purgan a los 7 días y las alertas a los 30, y con una llave foránea al
purgar los `id` la alerta de hace diez días se perdería en cascada. Ninguna de las
dos necesita la relación.

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
PANEL_URL=http://villaca.tail722206.ts.net:8080   # el botón "Abrir panel"
```

`PANEL_URL` vacío no es un error: el bot contesta que el panel todavía no está
disponible y no manda ningún enlace roto. Pero con el panel desplegado tiene que
tener valor, o el botón del aviso de alerta no aparece.

`TELEGRAM_CHAT_ID` **no** hay que ponerlo: la lista la descubre el bot con
`/registro`. Si se pone y la lista está vacía, se usa como atajo para dar de alta
ese chat al arrancar, y no pasa nada más.

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
| `/registro <código>` | Suma **este** chat a la lista con un código de un solo uso, sin quitar a nadie |
| `/alertas` | Cuántas hay pendientes, cuántas se mandaron y las últimas cinco |
| `/panel` | Manda un botón **Abrir panel** con un enlace de un solo uso, que caduca a los 10 minutos |

`/registro` sin código, o con un código que no sirve, pide el código. Y si el chat
**ya está en la lista**, no se responde nada: el silencio es de ese chat, no global.

`/alertas` contesta a **cualquier** familiar registrado, y a nadie más. Que el
silencio de `/registro`, este permiso y el botón de «Ya lo atendí» pregunten lo
mismo a la base es lo que evita que haya un chat que puede leer el historial pero
no puede resolver, o al revés.

Y en cada aviso de alerta hay un botón:

| Botón | Qué hace |
| --- | --- |
| **Ya lo atendí** | Marca la alerta como `resuelta`, reescribe el mensaje para que se vea como atendida, y avisa al satélite |

Que `/registro` solo funcione con un código, y solo una vez, es lo que evita que
cualquiera que escriba al bot se meta en la lista de quien recibe los avisos de gas.
Por eso, cuando ese chat ya está registrado, no se responde nada: ni siquiera «ya
estás registrado», porque confirmar que el bot existe ya le dice a cualquiera que
hable con él que este es el bot de la casa.

**`/silencio` se quitó a propósito.** Hasta la versión anterior, el bot tenía un
comando que ponía una hora de silencio y contestaba que las alertas «se siguen
guardando». El problema es que el gas llegaba igual: era una mentira del bot, no
un silencio real, y encima podía aparecer sin que nadie lo
pidiera. En una casa, un silencio que no silencia el gas es peor que no tener
silencio. Quien atiende una alerta la marca con «Ya lo atendí», que sí es verdad.

El botón «Ya lo atendí» solo resuelve una vez por alerta. Si se pulsa dos veces, la
segunda responde «Esta alerta ya estaba atendida» y no vuelve a avisar al
satélite.

**Y también se reescribe cuando la alerta se atiende desde el panel.** La
resolución tiene dos caminos —el botón de Telegram y el botón del panel web—, los
dos pasan por la misma función (`alertas.resolver`) y los dos dejan la alerta igual
en la base. Lo que solo puede hacer el bot es reescribir su mensaje mientras
resuelve: el panel es otro proceso y no lleva el token. Para que la familia no se
quede viendo un aviso con los botones puestos cuando ya hay alguien con el aviso,
la cola hace la otra mitad: cada cinco segundos mira los avisos ya enviados cuya
alerta está resuelta y todavía no se ha reescrito, y los deja como los deja el
botón («ATENDIDA», sin botones). El `message_id` con el que salió cada aviso se
guarda en `envios.mensaje_id` en cuanto se manda, y `envios.editado_en` es lo que
hace que cada aviso se reescriba una sola vez: si lo reescribió el propio botón, la
cola no lo vuelve a tocar. `alertas.texto_atendido` es la única redacción, así que
da igual por dónde se hubiera atendido.

## Los reintentos no tienen fin

Las esperas son 15, 30, 60, 120 y 300 segundos, y a partir de ahí siempre 300:

```
15, 30, 60, 120, 300, 300, 300, ...
```

**Una alerta no se descarta nunca.** Un fallo de red, un Telegram caído o el bot
suspendido un mes dejan la alerta pendiente, y sale sola cuando el problema se
arregla.

Y cada familiar va por su cuenta: el móvil sin cobertura acumula sus intentos y
sus esperas sin tocar los del otro. El caso que duele es el contrario —un teléfono
que bloquea al bot se queda sin alertas para siempre— y por eso la baja de un
familiar es una decisión del operador, no algo que el sistema decida solo. La alternativa —dar la alerta por perdida tras cinco intentos— convierte
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
| `PANEL_URL` | vacío | El botón "Abrir panel". Vacío: el botón no se muestra y nunca se manda un enlace roto |
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
