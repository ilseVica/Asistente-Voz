#!/usr/bin/env python3
"""Comprueba que cada comando `mosquitto_pub` de los README sea legal según el ACL.

Existe por un motivo concreto: con MQTT 3.1.1, un mensaje que la ACL rechaza **no
da error** a quien lo publica. `mosquitto_pub` sale con código 0, no dice nada, y
el mensaje no llega a ninguna parte. La única forma de saber si un comando de
prueba funciona es mirando el ACL antes de ejecutarlo, o mirando si el mensaje
llegó después.

Las pruebas manuales de estos README son justo el sitio donde ese error se cuela:
se escribe el comando copiando el de al lado, se cambia el tópico y se olvida
cambiar el usuario. En este proyecto ya pasó: `casa/sala/nodo/prueba_gas` se
publicaba con el usuario `nodo`, que solo tiene lectura de ese tópico.

Uso:  python3 herramientas/revisar_acl.py
Salida: 0 si todos los comandos son legales, 1 si hay alguno que no.
"""

import io
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACL = os.path.join(RAIZ, "servidor", "mosquitto", "config", "acl.conf")
READMES = [
    os.path.join(RAIZ, "firmware", "README.md"),
    os.path.join(RAIZ, "nodo_simulado", "README.md"),
    os.path.join(RAIZ, "servidor", "voz", "README.md"),
]

# Una publicación: el usuario (-u) y el tópico (-t) tienen que estar en la misma
# línea o en líneas consecutivas, porque el comando va partido con "\\" al final.
COMANDO = re.compile(
    r"-u\s+(\S+).*?-t\s+(\S+)", re.DOTALL
)


def leer_acl(ruta: str) -> dict:
    """Devuelve `{usuario: {"write": set, "read": set}}`.

    Se lee el archivo tal cual, sin interpretar comodines más allá de lo que
    hace el propio Mosquitto: `#` y `+` se guardan tal cual y la comparación es
    textual. Es suficiente para estos comandos, que usan tópicos literales.
    """
    permisos = {}
    usuario = None
    with io.open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            # El comentario del ACL empieza al principio de la linea, despues de
            # los espacios. NO se puede hacer `linea.split("#")[0]`, porque eso
            # parte por la mitad `topic readwrite casa/#` y deja `casa/`, que no
            # coincide con nada. Un `#` pegado a un topico es parte del patrón.
            limpia = linea.strip()
            if limpia.startswith("#"):
                continue
            limpia = limpia.rstrip()
            if limpia.startswith("user "):
                usuario = limpia.split()[1]
                permisos[usuario] = {"write": set(), "read": set(),
                                     "readwrite": set()}
                continue
            if usuario is None:
                continue
            partes = limpia.split()
            if len(partes) == 3 and partes[0] == "topic":
                permisos[usuario][partes[1]].add(partes[2])
            elif len(partes) == 2 and partes[0] == "topic":
                permisos[usuario]["readwrite"].add(partes[1])
    return permisos


def coincide(patron: str, topico: str) -> bool:
    """Empareja un patrón del ACL contra un tópico, con la sintaxis de MQTT.

    Hace falta porque el ACL usa comodines de verdad: `puente` tiene
    `topic readwrite casa/#`, y `casa/#` SÍ cubre `casa/sala/nodo/prueba_gas`. Sin
    esta función, el verificador daría un falso positivo en cada comando del
    puente y sería peor que no tenerlo.

    Las dos reglas, según la especificación:

    - `+` sustituye a UN nivel entero, y no puede aparecer dentro de un nivel:
      `casa/+/luz` cubre `casa/sala/luz`, no `casa/a/b/luz`.
    - `#` sustituye a Niveles restantes, incluido el ninguno: `casa/#` cubre
      `casa` y también `casa/sala/nodo/prueba_gas`. Solo puede ser el último nivel.
    """
    if patron == topico:
        return True
    niveles_patron = patron.split("/")
    niveles_topico = topico.split("/")

    for indice, nivel in enumerate(niveles_patron):
        if nivel == "#":
            # Solo es válido al final, y cubre de aqui en adelante.
            return indice <= len(niveles_topico)
        if indice >= len(niveles_topico):
            return False
        if nivel == "+":
            continue
        if nivel != niveles_topico[indice]:
            return False

    return len(niveles_patron) == len(niveles_topico)


def permitido(permisos: dict, usuario: str, topico: str) -> bool:
    """¿Puede este usuario publicar en este tópico?

    Se mira `write` y `readwrite`, que son las dos formas de escribir. `read` no
    cuenta: poder leer un tópico no es poder publicarlo, y esa confusión es
    exactamente el error que hizo falta encontrar en `prueba_gas`.
    """
    reglas = permisos.get(usuario)
    if reglas is None:
        return False
    for patron in reglas.get("write", ()):
        if coincide(patron, topico):
            return True
    for patron in reglas.get("readwrite", ()):
        if coincide(patron, topico):
            return True
    return False


def revisar_comandos(permisos: dict, ruta: str) -> list:
    problemas = []
    with io.open(ruta, encoding="utf-8") as archivo:
        texto = archivo.read()
    for numero, bloque in enumerate(texto.split("```bash")[1:], 1):
        # Cada bloque puede traer varios comandos. Se parte por "docker".
        for comando in bloque.split("docker")[1:]:
            trozo = "docker" + comando
            if "mosquitto_pub" not in trozo:
                continue
            lineas = [l for l in trozo.splitlines() if l.strip()]
            if not lineas:
                continue
            usuario = topico = None
            for linea in lineas[:6]:
                if usuario is None:
                    encuentra = re.search(r"-u\s+(\S+)", linea)
                    if encuentra:
                        usuario = encuentra.group(1)
                if topico is None:
                    encuentra = re.search(r"-t\s+(\S+)", linea)
                    if encuentra:
                        topico = encuentra.group(1)
                if usuario and topico:
                    break
            if not (usuario and topico):
                continue
            if not permitido(permisos, usuario, topico):
                problemas.append(
                    "%s: comando %d: el usuario '%s' NO puede publicar en '%s'"
                    % (os.path.basename(ruta), numero, usuario, topico)
                )
    return problemas


def main() -> int:
    if not os.path.exists(ACL):
        print("  no existe %s" % ACL)
        return 2
    permisos = leer_acl(ACL)
    print("  usuarios en el ACL: %s" % ", ".join(sorted(permisos)))

    problemas = []
    for ruta in READMES:
        if os.path.exists(ruta):
            problemas.extend(revisar_comandos(permisos, ruta))

    for problema in problemas:
        print("  " + problema)
    if problemas:
        print("  %d comando(s) ilegal(es)" % len(problemas))
        return 1
    print("  todos los comandos de prueba son legales segun el ACL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
