#pragma once

// TODO lo que NO es secreto del firmware del nodo ESP32.
//
// Regla de 3.4: este archivo se versiona y no puede contener ninguna credencial.
// Si alguna vez hace falta escribir un secreto aqui, es que ese valor no va en
// config.h: va en secrets.h, que NO se versiona.
//
// El unico valor que si es un secreto y por eso NO esta aqui es la contrasena del
// broker, el password del Wi-Fi y el usuario. Ver include/secrets.h.

// ---------------------------------------------------------------------------
// Placa
// ---------------------------------------------------------------------------
#define PLACA_NOMBRE "nodemcu-32s"
#define GPIO_PIN_LED 2
#define GPIO_PIN_BOTON_BOOT 0
#define GPIO_PIN_BOTON_EXTERNO 33

// ---------------------------------------------------------------------------
// Pines de salida. Ninguno es pin de strapping.
// El rele y el buzzer van a modulos con transistor: nunca el componente directo
// sobre el pin, porque el GPIO no da corriente suficiente.
// ---------------------------------------------------------------------------
#define GPIO_PIN_RELE 26
#define GPIO_PIN_BUZZER 27

// ---------------------------------------------------------------------------
// Pines de sensor. Cerrados por el usuario como ajuste final.
// ---------------------------------------------------------------------------
// MQ-2: ADC1, solo entrada, atenuacion de 11 dB segun el plan.
// GPIO 33 quedo descartado porque lo ocupa el boton externo.
#define GPIO_PIN_GAS_ADC 34
#define ADCN_GAS ADC1_CHANNEL_6  // GPIO 34 en el ESP32

// DHT22: sensor de un solo hilo con su propio protocolo. NO es I2C, por eso el
// nombre GPIO_PIN_DHT22 y no GPIO_PIN_SENSOR_I2C.
#define GPIO_PIN_DHT22 4

// PIR: salida digital del sensor que el ESP32 lee como entrada.
#define GPIO_PIN_SENSOR_PIR 35

// El hardware electrico todavia no existe (6.7), pero los pines quedan cableados
// y prontos. Cambiar estas dos banderas no toca codigo.
#define BUZZER_HABILITADO true
#define RELE_HABILITADO false

// El boton externo queda desactivado por defecto hasta que llegue el hardware.
#define BOTON_EXTERNO_HABILITADO false

// El LED integrado del clon puede ser activo a nivel bajo o alto (6.6).
// La NodeMCU-32S lo enciende en ALTO (GPIO 2 = 1), asi que se deja en false.
// Se confirma a la vista en la primera prueba fisica: si quedara invertido,
// se vuelve a true y no hace falta tocar ningun codigo.
#define LED_ACTIVO_EN_NIVEL_BAJO false

// ---------------------------------------------------------------------------
// Identidad
// ---------------------------------------------------------------------------
// Client id del broker. FIJO, sin partes aleatorias ni derivadas del MAC ni del
// hostname: es el que pide el contrato. Con uno aleatorio, cada reinicio dejaria
// una sesion abierta en el broker durante 2 dias (persistent_client_expiration).
//
// Consecuencia asumida: solo puede haber un nodo por broker con este id. Si algum
// dia hubiera dos ESP32, el segundo expulsaria al primero de su sesion MQTT. El
// nodo simulado de Python usa "nodo-simulado" por eso.
#define MQTT_CLIENT_ID_BASE "nodo-sala"
#define MQTT_PORTO 1883

// Espera creciente entre intentos de ARRANQUE del cliente MQTT, que es lo unico
// que este firmware controla: una vez que ESP32MQTTClient arranca, la libreria
// reconecta sola con su propio mecanismo.
//
// Antes el reintento era cada vuelta del loop (50 ms). Con el broker todavia sin
// resolver eso son 20 lineas de ERROR por segundo, y el buffer del monitor serie
// se llena en menos de un minuto. Con esto son 2, 4, 8, 16, 30, 30... segundos,
// y se vuelve a 2 s en cuanto el arranque tiene exito.
#define MQTT_REINTENTO_INICIAL_MS 2000UL
#define MQTT_REINTENTO_MAXIMO_MS 30000UL

// Zona horaria de la casa. El firmware guarda UTC y convierte al publicar.
#define ZONA_HORARIA "America/Mazatlan"

// ---------------------------------------------------------------------------
// Nombres de topico del contrato. Centralizados para no repetirlos en el codigo.
// ---------------------------------------------------------------------------
#define TOPIC_RAIZ "casa/sala/"
#define TOPIC_LUZ_ESTADO "casa/sala/luz/estado"
#define TOPIC_LUZ_SET "casa/sala/luz/set"
#define TOPIC_SENSORES "casa/sala/sensores"
#define TOPIC_NODO_ESTADO "casa/sala/nodo/estado"
#define TOPIC_NODO_CONFIG "casa/sala/nodo/config"
#define TOPIC_PRUEBA_GAS "casa/sala/nodo/prueba_gas"
#define TOPIC_ALERTA_GAS "casa/sala/alerta/gas"
#define TOPIC_ALERTA_AYUDA "casa/sala/alerta/ayuda"

// Claves del JSON, para que el typo se note en un solo lugar.
#define JSON_CLAVE_ID "id"
#define JSON_CLAVE_TS "ts"
#define JSON_CLAVE_ESTADO "estado"
#define JSON_CLAVE_ORIGEN_VOZ "origen"
#define JSON_CLAVE_TEMP "temp_c"
#define JSON_CLAVE_HUM "hum_pct"
#define JSON_CLAVE_GAS "gas_adc"
#define JSON_CLAVE_PRESENCIA "presencia"
#define JSON_CLAVE_ORIGEN "origen"

// ---------------------------------------------------------------------------
// QoS. Sale de la tabla del plan. El ESP32 solo publica con QoS 1 en las alertas.
// ---------------------------------------------------------------------------
#define QOS_LUZ_ESTADO 0
#define QOS_SENSORES 0
#define QOS_NODO_ESTADO 0
#define QOS_LUZ_SET_SUSCRIPCION 1
#define QOS_NODO_CONFIG_SUSCRIPCION 1
#define QOS_PRUEBA_GAS_SUSCRIPCION 1
#define QOS_ALERTA_GAS 1
#define QOS_ALERTA_AYUDA 1

// ---------------------------------------------------------------------------
// Tiempos del contrato. Los tres primeros salen del plan; el resto es el margen
// que hace falta para que las lecturas del DHT22 (que es lento) no encimen.
// ---------------------------------------------------------------------------
#define MS_1S 1000UL
#define MS_5S (5UL * MS_1S)
#define MS_10S (10UL * MS_1S)
#define MS_60S (60UL * MS_1S)
#define MS_3MIN (180UL * MS_1S)

// Gas: el episodio abre con la lectura mayor o igual al umbral de apertura, y
// cierra cuando la lectura baja del umbral de CIERRE durante
// GAS_CIERRE_SOSTENIDO_S.
//
// Los dos umbrales son numeros propio, y el de cierre no se calcula como «el
// umbral menos 150». Antes se hacia, y quedaba en 1650 en vez de los 1550 del
// contrato: un numero que no sale de ningun lado y que el nodo/config no puede
// cambiar. Con un valor derivado, cambiar el umbral de apertura movia el de
// cierre sin querer, y dos maquinas con distinto gas_on_adc cerraban distinto.
// Ahora son los dos del contrato y los dos los puede fijar nodo/config.
//
// El plan dice que el aviso sale "a los ~3 s, con 3 lecturas seguidas" (5.9), asi
// que el muestreo del MQ-2 es de 1 Hz. No puede ser la cadencia del loop (50 ms):
// a 50 ms las tres lecturas se contarian en 150 ms y un pico de ruido abriria un
// episodio.
#define PERIODO_LECTURA_GAS_MS MS_1S
#define GAS_UMBRAL_APERTURA 1800
#define GAS_UMBRAL_CIERRE 1550
#define GAS_LECTURAS_PARA_ABRIR 3
#define GAS_CIERRE_SOSTENIDO_S 10

// Rango del ADC del MQ-2 con atenuacion de 11 dB. Es el mismo rango que acepta el
// orquestador (servidor/orquestador/config.py, ADC_MINIMO..ADC_MAXIMO) y el mismo
// que comprueba nodo_simulado/contrato.py. Si cambia en un sitio, cambia en los
// tres: el nodo no puede publicar un valor que el servidor vaya a rechazar.
#define GAS_ADC_MAXIMO 4095

// Luz. El plan define luz/set con {"estado":"on"|"off","origen":...,"ts":epoch} y
// luz/estado con {"estado":"on"|"off","origen":...}. El ESP32 hace eco del origen
// que traia la orden y no lo inventa; si falta o no es valido, usa "panel", porque
// el unico que publica luz/set es el servidor.
#define LUZ_ESTADO_ON "on"
#define LUZ_ESTADO_OFF "off"
#define LUZ_ORIGEN_VOZ "voz"
#define LUZ_ORIGEN_ALEXA "alexa"
#define LUZ_ORIGEN_PANEL "panel"
#define LUZ_ORIGEN_BOTON "boton"

// Antiguedad maxima de una orden de luz. El plan (Fase 4, "Regla de antiguedad") y
// la propuesta v3 (2.3) ponen 10 segundos: se descarta lo que tenga mas, y tambien
// lo que no traiga ts.
#define ORDEN_MAX_ANTIGUEDAD_S 10

// Alerta de ayuda: antirrebote en el ESP32. La deduplicacion de 60 s que se
// aplicaba por voz se movio al servidor, porque el ESP32 ya no se suscribe a
// casa/sala/alerta/ayuda (correccion 4).
#define AYUDA_ANTIRREBOTE_S 60

// Boton: antirrebote. GPIO 33 es pull-up, asi que se espera el flanco de bajada.
// El plan pide ignorar los cambios de menos de 50 ms y exigir el estado estable en
// dos lecturas seguidas, con un solo toggle por pulsacion (2.5).
#define BOTON_ANTIRREBOTE_MS 50
#define BOTON_PULSO_MIN_MS 40
#define BOTON_ESTABLE_LECTURAS 2

// ---------------------------------------------------------------------------
// Frecuencia de publicacion
// ---------------------------------------------------------------------------
// luz/estado se publica al CAMBIAR, no en un latido: es un retained que solo
// interesa cuando hay novelty (regla "un solo estado con dos caminos", 2.3).
// sensores y nodo/estado si tienen latido, porque son estado vivo del nodo.
#define PERIODO_SENSORES_MINIMO_MS MS_10S
#define PERIODO_SENSORES_LATIDO_MS MS_60S
#define PERIODO_NODO_ESTADO_MS MS_60S

// ---------------------------------------------------------------------------
// Simulacion. Los sensores reales todavia no estan (6.7), asi que las lecturas
// las genera este modulo. Cuando llegue el hardware se sustituyen las funciones
// de lectura de sensores.cpp y no se toca nada mas.
// El gas simulado es una CONSTANTE en rango de aire limpio. Dos versiones
// anteriores lo hicieron mal y las dos se corrigieron en la v3 (6.16):
//   - fluctuaba entre 1400 y 2200 y cruzaba el umbral de 1800 por su cuenta, con
//     alertas falsas en el firmware normal;
//   - luego oscilaba entre 250 y 450. Eso ya no daba alertas falsas, pero si
//     llenaba el topic casa/sala/sensores, porque la regla de frecuencia compara
//     gas_adc con un margen de 50 y cada lectura se salia del margen.
// Con un valor fijo la simulacion no publica nada por si sola. El unico camino
// para moverlo es casa/sala/nodo/prueba_gas.
// ---------------------------------------------------------------------------
#define SIM_GAS_POR_DEFECTO 312
#define SIM_TEMP_BASE_C 24.5
#define SIM_TEMP_VARIACION_C 0.5
#define SIM_HUM_BASE_PCT 60
#define SIM_HUM_VARIACION_PCT 3

// La presencia alterna sola cada 3 minutos, independiente de prueba_gas (correccion 8).
#define SIM_PRESENCIA_PERIODO_S 180

// ---------------------------------------------------------------------------
// Frecuencia del latido de estado
// ---------------------------------------------------------------------------
#define NODO_ESTADO_CAMPO_VERSION "1.0.0"

// Nivel del log propio, con los mismos valores que el enum de registro.h:
// 0 = Error, 1 = Aviso, 2 = Info, 3 = Depurar. Info es el defecto porque ahi sale
// todo lo que hay que ver en una demo, y Depurar mete una linea por cada lectura.
//
// Se cambia por build_flags, no tocando el codigo:
//   pio run -t upload --target-option="build_flags=-DPRUEBAS=1 -DNIVEL_LOG=3"
#ifndef NIVEL_LOG
#define NIVEL_LOG 2
#endif
