# Servicio de voz (Vosk + WebSocket)

Etapa A del Paso 2. Este servicio recibe audio por WebSocket, lo pasa por Vosk y
publica el texto reconocido en MQTT. **No resuelve intenciones**: eso es del
orquestador (Paso 3). Aquí solo hay «¿qué se dijo?», no «¿qué hay que hacer?».

```
satélite  --(WebSocket: PCM 16 kHz + token)-->  servicio voz  --(MQTT)-->  orquestador
                                                        casa/sala/voz/texto
```

## Por qué es un servicio aparte

El reconocimiento es CPU y bloquea. Lo natural es tenerlo en su propio proceso,
con su propia `mem_limit`, para que una frase larga no retrase una alerta de gas.
El orquestador se queda con la decisión; el servicio de voz se queda con el oído.

## El protocolo (contrato)

El WebSocket es **persistente**: una conexión, un `inicio`, y después muchas
frases. El `token` va en el mensaje `inicio` y **nunca en la URL**, para que no
acabe en un log de proxy ni en el historial.

Mensajes que envía el satélite:

| Mensaje | Formato | Cuándo |
| --- | --- | --- |
| Inicio | `{"tipo":"inicio","token":"..."}` | Una vez, lo primero de todo. |
| Frase | `{"tipo":"frase","frase_id":"...","modo":"comandos"}` | Abre una frase. |
| Audio | binario (no JSON) | Tramas PCM 16 kHz mono 16-bit LE de **3200 B** (100 ms). |
| Fin | `{"fin":true}` | Cierra la frase y pide el resultado. |

Mensajes que devuelve el servicio:

| Mensaje | Formato |
| --- | --- |
| Listo | `{"tipo":"listo"}` (tras un `inicio` válido) |
| Final | `{"tipo":"final","frase_id":"...","texto":"...","modo":"comandos","marcas":{...}}` |
| Error | `{"tipo":"error","codigo":"...","frase_id":"..." o null,"detalle":"..."}` |

Reglas del audio:

- Tramas de 3200 B. La última puede ser más corta, pero **par** (16 bits por
  muestra). Una trama impar o de más de 3200 B es `formato_audio`.
- Más de **10 s** de audio en una frase (320000 B) es `frase_larga`.
- Más de **3 s** sin ninguna trama con una frase abierta es `inactividad`.
- Si el satélite se cae a mitad de una frase, la frase **se descarta** (se
  registra en el log y no se publica nada). Nunca se publica media frase.
- No hay resultados parciales: al satélite solo le llega el `final`, una vez.
- El modo `chat` todavía no existe: pedirlo devuelve `modo_chat_no_disponible`.

Códigos de error:

| Código | Qué pasó | Cierre |
| --- | --- | --- |
| `token_invalido` | El token del `inicio` no coincide. | WebSocket 4401 |
| `sin_inicio` | Audio (o un mensaje que no es `inicio`) antes de autenticarse. | WebSocket 4400 |
| `formato_audio` | Trama impar o de más de 3200 B. Esa frase se descarta. | — |
| `frase_larga` | Más de 10 s de audio en una frase. | — |
| `inactividad` | Más de 3 s sin tramas con una frase abierta. | — |
| `modo_chat_no_disponible` | El satélite pidió `modo: "chat"`. | — |
| `mensaje_invalido` | JSON roto, mensaje que no es objeto, o tipo desconocido. | — |
| `frase_en_curso` | Un `frase` con otro ya abierto. | — |

El error de autenticación **siempre cierra con 4401 y el mismo mensaje**, sin
decir si el token se parecía o no.

## La gramática

En modo `comandos` el reconocedor se limita a una lista cerrada de frases, la de
`gramatica.json`. Es un archivo de configuración y **no** código: cambiarla es
editar el JSON y reiniciar el servicio.

La lista que no reconoce cae en `[unk]`. Que `[unk]` se publique es correcto:
quien decide qué hacer con una frase que no se entendió es el orquestador, no
este servicio.

Al cargar, el servicio mira si alguna palabra de la gramática no está en el
vocabulario del modelo (Vosk imprime «Ignoring word missing in vocabulary» y la
ignora en silencio). Esas palabras se **avisan por el log**; no se cambian solas.
Una palabra que falta es una frase que nunca se reconocerá como se espera.

## Publicación en MQTT

- Tópico: `casa/sala/voz/texto`, QoS 1, **sin retención**.
- Cuerpo: `{"frase_id","texto","modo","ts","marcas"}`.
  - `ts`: época en segundos (UTC).
  - `marcas`: `primera_trama_ms`, `ultima_trama_ms`, `fin_ms`, `final_ms` y
    `publicado_ms`, en época en milisegundos, con el reloj del servidor. Son las
    mismas que van en el mensaje `final`; sirven para descomponer la latencia
    (cuánto fue red y cuánto reconocimiento).
- `[unk]` o el texto vacío se publican **tal cual**: no se filtra aquí.

El usuario MQTT es `voz` y solo puede escribir ese tópico (ver `acl.conf`). No
puede publicar una alerta ni encender la luz.

## Configuración (variables de entorno)

| Variable | Por defecto | Para qué |
| --- | --- | --- |
| `VOZ_PUERTO` | `2700` | Puerto del WebSocket. |
| `VOZ_DIRECCIONES` | `127.0.0.1 192.168.1.169 100.116.194.116` | Dónde escuchar. **Nunca `0.0.0.0`.** |
| `VOZ_MODELO` | `/modelo/vosk-model-small-es-0.42` | Ruta del modelo (montado en solo lectura). |
| `VOZ_GRAMATICA` | `/app/gramatica.json` | Ruta de la gramática. |
| `VOZ_TOKEN_SHA256` | — | SHA-256 (hex) del token del satélite. **Obligatoria.** |
| `MQTT_HOST` / `MQTT_PUERTO` | `127.0.0.1` / `1883` | Broker. |
| `MQTT_USUARIO` / `MQTT_VOZ_PASSWORD` | `voz` / — | Credenciales MQTT. **Obligatoria** la contraseña. |
| `NIVEL_LOG` | `INFO` | Verbosidad del log. |

El token en claro **no** vive aquí: en el `.env` va solo su SHA-256. El token en
claro lo guarda quien configura el satélite, en un archivo con permisos 600. Así,
ni un `.env` filtrado ni el `docker inspect` entregan un token usable.

## El modelo

No se versiona. Se descarga con:

```bash
servidor/voz/descargar-modelo.sh
```

El guion comprueba peso y SHA-256 antes de descomprimir. El modelo queda en
`servidor/voz/modelos/`, que está en `.gitignore`, y el compose lo monta en
`/modelo` en solo lectura.

## Pruebas

```bash
# desde servidor/
docker compose build voz
docker compose up -d voz
docker compose logs -f voz
```

Comprobar que un mensaje publicado por el usuario `voz` llega (con el usuario
`orquestador`, que sí puede leer el tópico):

```bash
docker run --rm --network host eclipse-mosquitto:2.0.22 \
  mosquitto_pub -h 127.0.0.1 -p 1883 -u voz -P "$MQTT_VOZ_PASSWORD" \
  -t casa/sala/voz/texto -m '{"frase_id":"prueba-1","texto":"hola","modo":"comandos","ts":0,"marcas":{}}'
```

`herramientas/arnes_wav.py` hace la prueba de extremo a extremo: sintetiza frases
con Piper, las manda por WebSocket como si fueran el satélite y mide aciertos y
latencias. Ver su `--help`.
