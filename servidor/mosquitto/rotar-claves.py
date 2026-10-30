#!/usr/bin/env python3
"""Rota las contrasenas MQTT de uno o varios usuarios.

Que hace:
  1. Respalda servidor/.env y el passwd en ${MOSQUITTO_SECRETS_DIR}/respaldos/
     (carpeta creada con 700 si no existe), con fecha y permisos 600.
  2. Genera TODAS las claves nuevas primero (secrets.token_urlsafe(24)).
  3. Aplica el passwd para todos y escribe servidor/.env al final.
     Si falla cualquier paso, restaura passwd y .env desde los respaldos.
  4. Pasa cada clave por la ENTRADA ESTANDAR a mosquitto_passwd, con el mismo
     mecanismo que crear-usuarios.sh. La clave no aparece en la linea de
     comandos, ni en `ps`, ni en el historial de bash.

No imprime ninguna contrasena: solo "ok <usuario>" por usuario y (por stderr)
los permisos actuales del passwd y de la carpeta, sin cambiarlos.

Uso:
  servidor/mosquitto/rotar-claves.py orquestador puente voz

Despues: recargar el broker (SIGHUP) y recrear los servicios afectados.
Los respaldos se borran A MANO cuando todo funcione.
"""
import datetime
import io
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
SERVIDOR = os.path.dirname(AQUI)
ENV = os.path.join(SERVIDOR, ".env")
IMAGEN = "eclipse-mosquitto:2.0.22"
PASSWD_REL = "passwd"

USUARIOS_CONOCIDOS = {"nodo", "puente", "orquestador", "satelite", "salud",
                      "panel", "voz"}


def validar_usuarios(usuarios):
    desconocidos = [u for u in usuarios if u not in USUARIOS_CONOCIDOS]
    if desconocidos:
        die("usuario desconocido: %s (conocidos: %s)"
            % (", ".join(desconocidos),
               ", ".join(sorted(USUARIOS_CONOCIDOS))))


def uso():
    sys.stdout.write(
        "Uso: rotar-claves.py [--help] <usuario> [usuario ...]\n"
        "Usuarios conocidos: %s\n" % ", ".join(sorted(USUARIOS_CONOCIDOS)))


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
    if not os.path.exists(ruta):
        return None
    destino = os.path.join(resp_dir,
                           "%s.bak-%s" % (os.path.basename(ruta), sello))
    shutil.copy2(ruta, destino)
    os.chmod(destino, 0o600)
    return destino


def restaurar(ruta, respaldo, modo_original):
    if respaldo and os.path.exists(respaldo):
        shutil.copy2(respaldo, ruta)
        os.chmod(ruta, modo_original)
    elif os.path.exists(ruta):
        os.remove(ruta)


def sustituir(texto, usuario, clave):
    variable = "MQTT_%s_PASSWORD" % usuario.upper()
    patron = re.compile(r"(?m)^%s=.*$" % re.escape(variable))
    nuevo, n = patron.subn("%s=%s" % (variable, clave), texto)
    if n != 1:
        raise RuntimeError("%s aparece %d veces en .env (se esperaba 1)"
                           % (variable, n))
    return nuevo


def rotar_en_passwd(sd, passwd, usuario, clave):
    cmd = [
        "docker", "run", "--rm", "-i",
        "--user", "%d:%d" % (os.getuid(), os.getgid()),
        "-v", "%s:/mosquitto/secrets" % sd,
        IMAGEN, "mosquitto_passwd",
    ]
    if not os.path.isfile(passwd):
        cmd.append("-c")
    cmd += ["/mosquitto/secrets/%s" % PASSWD_REL, usuario]
    entrada = ("%s\n%s\n" % (clave, clave)).encode()
    res = subprocess.run(cmd, input=entrada,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode != 0:
        raise RuntimeError("mosquitto_passwd fallo para %s" % usuario)


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


def main():
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        uso()
        return 0
    usuarios = sys.argv[1:]
    if not usuarios:
        die("uso: rotar-claves.py <usuario> [usuario ...]")
    validar_usuarios(usuarios)
    sd = secrets_dir()
    passwd = os.path.join(sd, PASSWD_REL)
    resp_dir = os.path.join(sd, "respaldos")
    os.makedirs(resp_dir, mode=0o700, exist_ok=True)
    sello = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    if subprocess.run(["docker", "image", "inspect", IMAGEN],
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        sys.stderr.write("descargando %s\n" % IMAGEN)
        subprocess.run(["docker", "pull", IMAGEN], check=True)

    modo_env = stat.S_IMODE(os.stat(ENV).st_mode)
    modo_passwd = (stat.S_IMODE(os.stat(passwd).st_mode)
                   if os.path.exists(passwd) else None)
    resp_env = respaldar(ENV, resp_dir, sello)
    resp_passwd = respaldar(passwd, resp_dir, sello)

    with io.open(ENV, encoding="utf-8") as f:
        texto = f.read()

    claves = {u: secrets.token_urlsafe(24) for u in usuarios}
    try:
        for u in usuarios:
            rotar_en_passwd(sd, passwd, u, claves[u])
        nuevo = texto
        for u in usuarios:
            nuevo = sustituir(nuevo, u, claves[u])
        escribir_env(nuevo)
    except BaseException:
        restaurar(ENV, resp_env, modo_env)
        restaurar(passwd, resp_passwd,
                  modo_passwd if modo_passwd is not None else 0o640)
        die("fallo: restaurado desde %s" % resp_dir)
    finally:
        claves.clear()

    for u in usuarios:
        print("ok %s" % u)

    sys.stderr.write("permisos passwd:  %s\n" % stat.filemode(os.stat(passwd).st_mode))
    sys.stderr.write("permisos carpeta: %s\n" % stat.filemode(os.stat(sd).st_mode))
    sys.stderr.write("respaldos en:    %s\n" % resp_dir)


if __name__ == "__main__":
    main()
