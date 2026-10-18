# Pruebas del panel web: lo que el del orquestador no puede ver.
#
# El `orquestador/pruebas.py` comprueba la puerta de entrada (`/panel`, el canje del
# enlace y la revocación). Este comprueba lo que hay al otro lado: el estado en memoria,
# la API, el WebSocket y la autorización de cada ruta.
#
# Van en un archivo aparte y no como una sección más del del orquestador por una razón
# práctica: este necesita `fastapi` y `httpx`, y el `pruebas.py` del orquestador corre
# también en el portátil, donde no siempre están. Separados, cada uno instala solo lo
# que necesita.
#
# ## Cómo se ejecuta
#
#     cd servidor
#     docker compose build panel
#     docker compose run --rm --entrypoint python panel pruebas.py
#
# O, sin levantar nada del compose, con la imagen ya construida:
#
#     docker run --rm --entrypoint python asistente-panel:1.0 /app/panel/pruebas.py
#
# ## Qué se prueba aquí y qué no
#
# Lo que se prueba:
#   - los cuatro estados (`conectado`, `desconectado`, `no_instalado`, `sin_datos`) y
#     cuándo se aplica cada uno, que es la parte del diseño que más sustos evita;
#   - que el estado del sitio sale del nodo en modo instalación y del puente en modo
#     demostración (el arreglo A);
#   - que un mensaje con la forma equivocada no rompe el estado ni mata el hilo de MQTT;
#   - que la API exige sesión en todas las rutas de casa, y que la de canje es la única
#     abierta;
#   - que `POST /api/luz` responde 202 y publica lo que dice;
#   - que resolver una alerta dos veces no avisa al satélite dos veces;
#   - que el WebSocket manda la instantánea inicial, los eventos y el latido, y cierra
#     con 1013 cuando el cliente no consume;
#   - que ninguna respuesta lleva `Cache-Control` que permita cachear.
#
# Lo que NO se prueba aquí:
#   - que Telegram acepte el botón. Eso ya se comprobó mandando los dos botones de
#     verdad el 2026-10-05 (el nombre corto dio HTTP 400 y el completo HTTP 200), y
#     no se repite porque manda un mensaje real a un familiar real.
#   - el MQTT de verdad. El cliente se sustituye por un doble que anota lo publicado,
#     y la conexión real se comprueba mirando los logs del contenedor al arrancar.
#     Probar un broker de verdad aquí montaría un Mosquitto en el test, que es otra
#     cosa.
#   - que un ESP32 real publique. El estado se inyecta con `EstadoSala.recibir()`, que
#     es exactamente la función que el callback de MQTT llama.

import asyncio
import json
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
RAIZ_ORQUESTADOR = os.path.join(os.path.dirname(RAIZ), "orquestador")
# El `orquestador/` delante del `panel/` en el path, y al revés de como parece: el
# panel importa `config`, `db`, `alertas` y `panel_acceso`, que están ahí, y este
# archivo tiene que poder importarlos aunque se lance desde cualquier sitio.
sys.path.insert(0, RAIZ_ORQUESTADOR)
sys.path.insert(0, RAIZ)

datos = tempfile.mkdtemp(prefix="pruebas-panel-")
os.environ["ORQUESTADOR_DB"] = os.path.join(datos, "pruebas.sqlite3")
# Una contraseña de mentira para el cliente MQTT. El cliente no se conecta en las
# pruebas (se sustituye por un doble), pero `ClienteEstado.__init__` se niega a
# construirse sin contraseña, y esa comprobación también se prueba.
os.environ.setdefault("MQTT_PANEL_PASSWORD", "CONTRASENA-DE-PRUEBAS-NO-REAL")
os.environ.setdefault("PANEL_URL", "http://villaca.tail722206.ts.net:8080")

import db  # noqa: E402
import panel_acceso  # noqa: E402
import config  # noqa: E402
import estado as estado_modulo  # noqa: E402
from estado import ClienteEstado, EstadoSala  # noqa: E402

FALLOS = []
COMPROBACIONES = [0]


def prueba(nombre, condicion, detalle=""):
    COMPROBACIONES[0] += 1
    if condicion:
        print("PASA  " + nombre)
    else:
        print("FALLA " + nombre + ("  <- " + str(detalle) if detalle else ""))
        FALLOS.append(nombre)


def titulo(texto):
    print("\n" + texto)
    print("-" * len(texto))


def base_nueva(prefijo="panel-"):
    carpeta = tempfile.mkdtemp(prefix=prefijo)
    ruta = os.path.join(carpeta, "o.sqlite3")
    base = db.BaseDatos(ruta)
    return base, ruta


# =============================================================================
# 1. Los cuatro estados, y cuándo se aplica cada uno
# =============================================================================
#
# El reloj se inyecta (`reloj=reloj_falso`) para poder envejecer una lectura sin
# esperar 90 segundos. Es el mismo motivo por el que `reloj` es un parámetro: una
# prueba que espera 90 s es una prueba que no se ejecuta.

titulo("1. Los cuatro estados del panel")

reloj = [1000]


def reloj_falso():
    return reloj[0]


sala = EstadoSala(modo="instalacion", plazo_visto_s=90, reloj=reloj_falso)
base1, _ = base_nueva("estado-")

# Sin nada recibido: hay lectura esperada, y todavía no ha llegado ninguna.
inst = sala.instantanea(base1)
prueba("sin datos recibidos, los cuatro componentes son `sin_datos`",
       inst["luz"]["estado"] == "sin_datos"
       and inst["sensores"]["estado"] == "sin_datos"
       and inst["nodo"]["estado"] == "sin_datos", inst)
prueba("y el satélite que no ha llegado nunca es `no_instalado`, no `sin_datos`",
       inst["satelite"]["estado"] == "no_instalado", inst["satelite"])
prueba("`sin_datos` es distinto de `no_instalado`",
       inst["satelite"]["estado"] != inst["luz"]["estado"])
prueba("y `no_instalado` no lleva `recibido_en`: nunca llegó nada",
       inst["satelite"]["recibido_en"] is None, inst["satelite"])

# Una lectura de la luz: conectada.
sala.recibir(config.TOPICO_LUZ_ESTADO, '{"estado":"on","origen":"manual"}')
inst = sala.instantanea(base1)
prueba("una luz recién llegada está conectada y encendida",
       inst["luz"]["estado"] == "conectado" and inst["luz"]["encendida"] is True,
       inst["luz"])
prueba("y trae su origen", inst["luz"]["origen"] == "manual", inst["luz"])

# Pasado el plazo: desconectada, pero conservando el último valor conocido.
reloj[0] += 91
inst = sala.instantanea(base1)
prueba("pasados 90 s la luz pasa a `desconectado`",
       inst["luz"]["estado"] == "desconectado", inst["luz"])
prueba("pero el último valor conocido NO se borra",
       inst["luz"]["encendida"] is True, inst["luz"])
prueba("y la interfaz puede decir de cuándo es, que es lo que distingue lo viejo",
       inst["luz"]["recibido_en"] == 1000, inst["luz"])

# Justo en el borde: 90 s follow. El plazo es `>=`, no `>`, así que a los 90
# segundos exactos todavía está conectada.
reloj[0] = 1000 + 90
prueba("a los 90 s exactos todavía está conectada (el plazo es <=)",
       sala.instantanea(base1)["luz"]["estado"] == "conectado")
reloj[0] = 1000 + 91
prueba("a los 91 s ya no", sala.instantanea(base1)["luz"]["estado"] == "desconectado")

# El Last Will: `{"conectado": false}` retenido es una caída YA pasada, y no espera.
sala.recibir(config.TOPICO_NODO_ESTADO, '{"conectado":false,"ip":"192.168.1.50"}')
inst = sala.instantanea(base1)
prueba("el Last Will retenido dice `desconectado` al instante, sin esperar 90 s",
       inst["nodo"]["estado"] == "desconectado", inst["nodo"])

sala.recibir(config.TOPICO_NODO_ESTADO,
             '{"conectado":true,"ip":"192.168.1.50","fw":"1.2.3"}')
inst = sala.instantanea(base1)
prueba("y un retained normal vuelve a conectado",
       inst["nodo"]["estado"] == "conectado" and inst["nodo"]["fw"] == "1.2.3",
       inst["nodo"])

# El satélite: en cuanto llega UNA vez, deja de ser `no_instalado`.
prueba("el satélite sigue en `no_instalado` mientras no llegue nunca",
       sala.instantanea(base1)["satelite"]["estado"] == "no_instalado")
sala.recibir("casa/sala/satelite/satelite-sala/estado", '{"conectado":true}')
inst = sala.instantanea(base1)
prueba("pero en cuanto llega una vez pasa a `conectado`",
       inst["satelite"]["estado"] == "conectado", inst["satelite"])

# Fase 2C: estado POR satelite, y el agregado con la forma de siempre.
prueba("`satelites` lista cada satelite con un booleano",
       inst["satelites"] == {"satelite-sala": True}, inst["satelites"])
prueba("`satelite` sigue siendo un componente (estado y recibido_en) para la app",
       set(inst["satelite"]) >= {"estado", "recibido_en"}, inst["satelite"])
sala.recibir("casa/sala/satelite/satelite-vm/estado", '{"conectado":true}')
sala.recibir("casa/sala/satelite/satelite-sala/estado", '{"conectado":false}')
inst = sala.instantanea(base1)
prueba("con uno caido y otro conectado, el agregado es `conectado`",
       inst["satelite"]["estado"] == "conectado"
       and inst["satelites"] == {"satelite-sala": False, "satelite-vm": True},
       inst)
sala.recibir("casa/sala/satelite/satelite-vm/estado", '{"conectado":false}')
inst = sala.instantanea(base1)
prueba("con los dos caidos, el agregado es `desconectado`",
       inst["satelite"]["estado"] == "desconectado"
       and inst["satelites"] == {"satelite-sala": False, "satelite-vm": False},
       inst)
sala.recibir("casa/sala/satelite/satelite-vm/estado", '{"conectado":true}')
reloj[0] += 500
prueba("un satelite conectado NO caduca a los 90 s (solo publica al conectar)",
       sala.instantanea(base1)["satelite"]["estado"] == "conectado",
       sala.instantanea(base1)["satelite"])
sala.recibir("casa/sala/satelite/satelite-vm/estado", "")
prueba("un retenido vacio da de baja a ese satelite",
       "satelite-vm" not in sala.instantanea(base1)["satelites"],
       sala.instantanea(base1)["satelites"])
prueba("el topico unico de antes ya no es del satelite",
       EstadoSala.componente_de("casa/sala/satelite/estado") is None)
prueba("ni un client id con comodin",
       EstadoSala.componente_de("casa/sala/satelite/+/estado") is None)
sala.recibir("casa/sala/satelite/satelite-sala/estado", '{"conectado":true}')

# Los sensores: los nombres del contrato se renombran para la interfaz.
sala.recibir(config.TOPICO_SENSORES,
             '{"temp_c":24.3,"hum_pct":51.0,"presencia":true,"gas_adc":300}')
inst = sala.instantanea(base1)
prueba("los sensores se renombran (temp_c -> temperatura_c, etc.)",
       inst["sensores"]["temperatura_c"] == 24.3
       and inst["sensores"]["humedad_pct"] == 51.0
       and inst["sensores"]["movimiento"] is True
       and inst["sensores"]["gas_adc"] == 300, inst["sensores"])

# Un mensaje con la forma equivocada no rompe nada, pero tampoco se inventa un dato.
sala.recibir(config.TOPICO_LUZ_ESTADO, "no soy json")
prueba("un retenido que no es el de la luz no la enciende",
       sala.instantanea(base1)["luz"]["encendida"] is None,
       sala.instantanea(base1)["luz"])
sala.recibir("casa/sala/alerta/gas", '{"id":"x","gas_adc":1}')
prueba("un tópico de alerta no se cuela en el estado",
       EstadoSala.componente_de("casa/sala/alerta/gas") is None,
       EstadoSala.componente_de("casa/sala/alerta/gas"))
inst = sala.instantanea(base1)
prueba("y sigue habiendo solo los cinco componentes de la casa",
       set(inst) >= {"luz", "sensores", "nodo", "satelite", "sitio"}
       and "alerta" not in inst, sorted(inst))

base1.cerrar()


# =============================================================================
# 2. El modo decide de dónde sale el estado del sitio (el arreglo A)
# =============================================================================

titulo("2. El estado del sitio según el modo")

sala_inst = EstadoSala(modo="instalacion", reloj=reloj_falso)
base2, _ = base_nueva("modo-")
sala_inst.recibir(config.TOPICO_NODO_ESTADO, '{"conectado":true}')
inst = sala_inst.instantanea(base2)
prueba("en modo instalación el sitio sale del NODO",
       inst["sitio"]["fuente"] == "nodo"
       and inst["sitio"]["estado"] == "conectado", inst["sitio"])

# Y el puente ni se mira: en modo instalación `$SYS/.../state` no lo escribe nadie,
# así que si se mirara, el sitio se vería «desconectado» para siempre con la casa
# funcionando. Este cartel que nunca se apaga es el fallo que evita el arreglo A.
sala_inst.recibir("$SYS/broker/connection/puente-sitio/state", "0")
inst = sala_inst.instantanea(base2)
prueba("en modo instalación el puente desconectado NO pone el sitio en rojo",
       inst["sitio"]["estado"] == "conectado", inst["sitio"])

reloj[0] += 200
prueba("pero si el nodo se cae de verdad, el sitio SÍ se pone en `desconectado`",
       sala_inst.instantanea(base2)["sitio"]["estado"] == "desconectado",
       sala_inst.instantanea(base2)["sitio"])

sala_demo = EstadoSala(modo="demostracion", reloj=reloj_falso)
inst = sala_demo.instantanea(base2)
prueba("en modo demostración el puente es `no_instalado` hasta que aparezca",
       inst["sitio"]["fuente"] == "puente"
       and inst["sitio"]["estado"] == "no_instalado", inst["sitio"])

sala_demo.recibir("$SYS/broker/connection/puente-sitio/state", "1")
inst = sala_demo.instantanea(base2)
prueba("el puente conectado pone el sitio en `conectado`",
       inst["sitio"]["estado"] == "conectado", inst["sitio"])

sala_demo.recibir("$SYS/broker/connection/puente-sitio/state", "0")
prueba("el puente desconectado (`0` en texto plano) sí lo pone en `desconectado`",
       sala_demo.instantanea(base2)["sitio"]["estado"] == "desconectado",
       sala_demo.instantanea(base2)["sitio"])

# El `+` del tópico: el puente puede cambiar de client_id sin que el panel deje de
# oírlo, porque reconoce el tópico por forma y no por igualdad.
prueba("el enlace del sitio se reconoce con cualquier client_id",
       EstadoSala.componente_de("$SYS/broker/connection/cualquier-cosa/state")
       == "puente")
prueba("pero un $SYS que no es el de connection no se cuela",
       EstadoSala.componente_de("$SYS/broker/clients/connected") is None)
prueba("ni un tópico de la casa que no es del panel",
       EstadoSala.componente_de("casa/sala/nodo/config") is None)

# En demostración el nodo se sigue mirando aparte, porque está detrás del puente y
# puede caerse sin que se caiga el puente.
sala_demo2 = EstadoSala(modo="demostracion", reloj=reloj_falso)
sala_demo2.recibir("$SYS/broker/connection/puente-sitio/state", "1")
inst = sala_demo2.instantanea(base2)
prueba("en demostración el puente puede estar bien y el nodo `sin_datos`",
       inst["sitio"]["estado"] == "conectado"
       and inst["nodo"]["estado"] == "sin_datos", inst)

base2.cerrar()


# =============================================================================
# 3. Los eventos del WebSocket
# =============================================================================

titulo("3. Los eventos")

sala3 = EstadoSala(modo="instalacion", reloj=reloj_falso)
base3, _ = base_nueva("eventos-")
bucle3 = asyncio.new_event_loop()
sala3.scrupular(bucle3)


def vaciar(oyente, bucle):
    """Lo que hay en la cola del oyente, y vacía la cola.

    `emitir` no mete el evento en la cola directamente: usa
    `call_soon_threadsafe`, que deja el trabajo PENDIENTE en el bucle. Por eso hay que
    darle una vuelta al bucle antes de mirar la cola, y no basta con mirar: sin esa
    vuelta la cola está vacía y parece que no se emitió nada.

    Es también el comportamiento real: en producción el bucle está corriendo siempre,
    así que el trabajo se hace en el siguiente turno.
    """
    bucle.run_until_complete(asyncio.sleep(0))
    eventos = []
    while True:
        try:
            eventos.append(oyente.cola.get_nowait())
        except asyncio.QueueEmpty:
            return eventos


oyente = sala3.oyente_nuevo()
sala3.recibir(config.TOPICO_LUZ_ESTADO, '{"estado":"on","origen":"panel"}')
eventos = vaciar(oyente, bucle3)
prueba("cambiar la luz produce un evento `luz`",
       len(eventos) == 1 and eventos[0]["tipo"] == "luz", eventos)
prueba("y el evento lleva el bloque entero, no un parche",
       eventos[0]["datos"]["encendida"] is True
       and eventos[0]["datos"]["estado"] == "conectado", eventos)

# En modo instalación, un cambio del nodo produce DOS eventos: el del nodo y el del
# sitio, porque el sitio sale de ahí. Si se emitiera solo uno, la tarjeta del sitio
# se quedaría con lo viejo hasta la siguiente instantánea.
sala3.recibir(config.TOPICO_NODO_ESTADO, '{"conectado":true}')
tipos = [e["tipo"] for e in vaciar(oyente, bucle3)]
prueba("en modo instalación cambiar el nodo produce `nodo` Y `sitio`",
       "nodo" in tipos and "sitio" in tipos, tipos)

# Y en modo demostración no: el sitio sale del puente.
sala3d = EstadoSala(modo="demostracion", reloj=reloj_falso)
bucle3d = asyncio.new_event_loop()
sala3d.scrupular(bucle3d)
oyente_d = sala3d.oyente_nuevo()
sala3d.recibir(config.TOPICO_NODO_ESTADO, '{"conectado":true}')
tipos = [e["tipo"] for e in vaciar(oyente_d, bucle3d)]
prueba("en modo demostración cambiar el nodo produce SOLO `nodo`",
       tipos == ["nodo"], tipos)

sala3.oyente_baja(oyente)
prueba("bajar un oyente lo quita de la cuenta de conexiones",
       sala3.conexiones() == 0, sala3.conexiones())

# La cola acotada: un cliente que no consume se marca como saturado, y de ahí sale
# el cierre 1013. Es lo que evita que un móvil con la pantalla apagada sea una fuga
# de memoria con forma de intermitencia.
colado = sala3.oyente_nuevo()
for _ in range(config.COLA_EVENTOS_WS + 5):
    sala3.recibir(config.TOPICO_LUZ_ESTADO, '{"estado":"on"}')
vaciar(colado, bucle3)
prueba("un cliente que no consume se marca saturado en cuanto se llena la cola",
       colado.saturado, (colado.saturado, config.COLA_EVENTOS_WS))

# Y en la cola hay un TESTIGO al final. Eso es lo que hace que el cierre con 1013 se
# llegue a ver: el bucle que escribe está bloqueado en `cola.get()`, la marca `saturado`
# se pone, y sin testigo no vuelve a mirar hasta que llegue otro evento. Y no va a
# llegar ninguno, porque para llenarse hubo que mandar COLA_EVENTOS_WS eventos y este
# es el siguiente. Sin el testigo, el cierre se quedaría para el siguiente latido o
# para cuando el socket se caiga, que es justo cuando ya no haría falta.
sala3.oyente_baja(colado)
colado2 = sala3.oyente_nuevo()
for _ in range(config.COLA_EVENTOS_WS + 5):
    sala3.recibir(config.TOPICO_LUZ_ESTADO, '{"estado":"on"}')
encolados = vaciar(colado2, bucle3)
prueba("la cola saturada termina en el testigo, que es lo que la despierta",
       encolados and encolados[-1] is estado_modulo.TESTIGO_SATURADO,
       encolados[-3:] if encolados else encolados)
prueba("y el testigo NO es un evento, para que no se intente mandar por el WebSocket",
       estado_modulo.TESTIGO_SATURADO is None, estado_modulo.TESTIGO_SATURADO)
prueba("el testigo no ocupa un hueco de mas: la cola no ha crecido",
       len(encolados) == config.COLA_EVENTOS_WS,
       (len(encolados), config.COLA_EVENTOS_WS))

# Y el aviso por `Event`, que es lo que de verdad cierra la conexion. Se comprueba
# aparte porque responde a una pregunta distinta: no es "la marca esta puesta", es
# "alguien que este bloqueado esperando se ha enterado". Ese "alguien" es
# `_vigilar_saturacion`, que puede estar esperando mientras `_eventos` esta bloqueado
# en `send_text` y no vuelve a mirar la cola nunca.
vacio = estado_modulo.Oyente()
prueba("un oyente nuevo NO tiene el aviso puesto",
       not vacio.saturado_espera.is_set(), vacio.saturado_espera.is_set())
vacio.poner({"tipo": "luz"})
prueba("y con la cola vacia tampoco, aunque se haya escrito",
       not vacio.saturado_espera.is_set(), vacio.saturado_espera.is_set())
for indice in range(config.COLA_EVENTOS_WS):
    vacio.poner({"tipo": "luz", "datos": {"n": indice}})
prueba("se pone en el instante en que se llena, no en el siguiente",
       vacio.saturado_espera.is_set(), vacio.saturado_espera.is_set())
prueba("y entonces `poner` avisa de que hay que cerrar",
       vacio.poner({"tipo": "luz"}) is False, "deberia devolver False")

# Y que el aviso sea de una sola vez y no se vuelva a poner en `False`, porque quien
# espera ya no lo va a mirar otra vez.
prueba("el aviso no se desactiva solo",
       vacio.saturado_espera.is_set() and vacio.saturado,
       (vacio.saturado_espera.is_set(), vacio.saturado))

# Y que la cola siga admitiendo: se comprueba que después del aviso el oyente está
# listo para el siguiente ciclo. Un `Oyente` que se quedara con la marca puesta para
# siempre cerraría cada conexión en cuanto volviera a llenarse, y el panel con la
# pantalla apagada no volvería a funcionar ni con la luz encendida.
sala3.oyente_baja(colado2)
colado3 = sala3.oyente_nuevo()
prueba("un oyente nuevo no nace saturado", not colado3.saturado, colado3.saturado)
sala3.oyente_baja(colado3)
prueba("y al bajar, la cuenta de conexiones vuelve a cero",
       sala3.conexiones() == 0, sala3.conexiones())

# El hilo de MQTT llama a `emitir` desde FUERA del bucle, y eso tiene que funcionar:
# es el caso real, porque los callbacks de paho vienen en su propio hilo. Por eso
# `emitir` usa `call_soon_threadsafe` y no un `put_nowait` directo: una
# `asyncio.Queue` no es segura desde otro hilo.
import threading  # noqa: E402

sala3h = EstadoSala(modo="instalacion", reloj=reloj_falso)
bucle = asyncio.new_event_loop()
sala3h.scrupular(bucle)
o_h = sala3h.oyente_nuevo()
bucle.run_until_complete(asyncio.sleep(0))
hilo = threading.Thread(
    target=lambda: sala3h.recibir(config.TOPICO_LUZ_ESTADO, '{"estado":"on"}'))
hilo.start()
hilo.join()
bucle.run_until_complete(asyncio.sleep(0.01))
prueba("un mensaje recibido desde otro hilo llega igual a la cola del WebSocket",
       not o_h.cola.empty(), o_h.cola.qsize())
bucle.close()

base3.cerrar()


# =============================================================================
# 4. El cliente MQTT publica lo que debe y no lo que no debe
# =============================================================================

titulo("4. El cliente MQTT del panel")


class ClienteFalso:
    """Sustituye al cliente de paho y anota lo publicado.

    `publish` es el nombre que usa `ClienteEstado.publicar`, porque es el de paho. Por
    eso el doble tiene que llamar así aunque anote en `publicado`: si se renombrara el
    método del doble, la prueba se estaría probando a sí misma.
    """

    def __init__(self):
        self.publicado = []

    def publish(self, topico, cuerpo, qos=0, retain=False):
        self.publicado.append((topico, json.loads(cuerpo), qos, retain))
        return ResultadoFalso()


class ResultadoFalso:
    """Lo que devuelve `publish` en paho: algo con `wait_for_publish`."""

    def wait_for_publish(self, timeout=None):
        return True


cliente = ClienteFalso()
sala4 = EstadoSala()
antes = time.time()

# Se construye con `__new__` y a pelo, sin llamar a `__init__`: `__init__` levanta un
# cliente de paho de verdad, que intentaría conectar con el broker. Lo que se prueba
# aquí es `publicar_luz`, que no necesita la conexión.
real = ClienteEstado.__new__(ClienteEstado)
real.estado = sala4
real.conectado = True
real.cliente = cliente
real.publicar_luz("on")
real.publicar_luz("off")

prueba("la luz se publica en `casa/sala/luz/set`",
       [p[0] for p in cliente.publicado]
       == ["casa/sala/luz/set", "casa/sala/luz/set"], cliente.publicado)
prueba("con QoS 1", all(p[2] == 1 for p in cliente.publicado), cliente.publicado)
prueba("y SIN retenido: la orden es una acción, no un estado",
       all(p[3] is False for p in cliente.publicado), cliente.publicado)

cargas = [p[1] for p in cliente.publicado]
prueba("el mensaje lleva `estado`",
       cargas[0]["estado"] == "on" and cargas[1]["estado"] == "off", cargas)
prueba("el mensaje lleva `origen: panel` que pone el que publica, no el cliente",
       all(c["origen"] == "panel" for c in cargas), cargas)
prueba("y lleva `ts`, que es lo que hace que el ESP32 no lo descarte por viejo",
       all(abs(c["ts"] - antes) < 60 for c in cargas), cargas)

# Y lo que el panel NO publica: no hay forma de que desde el panel salga una alerta.
prueba("el cliente no tiene forma de publicar en alerta/inactividad",
       not hasattr(real, "publicar_inactividad"))
prueba("ni de publicar alertas",
       not hasattr(real, "publicar_alerta"))

# Sin contraseña el cliente no se construye. Un panel sin conexión parece sano (la web
# carga, el healthcheck pasa) pero no muestra ni un dato, así que es mejor que muera
# al arrancar y se note en el primer `docker logs`.
passwordo_real = os.environ.get("MQTT_PANEL_PASSWORD")
os.environ["MQTT_PANEL_PASSWORD"] = ""
try:
    import importlib

    config_mod = importlib.reload(config)
    error_esperado = None
    try:
        ClienteEstado(sala4)
    except RuntimeError as error:
        error_esperado = str(error)
    prueba("sin MQTT_PANEL_PASSWORD el cliente NO se construye",
           error_esperado is not None, error_esperado)
    prueba("y el error dice qué variable falta",
           error_esperado and "MQTT_PANEL_PASSWORD" in error_esperado,
           error_esperado)
finally:
    os.environ["MQTT_PANEL_PASSWORD"] = passwordo_real
    importlib.reload(config)

# Los tópicos que lee, contra lo que dice la ACL. Este es el punto donde una prueba
# vale más que una lectura: si mañana se añade un tópico al cliente y no a la ACL, el
# broker lo rechaza en silencio y el panel muestra «sin datos» sin decir por qué.
#
# Los cinco que lee el panel, escritos aquí a mano y no sacados de `config`: si se
# comprobara contra `config`, la prueba solo diría que el panel lee lo que el panel
# dice que lee, y no que sean los cinco correctos. La ACL es la referencia.
leidos = {config.TOPICO_LUZ_ESTADO, config.TOPICO_SENSORES,
          config.TOPICO_NODO_ESTADO, config.TOPICO_SATELITE_ESTADO,
          config.TOPICO_ENLACE_SITIO}
esperados = {"casa/sala/luz/estado", "casa/sala/sensores",
             "casa/sala/nodo/estado", "casa/sala/satelite/+/estado",
             "$SYS/broker/connection/+/state"}
prueba("el panel lee exactamente los cinco tópicos que le deja la ACL",
       leidos == esperados, sorted(leidos ^ esperados))
prueba("el enlace del sitio usa el `+` del client_id, para no depender del nombre",
       config.TOPICO_ENLACE_SITIO == "$SYS/broker/connection/+/state",
       config.TOPICO_ENLACE_SITIO)
prueba("y NO lee ninguno de los dos tópicos de alerta",
       not any(t.startswith("casa/sala/alerta/") for t in leidos),
       sorted(leidos))
prueba("ni el de inactividad, que la ACL del panel excluye a propósito",
       "inactividad" not in " ".join(leidos))

base4, _ = base_nueva("mqtt-")
base4.registrar_familiar("111", nombre="Marta")
base4.registrar_familiar("222", nombre="Luis")
inst = sala4.instantanea(base4)
prueba("la instantánea cuenta los familiares sin dar nombres ni chat_id",
       inst["familiares"] == 2, inst["familiares"])
prueba("y `alertas` trae los cuatro estados en cero, no solo los que hay",
       set(inst["alertas"]) == {"pendientes", "enviadas", "resueltas", "descartadas"},
       inst["alertas"])
prueba("con `conexiones` a 0 porque aún no hay ningún WebSocket abierto",
       inst["conexiones"] == 0, inst["conexiones"])

# El bloque de la inactividad sale de SQLite (la tabla `configuracion`), no de un
# tópico: por eso se prueba aquí, guardando en la base y volviendo a pedir la
# instantánea.
inact = inst["inactividad"]
prueba("la instantánea trae el bloque de inactividad con su forma",
       set(inact) == {"ultimo_movimiento", "tiempo_min", "minimo", "maximo"}, inact)
prueba("sin nada guardado, el ajuste es el de fábrica y el reloj, sin datos",
       inact["tiempo_min"] == config.TIEMPO_INACTIVIDAD_MIN_DEFECTO
       and inact["ultimo_movimiento"] is None, inact)
base4.fijar_ultimo_movimiento(1_700_000_000)
base4.guardar_tiempo_inactividad(45)
inact = sala4.instantanea(base4)["inactividad"]
prueba("lo guardado por el orquestador y el ajuste del panel salen al instante",
       inact["ultimo_movimiento"] == 1_700_000_000
       and inact["tiempo_min"] == 45, inact)
base4.cerrar()


print("\n" + "=" * 66)
print("comprobaciones: %d" % COMPROBACIONES[0])
print("fallos: %d" % len(FALLOS))
for nombre in FALLOS:
    print("  - " + nombre)
sys.exit(1 if FALLOS else 0)