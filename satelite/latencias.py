#!/usr/bin/env python3
"""Resumen de latencias del satelite (etapa B del Paso 2).

Lee el registro por frase que deja `satelite.py` (una linea JSON por frase, sin
audio) y saca la mediana y el maximo de los tres intervalos medidos con el mismo
reloj del satelite:

    fin->final   de soltar la tecla al `final` de Vosk
    fin->decir   de soltar la tecla al mensaje `satelite/decir`
    fin->sonido  de soltar la tecla al arranque del sonido de Piper

Uso:

    python latencias.py                       # ruta por defecto
    python latencias.py --registro RUTA
    python latencias.py --json                # tambien vuelca el resumen en JSON
"""

import argparse
import json
import os
import statistics
import sys


def ruta_defecto() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "asistente-satelite", "latencias.jsonl")


def cargar(ruta: str) -> list:
    entradas = []
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()
            if not linea:
                continue
            try:
                entradas.append(json.loads(linea))
            except ValueError:
                continue
    return entradas


def resumen(entradas: list) -> dict:
    def medir(clave: str):
        valores = [e[clave] for e in entradas
                   if isinstance(e.get(clave), (int, float))]
        if not valores:
            return None
        return {
            "n": len(valores),
            "mediana": statistics.median(valores),
            "max": max(valores),
            "min": min(valores),
        }

    primer = next((e for e in entradas if e.get("t_sonido_ms") is not None
                   and e.get("fin_sonido_ms") is not None), None)
    total = len(entradas)
    con_respuesta = sum(1 for e in entradas if e.get("t_decir_ms") is not None)
    return {
        "frases": total,
        "con_respuesta": con_respuesta,
        "fin_final_ms": medir("fin_final_ms"),
        "fin_decir_ms": medir("fin_decir_ms"),
        "fin_sonido_ms": medir("fin_sonido_ms"),
        "primer_turno_fin_sonido_ms": (primer.get("fin_sonido_ms")
                                       if primer else None),
    }


def imprimir(res: dict) -> None:
    print("frases registradas: %d (con respuesta: %d)"
          % (res["frases"], res["con_respuesta"]))
    for clave in ("fin_final_ms", "fin_decir_ms", "fin_sonido_ms"):
        datos = res[clave]
        etiqueta = clave.replace("_ms", "").replace("_", "->")
        if not datos:
            print("  %-14s sin datos" % etiqueta)
        else:
            print("  %-14s mediana %.0f ms, max %d ms, min %d ms (n=%d)"
                  % (etiqueta, datos["mediana"], datos["max"], datos["min"],
                     datos["n"]))
    print("  primer turno fin->sonido: %s ms"
          % (res["primer_turno_fin_sonido_ms"],))


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumen de latencias del satelite.")
    parser.add_argument("--registro", default=ruta_defecto())
    parser.add_argument("--json", action="store_true",
                        help="imprime tambien el resumen en JSON")
    args = parser.parse_args()
    if not os.path.exists(args.registro):
        print("No existe el registro %s" % args.registro, file=sys.stderr)
        return 2
    res = resumen(cargar(args.registro))
    imprimir(res)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
