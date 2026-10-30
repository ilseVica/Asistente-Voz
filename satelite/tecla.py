#!/usr/bin/env python3
"""Aviso corto de la tecla para hablar, y entrada por archivo.

La tecla (F9 por defecto) la captura Hyprland con `bind`/`bindr`, que son atajos
globales: llegan al socket Unix del satelite aunque mpv este en pantalla
completa, porque no dependen del foco de la ventana.


Lo llama Hyprland en `bind` (al presionar) y `bindr` (al soltar). Solo abre el
socket Unix local del servicio, manda una letra (`P` o `R`) y se marcha. No usa
evdev ni el grupo input: no lee el teclado, solo escucha la orden del gestor de
ventanas.

Uso:

    asistente-tecla presionar | soltar
    asistente-tecla archivo <ruta.wav>      (satelite con [audio] fuente = "archivo")
    asistente-tecla --socket <ruta> ...     (otro satelite, p. ej. el de la VM)

`archivo` pide al satelite que mande ese WAV como una frase completa. El WAV tiene
que ser PCM 16 kHz mono 16 bits; si no, el satelite lo rechaza y aqui se imprime el
motivo. Si sale bien, se imprime el `frase_id` de la frase.

El socket sale de `--socket`, de `ASISTENTE_SATELITE_SOCKET` o, si no hay ninguno,
de `$XDG_RUNTIME_DIR/asistente-satelite.sock` (el de la laptop).

Se puede ejecutar con el Python del sistema (solo usa la biblioteca estandar), asi
que no depende del entorno de uv.
"""

import os
import socket
import sys

RUTA_DEFECTO = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "asistente-satelite.sock")


def _archivo(ruta_socket: str, ruta_wav: str) -> int:
    ruta_wav = os.path.abspath(ruta_wav)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(ruta_socket)
            s.sendall(b"A " + ruta_wav.encode("utf-8") + b"\n")
            s.shutdown(socket.SHUT_WR)
            respuesta = b""
            while True:
                trozo = s.recv(4096)
                if not trozo:
                    break
                respuesta += trozo
    except OSError as error:
        print("no se pudo hablar con el satelite en %s: %s" % (ruta_socket, error),
              file=sys.stderr)
        return 1
    texto = respuesta.decode("utf-8", "replace").strip()
    if texto.startswith("ok"):
        print(texto)
        return 0
    print(texto or "el satelite no contesto", file=sys.stderr)
    return 2


def main() -> int:
    argumentos = sys.argv[1:]
    ruta = os.environ.get("ASISTENTE_SATELITE_SOCKET", RUTA_DEFECTO)
    if len(argumentos) >= 2 and argumentos[0] == "--socket":
        ruta = os.path.expandvars(os.path.expanduser(argumentos[1]))
        argumentos = argumentos[2:]
    accion = argumentos[0].lower() if argumentos else "presionar"
    if accion == "archivo":
        if len(argumentos) != 2:
            print("uso: asistente-tecla [--socket RUTA] archivo <ruta.wav>",
                  file=sys.stderr)
            return 2
        return _archivo(ruta, argumentos[1])
    letra = b"P" if accion.startswith("p") else b"R"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            s.connect(ruta)
            s.sendall(letra)
    except OSError:
        # Si el servicio no esta, no se rompe el atajo de Hyprland.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
