#!/usr/bin/env python3
"""Aviso corto de la tecla para hablar.

Lo llama Hyprland en `bind` (al presionar) y `bindr` (al soltar). Solo abre el
socket Unix local del servicio, manda una letra (`P` o `R`) y se marcha. No usa
evdev ni el grupo input: no lee el teclado, solo escucha la orden del gestor de
ventanas.

Se puede ejecutar con el Python del sistema (solo usa la biblioteca estandar), asi
que no depende del entorno de uv.
"""

import os
import socket
import sys

RUTA_DEFECTO = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "asistente-satelite.sock")


def main() -> int:
    ruta = os.environ.get("ASISTENTE_SATELITE_SOCKET", RUTA_DEFECTO)
    accion = sys.argv[1].lower() if len(sys.argv) > 1 else "presionar"
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
