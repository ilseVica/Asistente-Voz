#!/usr/bin/env python3
"""Revisa que los archivos escritos no traigan caracteres de otro idioma.

Sirve para una cosa concreta: los caracteres chinos, cirílicos y coreanos se cuelan
en los archivos que escribo a máquina y rompen la lectura. No detectan un error de
lógica, solo que la palabra está mal.

Uso:  python3 herramientas/revisar_texto.py <archivo> [<archivo> ...]
Salida: 0 si todo está limpio, 1 si hay algo que arreglar.
"""

import io
import re
import sys

# Chino, japonés, coreano, cirílico y los signos de ancho completo.
EXTRANOS = re.compile(
    "[\u1100-\u11ff\u2e80-\u9fff\uac00-\ud7ff\uf900-\ufaff"
    "\uff00-\uffef\u0400-\u04ff\u0370-\u03ff]"
)

# Palabras que aparecen por error cuando el texto se mezcla con inglés.
# Solo palabras que son typos cuando aparecen. `broker`, `payload`, `timeout`,
# `handler` y `logger` quedan fuera a proposito: son los terminos que usa el
# proyecto entero, y marcarlos haria que la herramienta gritara sin motivo.
# Palabras inglesas que ademas son palabra clave de Python: en un archivo .py
# son sintaxis legitima, no prosa en ingles.
PALABRAS_CLAVE = frozenset(("with", "from", "and", "in", "not", "or", "if", "else", "is"))

INGLES = re.compile(
    r"\b(seemingly|having|nombredel|delBro|quemirar|espa_ol|spao_)\b|"
    r"\b(the|and|with|from|this|that|should|would)\b"
)


def revisar(ruta: str) -> list:
    """Devuelve la lista de problemas de un archivo."""
    problemas = []
    try:
        with io.open(ruta, encoding="utf-8") as archivo:
            lineas = archivo.readlines()
    except UnicodeDecodeError:
        return ["%s: no es UTF-8" % ruta]
    except OSError as error:
        return ["%s: no se pudo leer (%s)" % (ruta, error)]

    for numero, linea in enumerate(lineas, 1):
        if EXTRANOS.search(linea):
            for coincidencia in EXTRANOS.finditer(linea):
                caracter = coincidencia.group(0)
                problemas.append(
                    "%s:%d: caracter estranho %r (U+%04X)"
                    % (ruta, numero, caracter, ord(caracter))
                )
        for palabra in INGLES.finditer(linea):
            # Conectores en ingles que si son un error en un texto en espanol.
            # Se descartan los que ademas son palabra clave de Python (`with`,
            # `from`, `and`, `in`), porque en codigo son sintaxis y no prosa. Los
            # que no son palabra clave (`the`, `this`, `should`) no tienen esa
            # excusa y se reportan siempre.
            if palabra.group(0) in PALABRAS_CLAVE:
                continue
            problemas.append(
                "%s:%d: palabra en ingles %r"
                % (ruta, numero, palabra.group(0))
            )
            problemas.append(
                "%s:%d: palabra en ingles %r" % (ruta, numero, palabra.group(0))
            )
    return problemas


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    todos = []
    for ruta in sys.argv[1:]:
        todos.extend(revisar(ruta))
    for problema in todos:
        print("  " + problema)
    if todos:
        print("  %d problema(s)" % len(todos))
        return 1
    print("  limpio: %d archivo(s) sin caracteres de otro idioma"
          % (len(sys.argv) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
