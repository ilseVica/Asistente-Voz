#!/usr/bin/env python3
"""Pone el valor de una (o varias) variable(s) de servidor/.env SIN mostrarlo.

Pensado para que lo corra Ilse en SU terminal: el valor se teclea con getpass
(sin eco) y nunca aparece en pantalla, ni en la linea de comandos, ni en `ps`,
ni en el historial de bash.

Que hace:
  1. Respalda servidor/.env en ${MOSQUITTO_SECRETS_DIR}/respaldos/ (carpeta
     creada con 700 si no existe), con fecha y permisos 600.
  2. Reemplaza exactamente la linea VARIABLE=... Si la variable no existe,
     la agrega al final. Si aparece mas de una vez, se planta (ambiguo).
  3. Escribe servidor/.env de forma atomica y con permisos 600.

No imprime el valor: solo el nombre de la variable, el numero de caracteres y
la ruta del respaldo.

Uso:
  herramientas/poner-secreto.py TELEGRAM_BOT_TOKEN
  herramientas/poner-secreto.py TELEGRAM_BOT_TOKEN TAILSCALE_AUTHKEY

Despues: recrear los servicios que lean esa variable (ver el plan).
"""
import datetime
import getpass
import io
import os
import re
import shutil
import stat
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
SERVIDOR = os.path.join(os.path.dirname(AQUI), "servidor")
ENV = os.path.join(SERVIDOR, ".env")
NOMBRE_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def die(msg):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(1)


def secrets_dir():
    v = os.environ.get("MOSQUITTO_SECRETS_DIR")
    if v is None:
        if not os.path.isfile(ENV):
            die("no encuentro %s y no hay MOSQUITTO_SECRETS_DIR" % ENV)
        with io.open(ENV, encoding="utf-8") as f:
            for linea in f:
                if linea.startswith("MOSQUITTO_SECRETS_DIR="):
                    v = linea.split("=", 1)[1]
                    break
        if v is None:
            die("no encuentro MOSQUITTO_SECRETS_DIR en %s" % ENV)
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    return v


def respaldar(ruta, resp_dir, sello):
    destino = os.path.join(resp_dir,
                           "%s.bak-%s" % (os.path.basename(ruta), sello))
    shutil.copy2(ruta, destino)
    os.chmod(destino, 0o600)
    return destino


def sustituir(texto, variable, valor):
    patron = re.compile(r"(?m)^%s=.*$" % re.escape(variable))
    if patron.search(texto) is None:
        if texto and not texto.endswith("\n"):
            texto += "\n"
        return texto + "%s=%s\n" % (variable, valor), False
    nuevo, n = patron.subn("%s=%s" % (variable, valor), texto)
    if n != 1:
        raise RuntimeError("%s aparece %d veces en .env (se esperaba 1)"
                           % (variable, n))
    return nuevo, True


def escribir_env(texto):
    tmp = ENV + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(texto)
        os.chmod(tmp, 0o600)
        os.replace(tmp, ENV)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def pedir_valor(variable):
    valor = getpass.getpass("valor para %s (no se vera): " % variable).strip()
    if not valor:
        resp = input("valor vacio; escribe SI para dejarla vacia: ").strip()
        if resp != "SI":
            die("cancelado: %s no se toco" % variable)
    return valor


def main():
    variables = sys.argv[1:]
    if not variables:
        die("uso: poner-secreto.py VARIABLE [VARIABLE ...]")
    for v in variables:
        if not NOMBRE_OK.match(v):
            die("nombre de variable no valido: %s" % v)
    if not os.path.isfile(ENV):
        die("no encuentro %s" % ENV)

    sd = os.environ.get("MOSQUITTO_SECRETS_DIR")
    if sd is None:
        # reutiliza el mismo buscador que rotar-claves.py
        for linea in io.open(ENV, encoding="utf-8"):
            if linea.startswith("MOSQUITTO_SECRETS_DIR="):
                sd = linea.split("=", 1)[1]
                break
        if sd is None:
            die("no encuentro MOSQUITTO_SECRETS_DIR en %s" % ENV)
    sd = sd.strip()
    if len(sd) >= 2 and sd[0] == sd[-1] and sd[0] in ("'", '"'):
        sd = sd[1:-1]

    resp_dir = os.path.join(sd, "respaldos")
    os.makedirs(resp_dir, mode=0o700, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    respaldo = respaldar(ENV, resp_dir, sello)

    with io.open(ENV, encoding="utf-8") as f:
        texto = f.read()

    for variable in variables:
        valor = pedir_valor(variable)
        texto, existia = sustituir(texto, variable, valor)
        sys.stderr.write("%s: %s (%d caracteres)\n"
                         % (variable, "reemplazada" if existia else "agregada",
                            len(valor)))

    escribir_env(texto)
    sys.stderr.write("respaldo en: %s\n" % respaldo)
    sys.stderr.write("servidor/.env escrito con permisos %s\n"
                     % oct(stat.S_IMODE(os.stat(ENV).st_mode)))


if __name__ == "__main__":
    main()
