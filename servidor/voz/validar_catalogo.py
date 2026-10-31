#!/usr/bin/env python3
"""Valida servidor/voz/canciones_ingles.json contra el vocabulario del modelo ingles.

Para cada titulo crea un KaldiRecognizer con gramatica [titulo, "[unk]"] y captura
el stderr de Vosk. Si Vosk dice "Ignoring word missing in vocabulary", el titulo
tiene palabras fuera del vocabulario y no se usara en el reconocimiento.

Uso:
    python validar_catalogo.py
    python validar_catalogo.py --catalogo ruta.json --modelo /modelo/vosk-model-small-en-us-0.15
"""

import argparse
import contextlib
import io
import json
import os
import sys

import vosk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import servidor_voz as sv


def capturar_stderr(funcion):
    """Ejecuta funcion() capturando el fd 2 (Vosk escribe en C)."""
    lectura, escritura = os.pipe()
    original = os.dup(2)
    try:
        os.dup2(escritura, 2)
        os.close(escritura)
        funcion()
    finally:
        os.dup2(original, 2)
        os.close(original)
    trozos = []
    while True:
        datos = os.read(lectura, 4096)
        if not datos:
            break
        trozos.append(datos)
    os.close(lectura)
    return b"".join(trozos).decode("utf-8", "replace")


def palabras_fuera(modelo, titulo):
    faltantes = []

    def crear():
        vosk.KaldiRecognizer(modelo, 16000, json.dumps([titulo, "[unk]"], ensure_ascii=False))

    salida = capturar_stderr(crear)
    for linea in salida.splitlines():
        if "missing in vocabulary" in linea:
            palabra = linea.split("missing in vocabulary", 1)[1]
            palabra = palabra.strip().lstrip(":").strip().strip("'\"")
            if palabra:
                faltantes.append(palabra)
    return faltantes


def main():
    parser = argparse.ArgumentParser(description="Valida el catalogo de canciones en ingles.")
    parser.add_argument("--catalogo", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "canciones_ingles.json"))
    parser.add_argument("--modelo", default=os.environ.get(
        "VOZ_MODELO_INGLES", "/modelo/vosk-model-small-en-us-0.15"))
    args = parser.parse_args()

    if not os.path.isfile(args.catalogo):
        print("no existe el catalogo: %s" % args.catalogo, file=sys.stderr)
        return 2
    if not os.path.isdir(args.modelo):
        print("no existe el modelo: %s" % args.modelo, file=sys.stderr)
        return 2

    vosk.SetLogLevel(-1)
    modelo = vosk.Model(args.modelo)
    catalogo = json.load(open(args.catalogo, encoding="utf-8"))
    if not isinstance(catalogo, list):
        print("el catalogo no es una lista", file=sys.stderr)
        return 2

    validos = 0
    fuera_total = 0
    print("titulo\tartista\tpalabras_fuera")
    for item in catalogo:
        titulo = str(item.get("titulo", "")).strip().lower()
        artista = str(item.get("artista", "")).strip()
        faltantes = palabras_fuera(modelo, titulo)
        if faltantes:
            fuera_total += 1
            print("%s\t%s\t%s" % (titulo, artista, ",".join(faltantes)))
        else:
            validos += 1
    print("validos: %d/%d  fuera_de_vocabulario: %d" % (validos, len(catalogo), fuera_total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
