"""Comprueba que la ACL del usuario `panel` es lo que dice `acl.conf`, y ni una linea mas.

QUE COMPRUEBA
  - Que `panel` PUEDE publicar en sus dos unicos topicos de escritura: `luz/set` y
    `satelite/decir`.
  - Que NO puede publicar en los cinco que la ACL le recorta, ni en nada fuera de
    `casa/sala/`.
  - (Fase 2C, si estan MQTT_SATELITE_PASSWORD y MQTT_ORQUESTADOR_PASSWORD) Que
    `satelite` escribe su estado en `casa/sala/satelite/<client_id>/estado` y ya
    no en el topico unico de antes, que no escribe `satelite/decir`, y que
    `panel` y `orquestador` leen `casa/sala/satelite/+/estado`.

POR QUE NO SE COMPRUEBA EL SUBACK
  El SUBACK no sirve para esto. Mosquitto concede la suscripcion con el QoS pedido y
  filtra despues, en la entrega: se comprobo que devuelve 1 incluso para `otro/tema`,
  con un usuario que no tiene permiso para nada en ese topico. Un SUBACK da igual a
  "concedido", asi que un script que se fiara de el daria verde a una ACL vacia.

  Tampoco sirve mirar si `publish()` lanza excepcion: no lanza nunca. El broker acepta
  la conexion y descarta el mensaje en silencio. Por eso lo que se mide es si un
  oyente AUTORIZADO recibe el mensaje.

POR QUE EL OYENTE ES `puente` Y NO `panel`
  `puente` tiene `casa/#` en lectura y escritura, asi que ve todo lo de la casa. Quien
  publica es `panel`, con sus permisos de verdad. Si el mensaje llega, es que `panel` lo
  publico, porque los permisos del oyente no cambian durante la prueba.

  El topico `casa/otra/luz/set` se comprueba con este mismo metodo a proposito: esta
  fuera de `casa/sala/` y, aun asi, `puente` lo ve porque tiene `casa/#`. Es el caso
  que un `casa/sala/#` bien puesto no dejaria pasar.

COMO SE USA
  Necesita un broker Mosquitto con la ACL y el `passwd` de este repositorio. NO se usa
  el de produccion (1883): este script publica mensajes de prueba y hay que reiniciar el
  broker para limpiar los retenidos. Apuntar a uno de prueba:

      D=/tmp/broker-prueba
      mkdir -p $D
      cp /ruta/a/secretos/mosquitto/passwd $D/
      cp mosquitto/config/acl.conf $D/
      cat > $D/mosquitto.conf <<'FIN'
      listener 18830 127.0.0.1
      allow_anonymous false
      password_file /mosquitto/prueba/passwd
      acl_file /mosquitto/prueba/acl.conf
      persistence false
      FIN
      docker run -d --name broker-prueba --network host --user 1883:1883 \
          --group-add 1000 -v $D:/mosquitto/prueba:ro eclipse-mosquitto:2.0.22 \
          mosquitto -c /mosquitto/prueba/mosquitto.conf

      MQTT_PUERTO=18830 MQTT_PANEL_PASSWORD=... MQTT_PUENTE_PASSWORD=... \
          [MQTT_SATELITE_PASSWORD=... MQTT_ORQUESTADOR_PASSWORD=...] \
          python panel/pruebas_acl.py

  Las contrasenas salen del entorno, nunca de la linea de comandos ni de aqui dentro.
"""
import json
import os
import sys
import time

# El `sys.path` se antepone con una ruta calculada y no con una fija de contenedor: esto
# tambien se ejecuta desde el repo, no solo desde la imagen del panel.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "orquestador"))

import paho.mqtt.client as mqtt  # noqa: E402


def _falta(nombre):
    sys.exit("falta la variable de entorno %s. Este script no lleva contrasenas dentro "
             "y no las pide por linea de comandos, para que no salgan en `ps` ni en el "
             "historial de bash." % nombre)


def exige_entorno_prueba() -> None:
    """Las pruebas ACL no leen servidor/.env y no tocan el broker de produccion."""
    if PUERTO == 1883:
        sys.exit("ERROR: puerto 1883 es de produccion; use un broker de prueba.")


def _clave_prueba(nombre_generico):
    return (os.environ.get("PRUEBA_" + nombre_generico)
            or os.environ.get(nombre_generico)
            or _falta("PRUEBA_" + nombre_generico))


PUERTO = int(os.environ.get("MQTT_PUERTO", "18830"))
HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
exige_entorno_prueba()
CLAVE_PANEL = _clave_prueba("MQTT_PANEL_PASSWORD")
CLAVE_PUENTE = _clave_prueba("MQTT_PUENTE_PASSWORD")
# Opcionales: sin ellas se salta la parte de la Fase 2C.
CLAVE_SATELITE = os.environ.get("MQTT_SATELITE_PASSWORD", "")
CLAVE_ORQUESTADOR = os.environ.get("MQTT_ORQUESTADOR_PASSWORD", "")

if PUERTO == 1883:
    # El de produccion, no. Este script publica mensajes de prueba, deja retenidos que
    # no se pueden borrar sin reiniciar el broker, y ademas el `satelite/decir` haria
    # hablar al sitio. Por eso se comprueba el puerto y se sale.
    sys.exit("MQTT_PUERTO=1883 es el broker de produccion. Levanta uno de prueba: "
             "la cabecera de este archivo explica como.")

CUERPO = json.dumps({"estado": "on", "origen": "prueba-acl"})
SEGUNDOS_ESPERA = 5


def _id(prefijo, semilla):
    """Un client_id unico y sin caracteres raros.

    El sufijo va en numero y no en el topic entero porque Mosquitto solo acepta
    hasta 23 caracteres de client_id en MQTT 3.1.1, y `casa/sala/nodo/config` con un
    prefijo se pasa de largo. Que se trunque es lo que hace el broker; aqui se acorta
    antes para no depender de esa recorte.
    """
    return (prefijo + str(abs(hash(semilla)) % 100000000))[:23]


def _oyente(clave, topico, recibidos, usuario="puente"):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=_id("oy", topico + clave))
    c.username_pw_set(usuario, clave)
    c.on_message = lambda cli, usuario, mensaje: recibidos.append(mensaje.topic)
    c.connect(HOST, PUERTO, 30)
    c.subscribe(topico, 1)
    c.loop_start()
    return c


def _publica_como_panel(clave, topico, usuario="panel"):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=_id("em", topico + clave))
    c.username_pw_set(usuario, clave)
    c.connect(HOST, PUERTO, 30)
    c.loop_start()
    # El `sleep` antes de publicar es lo que da tiempo a que la suscripcion del oyente
    # este ya de pie en el broker. Sin el, el mensaje sale antes de que exista quien
    # pueda oirlo y la prueba daria "rechazado" para un topico que si permite.
    time.sleep(0.5)
    c.publish(topico, CUERPO, qos=1)
    time.sleep(1.0)
    c.loop_stop()
    c.disconnect()


def entregado(topico, emisor=("panel", None), oyente_de=("puente", None),
              suscripcion=None):
    """True si el mensaje del emisor llego al oyente.

    Por defecto, `panel` publica y `puente` escucha. Para la Fase 2C se cambia uno
    de los dos: el emisor (`satelite`) o el oyente (`panel`, `orquestador`, con
    `suscripcion` = el patron que lee).
    """
    usuario_em, clave_em = emisor
    usuario_oy, clave_oy = oyente_de
    recibidos = []
    oyente = _oyente(clave_oy or CLAVE_PUENTE, suscripcion or topico, recibidos,
                     usuario=usuario_oy)
    time.sleep(0.8)
    _publica_como_panel(clave_em or CLAVE_PANEL, topico, usuario=usuario_em)
    for _ in range(SEGUNDOS_ESPERA * 10):
        if recibidos:
            break
        time.sleep(0.1)
    oyente.loop_stop()
    oyente.disconnect()
    return bool(recibidos)


fallos = []

print("LO QUE EL PANEL TIENE QUE PODER PUBLICAR")
for topico in ("casa/sala/luz/set", "casa/sala/satelite/decir"):
    ok = entregado(topico)
    print("  publicar %-28s %s" % (topico, "si" if ok else "NO  <-- falla"))
    if not ok:
        fallos.append("no puede publicar " + topico)

print()
print("LO QUE EL PANEL NO TIENE QUE PODER PUBLICAR")
recortes = (
    ("casa/sala/nodo/config", "umbrales de gas de cada habitacion"),
    ("casa/sala/alerta/inactividad", "el panel resuelve alertas, no las crea"),
    ("casa/sala/alerta/gas", "las alertas salen de SQLite, no de MQTT"),
    ("casa/sala/alerta/ayuda", "las alertas salen de SQLite, no de MQTT"),
    ("casa/sala/nodo/prueba_gas", "es del ESP32 con PRUEBAS=1"),
    ("casa/otra/luz/set", "no sale de casa/sala/"),
)
for topico, motivo in recortes:
    ok = entregado(topico)
    print("  %-32s %-9s (%s)" % (topico, "si <-- falla" if ok else "rechazado", motivo))
    if ok:
        fallos.append("puede publicar " + topico)

comprobadas_2c = 0
if CLAVE_SATELITE and CLAVE_ORQUESTADOR:
    print()
    print("FASE 2C: ESTADO POR SATELITE")
    satelite = ("satelite", CLAVE_SATELITE)
    puente = ("puente", CLAVE_PUENTE)
    casos = (
        # (descripcion, topico, emisor, oyente, suscripcion, debe_llegar)
        ("satelite escribe su estado", "casa/sala/satelite/satelite-prueba/estado",
         satelite, puente, None, True),
        ("satelite NO escribe el topico de antes", "casa/sala/satelite/estado",
         satelite, puente, None, False),
        ("satelite NO escribe satelite/decir", "casa/sala/satelite/decir",
         satelite, puente, None, False),
        ("panel lee satelite/+/estado", "casa/sala/satelite/satelite-prueba/estado",
         puente, ("panel", CLAVE_PANEL), "casa/sala/satelite/+/estado", True),
        ("orquestador lee satelite/+/estado",
         "casa/sala/satelite/satelite-prueba/estado", puente,
         ("orquestador", CLAVE_ORQUESTADOR), "casa/sala/satelite/+/estado", True),
    )
    for descripcion, topico, emisor, oyente, suscripcion, debe in casos:
        ok = entregado(topico, emisor=emisor, oyente_de=oyente,
                       suscripcion=suscripcion)
        bien = ok == debe
        print("  %-40s %s" % (descripcion, "bien" if bien else "MAL  <-- falla"))
        if not bien:
            fallos.append(descripcion)
        comprobadas_2c += 1
else:
    print()
    print("FASE 2C: saltada (faltan MQTT_SATELITE_PASSWORD o MQTT_ORQUESTADOR_PASSWORD)")

print()
if fallos:
    print("ACL DEL PANEL: INCORRECTA")
    for una in fallos:
        print("  - " + una)
    sys.exit(1)
print("ACL DEL PANEL: correcta (2 escrituras permitidas, %d recortadas)"
      % len(recortes))
if comprobadas_2c:
    print("ACL DE LA FASE 2C: correcta (%d comprobaciones)" % comprobadas_2c)
