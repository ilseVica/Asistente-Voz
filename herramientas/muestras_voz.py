#!/usr/bin/env python3
"""Genera las muestras de voz para escuchar en el iPad (fase 2A del rediseno de voz).

No forma parte del producto: es una herramienta de la fase 2A para que Ilse elija la
voz y los parametros de Piper escuchando. Sintetiza unas frases fijas con varias
variantes (voz + length_scale + sentence_silence + noise_scale + noise_w) y escribe
un index.html sin dependencias externas para reproducirlos desde Safari en el iPad.

Cada "ronda" es un conjunto de frases y variantes con su carpeta de salida y su
modo de pagina:

  - ronda 1 (modo normal): 7 variantes x 6 frases = 42 WAV en ~/asistente-muestras-voz/.
    La primera variante es la voz de referencia (la que suena hoy).
  - ronda 2 (modo ciego): 6 variantes x 8 frases = 48 WAV en ~/asistente-muestras-voz-2/.
    Los reproductores se etiquetan solo con un numero visible (1..6), el orden se
    baraja en cada seccion con una semilla fija, y la clave (numero -> variante) queda
    al final en un <details> cerrado.
  - ronda 3 (modo ciego): 5 variantes x 8 frases = 40 WAV en ~/asistente-muestras-voz-3/.
    Misma frase y config que la variante C de la ronda 2, con el tono desplazado por
    post-proceso con sox (efecto pitch, en cents). Igual de ciega, con 5 numeros.
  - ronda 4 (modo ciego, la ultima): 6 variantes x 8 frases = 48 WAV en
    ~/asistente-muestras-voz-4/. Tres velocidades de la C en Piper (1.2, 1.1, 1.0) y
    tres voces del motor Kokoro (ef_dora, em_alex, em_santa). A ciegas con 6 numeros,
    mas una seccion aparte, NO a ciegas, con la prueba de acento.

Las frases y las variantes estan en las tablas de abajo (FRASES_*, VARIANTES_*), para
no reescribir el script en cada ronda.

Hay DOS motores:

  * Piper (por defecto): usa el Piper instalado en
    ~/.local/share/asistente-satelite/piper y las voces de
    ~/.local/share/asistente-satelite/voces. Cada variante lleva locale, voz, ls, ss,
    ns, nw (None = no pasar el flag) y, si trae `pitch` (cents), se pasa el WAV por
    `sox entrada.wav salida.wav pitch <cents>`. Se mide el tiempo de Piper y el de sox
    por separado.

  * Kokoro (variante con `motor: "kokoro"`): usa kokoro-onnx en un entorno propio en
    ~/.local/share/asistente-satelite/kokoro-venv (Python 3.13), con los modelos de
    ~/.local/share/asistente-satelite/kokoro. Como kokoro-onnx no corre en el Python
    del sistema (3.14), este script NO importa kokoro: llama por subproceso al
    helper ~/.local/share/asistente-satelite/kokoro/sintetizar_kokoro.py, que carga
    el modelo UNA vez por variante y sintetiza todas las frases; asi se separa el
    tiempo de carga del tiempo de sintesis de cada frase. El idioma es `lang`
    (por defecto "es-419", espanol latinoamericano) y la voz es `voz`.

Uso:
    python3 herramientas/muestras_voz.py            # ronda 1 (por defecto)
    python3 herramientas/muestras_voz.py --ronda 2
    python3 herramientas/muestras_voz.py --ronda 3
    python3 herramientas/muestras_voz.py --ronda 4
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import subprocess
import sys
import time
import wave

PIPER_DIR = os.path.expanduser("~/.local/share/asistente-satelite/piper")
VOCES_DIR = os.path.expanduser("~/.local/share/asistente-satelite/voces")
TIME_BIN = "/usr/bin/time"
SOX_BIN = "sox"

# Kokoro (motor alternativo). Entorno y modelos aislados, fuera del repo.
KOKORO_DIR = os.path.expanduser("~/.local/share/asistente-satelite/kokoro")
KOKORO_VENV_PY = os.path.expanduser(
    "~/.local/share/asistente-satelite/kokoro-venv/bin/python")
KOKORO_HELPER = os.path.join(KOKORO_DIR, "sintetizar_kokoro.py")
KOKORO_MODEL = os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx")
KOKORO_VOCES = os.path.join(KOKORO_DIR, "voices-v1.0.bin")
KOKORO_LANG = "es-419"

# Semilla fija del barajado de la pagina ciega (ronda 2): reproducible.
SEMILLA_CIEGO = 20261013

# --- Frases ----------------------------------------------------------------

FRASES_R1 = [
    "Listo, encendí la luz de la sala.",
    "Disculpe, no le entendí. ¿Me lo puede repetir?",
    "Ya avisé a su familia. Alguien se comunicará con usted pronto.",
    "Atención: se detectó gas en la cocina. Por favor, abra una ventana y no encienda ninguna llama.",
    "La temperatura de la sala es de 24 grados.",
    "El tinaco está al sesenta por ciento.",
]

FRASES_R2 = FRASES_R1 + [
    "Con gusto. Ya encendí la luz de la sala.",
    "No se preocupe, ya avisé a su familia. Alguien se comunicará con usted pronto.",
]

# --- Variantes -------------------------------------------------------------
# Cada variante: id (interno), locale, voz, ls, ss, ns, nw, ref, desc.
# ns/nw en None = no pasar el flag (se usan los valores por defecto de Piper).

VARIANTES_R1 = [
    {"id": "ald", "locale": "MX", "voz": "ald-medium", "ls": "1.0", "ss": "0.2",
     "ns": None, "nw": None, "ref": True},
    {"id": "ch1.0-0.3", "locale": "MX", "voz": "claude-high", "ls": "1.0", "ss": "0.3",
     "ns": None, "nw": None, "ref": False},
    {"id": "ch1.0-0.6", "locale": "MX", "voz": "claude-high", "ls": "1.0", "ss": "0.6",
     "ns": None, "nw": None, "ref": False},
    {"id": "ch1.15-0.3", "locale": "MX", "voz": "claude-high", "ls": "1.15", "ss": "0.3",
     "ns": None, "nw": None, "ref": False},
    {"id": "ch1.15-0.6", "locale": "MX", "voz": "claude-high", "ls": "1.15", "ss": "0.6",
     "ns": None, "nw": None, "ref": False},
    {"id": "ch1.3-0.3", "locale": "MX", "voz": "claude-high", "ls": "1.3", "ss": "0.3",
     "ns": None, "nw": None, "ref": False},
    {"id": "ch1.3-0.6", "locale": "MX", "voz": "claude-high", "ls": "1.3", "ss": "0.6",
     "ns": None, "nw": None, "ref": False},
]

VARIANTES_R2 = [
    {"id": "R", "locale": "MX", "voz": "claude-high", "ls": "1.3", "ss": "0.3",
     "ns": "0.667", "nw": "0.8", "ref": True,
     "desc": "claude-high, velocidad 1.3, entonación y ritmo por defecto "
             "(la elegida en la ronda 1)"},
    {"id": "A", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.667", "nw": "0.8", "ref": False,
     "desc": "claude-high, velocidad 1.2, entonación y ritmo por defecto"},
    {"id": "B", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.8", "nw": "0.8", "ref": False,
     "desc": "claude-high, velocidad 1.2, más entonación"},
    {"id": "C", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.667", "nw": "1.0", "ref": False,
     "desc": "claude-high, velocidad 1.2, ritmo más natural"},
    {"id": "D", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.8", "nw": "1.0", "ref": False,
     "desc": "claude-high, velocidad 1.2, más entonación y ritmo más natural"},
    {"id": "E", "locale": "AR", "voz": "daniela-high", "ls": "1.15", "ss": "0.3",
     "ns": "0.667", "nw": "0.8", "ref": False,
     "desc": "daniela-high (voz distinta, argentina), velocidad 1.15"},
]

# Ronda 3: la config "C" elegida en la ronda 2 (claude-high, ls 1.2, ss 0.3,
# ns 0.667, nw 1.0), con el tono desplazado por sox en distintos cents.
VARIANTES_R3 = [
    {"id": "p0", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.667", "nw": "1.0", "pitch": 0, "ref": True,
     "desc": "claude-high, la variante C de la ronda 2, sin cambio de tono"},
    {"id": "p100", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.667", "nw": "1.0", "pitch": 100, "ref": False,
     "desc": "claude-high, un semitono más agudo"},
    {"id": "p150", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.667", "nw": "1.0", "pitch": 150, "ref": False,
     "desc": "claude-high, semitono y medio más agudo"},
    {"id": "p200", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.667", "nw": "1.0", "pitch": 200, "ref": False,
     "desc": "claude-high, dos semitonos más agudo"},
    {"id": "p300", "locale": "MX", "voz": "claude-high", "ls": "1.2", "ss": "0.3",
     "ns": "0.667", "nw": "1.0", "pitch": 300, "ref": False,
     "desc": "claude-high, tres semitonos más agudo"},
]

# Ronda 4 (la última): velocidades de la C en Piper y el motor Kokoro.
# Las de Piper: la C (ls 1.2) y dos más rápidas (1.1 y 1.0), con el resto igual.
# Las de Kokoro: tres voces en español latinoamericano (lang es-419), velocidad 1.0.
VARIANTES_R4 = [
    {"id": "C12", "motor": "piper", "locale": "MX", "voz": "claude-high",
     "ls": "1.2", "ss": "0.3", "ns": "0.667", "nw": "1.0", "ref": True,
     "desc": "Piper claude-high, velocidad 1.2 (la elegida; referencia)"},
    {"id": "C11", "motor": "piper", "locale": "MX", "voz": "claude-high",
     "ls": "1.1", "ss": "0.3", "ns": "0.667", "nw": "1.0", "ref": False,
     "desc": "Piper claude-high, velocidad 1.1 (un poco más rápida)"},
    {"id": "C10", "motor": "piper", "locale": "MX", "voz": "claude-high",
     "ls": "1.0", "ss": "0.3", "ns": "0.667", "nw": "1.0", "ref": False,
     "desc": "Piper claude-high, velocidad 1.0 (normal)"},
    {"id": "KD", "motor": "kokoro", "voz": "ef_dora", "lang": "es-419",
     "speed": 1.0, "ref": False,
     "desc": "Kokoro voz ef_dora (femenina), español latinoamericano, velocidad 1.0"},
    {"id": "KA", "motor": "kokoro", "voz": "em_alex", "lang": "es-419",
     "speed": 1.0, "ref": False,
     "desc": "Kokoro voz em_alex (masculina), español latinoamericano, velocidad 1.0"},
    {"id": "KS", "motor": "kokoro", "voz": "em_santa", "lang": "es-419",
     "speed": 1.0, "ref": False,
     "desc": "Kokoro voz em_santa (masculina), español latinoamericano, velocidad 1.0"},
]

NOTA_R1 = ("7 variantes × 6 frases. En cada frase, la primera es la voz actual "
           "(referencia, marco verde). «Velocidad»: 1.0 = normal, 1.15 = un poco "
           "más lento, 1.3 = más lento. «Pausa entre frases» es el silencio de "
           "Piper tras cada frase. El tiempo de síntesis incluye la carga del "
           "modelo (cada archivo se generó en un proceso nuevo).")

NOTA_R2 = ("6 variantes × 8 frases. Los reproductores van a ciegas: en cada frase "
           "aparecen como 1 a 6, en orden distinto según la frase, para que no "
           "siempre sea la misma variante en el mismo sitio. Anota tú lo que te "
           "gusta. Al final, en «Ver qué es cada letra», está la clave de cada "
           "frase.")

NOTA_R3 = ("5 variantes × 8 frases. Todas son la misma voz y los mismos ajustes "
           "que elegiste en la ronda 2 (claude-high, velocidad 1.2, ritmo más "
           "natural); lo único que cambia es el tono, desplazado con sox. Van a "
           "ciegas: en cada frase aparecen como 1 a 5, en orden distinto según la "
           "frase. Anota tú lo que te gusta. Al final, en «Ver qué es cada "
           "número», está la clave de cada frase.")

NOTA_R4 = ("6 variantes × 8 frases (la última ronda). Tres son Piper (la misma voz "
           "que elegiste, a tres velocidades: 1.2, 1.1 y 1.0) y tres son un motor "
           "distinto, Kokoro, con tres voces en español latinoamericano. Van a "
           "ciegas: en cada frase aparecen como 1 a 6, en orden distinto según la "
           "frase. Anota tú lo que te gusta. Al final, en «Ver qué es cada número», "
           "está la clave. Después hay una sección aparte, NO a ciegas, con una "
           "prueba de acento.")

HUMO_R4 = {
    "titulo": "Prueba de acento (misma frase y voz, dos acentos)",
    "nota": ("La misma frase («Listo, encendí la luz de la cocina.») con la misma voz "
             "de Kokoro (ef_dora), cambiando solo el acento. En español de España la "
             "c y la z suenan como la «th» inglesa (θ); en español latinoamericano "
             "suenan como s. Los fonemas lo confirman: el primero lleva θ y el "
             "segundo s."),
    "pistas": [
        {"etiqueta": "Español de España (es) — c/z suenan como θ: "
                     "lˈisto, ˌenθendˈi la lˈuθ ðe la koθˈina.",
         "archivo": "humo/acento_es.wav"},
        {"etiqueta": "Español latinoamericano (es-419) — c/z suenan como s: "
                     "lˈisto, ˌensendˈi la lˈus ðe la kosˈina.",
         "archivo": "humo/acento_es-419.wav"},
    ],
}

RONDAS = {
    "1": {
        "salida": os.path.expanduser("~/asistente-muestras-voz"),
        "patron": "{voz}_ls{ls}_ss{ss}_f{n}.wav",
        "modo": "normal",
        "titulo": "Muestras de voz",
        "titulo_pagina": "Muestras de voz — Asistente",
        "nota": NOTA_R1,
        "frases": FRASES_R1,
        "variantes": VARIANTES_R1,
    },
    "2": {
        "salida": os.path.expanduser("~/asistente-muestras-voz-2"),
        "patron": "{id}_{voz}_ls{ls}_ss{ss}_ns{ns}_nw{nw}_f{n}.wav",
        "modo": "ciego",
        "titulo": "Muestras de voz — ronda 2 (a ciegas)",
        "nota": NOTA_R2,
        "frases": FRASES_R2,
        "variantes": VARIANTES_R2,
    },
    "3": {
        "salida": os.path.expanduser("~/asistente-muestras-voz-3"),
        "patron": "{id}_{voz}_C_pitch{pitch}_f{n}.wav",
        "modo": "ciego",
        "titulo": "Muestras de voz — ronda 3 (a ciegas)",
        "nota": NOTA_R3,
        "etiqueta_col": "Número",
        "resumen_clave": "Ver qué es cada número",
        "frases": FRASES_R2,
        "variantes": VARIANTES_R3,
    },
    "4": {
        "salida": os.path.expanduser("~/asistente-muestras-voz-4"),
        "patron": "{id}_{motor}_{voz}_f{n}.wav",
        "modo": "ciego",
        "titulo": "Muestras de voz — ronda 4 (a ciegas)",
        "nota": NOTA_R4,
        "etiqueta_col": "Número",
        "resumen_clave": "Ver qué es cada número",
        "frases": FRASES_R2,
        "variantes": VARIANTES_R4,
        "humo": HUMO_R4,
    },
}

VELOCIDAD = {"1.0": "normal", "1.15": "un poco más lento", "1.2": "un poco más lento",
             "1.3": "más lento"}

_RSS = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def ruta_voz(locale: str, voz: str) -> str:
    return os.path.join(VOCES_DIR, "es_%s-%s.onnx" % (locale, voz))


def ruta_espeak() -> str:
    return os.path.join(PIPER_DIR, "espeak-ng-data")


def sintetizar(texto: str, variante: dict, destino: str) -> dict:
    """Sintetiza una frase y devuelve {ms, ms_sox, rss_kb, dur_s}.

    `ms` es el tiempo de Piper; `ms_sox` es el del post-proceso de tono (0 si no se
    aplica). Lanza si Piper o sox fallan.
    """
    pitch = int(variante.get("pitch") or 0)
    # Si hay que cambiar el tono, Piper escribe en un temporal y sox produce el final.
    intermedio = destino + ".piper.wav" if pitch else destino

    piper = os.path.join(PIPER_DIR, "piper")
    orden = [TIME_BIN, "-v", piper, "--model", ruta_voz(variante["locale"], variante["voz"]),
             "--length_scale", variante["ls"], "--sentence_silence", variante["ss"]]
    if variante.get("ns") is not None:
        orden += ["--noise_scale", variante["ns"]]
    if variante.get("nw") is not None:
        orden += ["--noise_w", variante["nw"]]
    if os.path.isdir(ruta_espeak()):
        orden += ["--espeak_data", ruta_espeak()]
    orden += ["--output_file", intermedio]

    entorno = dict(os.environ)
    entorno["LD_LIBRARY_PATH"] = PIPER_DIR + os.pathsep + entorno.get("LD_LIBRARY_PATH", "")

    inicio = time.perf_counter()
    proceso = subprocess.run(
        orden, input=(texto + "\n").encode("utf-8"), cwd=PIPER_DIR, env=entorno,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
    ms = (time.perf_counter() - inicio) * 1000.0

    if proceso.returncode != 0:
        raise RuntimeError("Piper fallo (rc=%d): %s" % (
            proceso.returncode, proceso.stderr.decode("utf-8", "replace")[-500:]))

    salida = proceso.stderr.decode("utf-8", "replace")
    coincidencia = _RSS.search(salida)
    rss_kb = int(coincidencia.group(1)) if coincidencia else -1

    ms_sox = 0.0
    if pitch:
        inicio = time.perf_counter()
        proceso = subprocess.run(
            [SOX_BIN, intermedio, destino, "pitch", str(pitch)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
        ms_sox = (time.perf_counter() - inicio) * 1000.0
        if proceso.returncode != 0:
            os.remove(intermedio)
            raise RuntimeError("sox fallo (rc=%d): %s" % (
                proceso.returncode, proceso.stderr.decode("utf-8", "replace")[-500:]))
        os.remove(intermedio)

    with wave.open(destino, "rb") as w:
        dur_s = w.getnframes() / w.getframerate()

    return {"ms": ms, "ms_sox": ms_sox, "rss_kb": rss_kb, "dur_s": dur_s}


def nombre_archivo(ronda: dict, variante: dict, numero: int) -> str:
    return ronda["patron"].format(
        id=variante["id"], motor=variante.get("motor", "piper"), voz=variante["voz"],
        ls=variante.get("ls"), ss=variante.get("ss"), ns=variante.get("ns"),
        nw=variante.get("nw"), pitch=variante.get("pitch", 0), n=numero)


def sintetizar_kokoro(variante: dict, ronda: dict) -> list:
    """Sintetiza TODAS las frases de una variante Kokoro en un solo proceso.

    Llama por subproceso al helper (Python del venv de Kokoro), que carga el modelo
    una vez y sintetiza cada frase. Devuelve una fila por frase con el tiempo de
    sintesis (`ms`), la carga del modelo aparte (`load_ms`) y la memoria.
    """
    trabajos = []
    for numero, frase in enumerate(ronda["frases"], 1):
        archivo = nombre_archivo(ronda, variante, numero)
        trabajos.append({"f": numero, "text": frase,
                         "out": os.path.join(ronda["salida"], archivo),
                         "archivo": archivo})

    config = {
        "model": KOKORO_MODEL, "voices": KOKORO_VOCES,
        "voice": variante["voz"], "lang": variante.get("lang", KOKORO_LANG),
        "speed": variante.get("speed", 1.0),
        "jobs": [{"f": t["f"], "text": t["text"], "out": t["out"]} for t in trabajos],
    }
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False)
        ruta_config = fh.name

    try:
        proceso = subprocess.run(
            [KOKORO_VENV_PY, KOKORO_HELPER, ruta_config],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    finally:
        os.remove(ruta_config)

    if proceso.returncode != 0:
        raise RuntimeError("Kokoro fallo (rc=%d): %s" % (
            proceso.returncode, proceso.stderr.decode("utf-8", "replace")[-800:]))

    salida = json.loads(proceso.stdout.decode("utf-8").strip().splitlines()[-1])
    filas = []
    for trabajo, resultado in zip(trabajos, salida["jobs"]):
        filas.append({
            "id": variante["id"], "motor": "kokoro", "voz": variante["voz"],
            "lang": resultado.get("lang", config["lang"]),
            "speed": variante.get("speed", 1.0),
            "ls": None, "ss": None, "ns": None, "nw": None, "pitch": 0,
            "ref": variante["ref"], "f": trabajo["f"], "archivo": trabajo["archivo"],
            "ms": resultado["ms"], "ms_sox": 0.0, "rss_kb": salida["rss_max_kb"],
            "load_ms": salida["load_ms"], "rss_load_kb": salida["rss_load_kb"],
            "sr": resultado["sr"], "dur_s": resultado["dur_s"],
        })
    return filas


def generar(ronda: dict) -> list:
    variantes = ronda["variantes"]
    hay_kokoro = any(v.get("motor", "piper") == "kokoro" for v in variantes)
    hay_piper = any(v.get("motor", "piper") == "piper" for v in variantes)

    if hay_piper and not os.path.isdir(PIPER_DIR):
        sys.exit("no existe %s" % PIPER_DIR)
    if hay_kokoro:
        for ruta in (KOKORO_VENV_PY, KOKORO_HELPER, KOKORO_MODEL, KOKORO_VOCES):
            if not os.path.exists(ruta):
                sys.exit("falta para Kokoro: %s" % ruta)
    for variante in variantes:
        if variante.get("motor", "piper") == "piper":
            ruta = ruta_voz(variante["locale"], variante["voz"])
            if not os.path.exists(ruta):
                sys.exit("no existe la voz %s" % ruta)
    os.makedirs(ronda["salida"], exist_ok=True)

    resultados = []
    for variante in variantes:
        if variante.get("motor", "piper") == "kokoro":
            filas = sintetizar_kokoro(variante, ronda)
            for fila in filas:
                print("  %-52s kokoro synth %6.0f ms  carga %6.0f ms  "
                      "RSS %7d kB  %5.2f s" % (
                          fila["archivo"], fila["ms"], fila["load_ms"],
                          fila["rss_kb"], fila["dur_s"]), flush=True)
            resultados.extend(filas)
            continue
        for numero, frase in enumerate(ronda["frases"], 1):
            archivo = nombre_archivo(ronda, variante, numero)
            destino = os.path.join(ronda["salida"], archivo)
            datos = sintetizar(frase, variante, destino)
            print("  %-52s piper %6.0f ms  sox %5.0f ms  RSS %7d kB  %5.2f s" % (
                archivo, datos["ms"], datos["ms_sox"], datos["rss_kb"],
                datos["dur_s"]), flush=True)
            resultados.append({
                "id": variante["id"], "motor": "piper", "locale": variante["locale"],
                "voz": variante["voz"], "ls": variante["ls"], "ss": variante["ss"],
                "ns": variante["ns"], "nw": variante["nw"],
                "pitch": variante.get("pitch", 0), "ref": variante["ref"], "f": numero,
                "archivo": archivo, **datos,
            })
    return resultados


def memoria_por_motor(resultados: list) -> dict:
    memoria = {}
    for fila in resultados:
        clave = "%s/%s" % (fila.get("motor", "piper"), fila["voz"])
        actual = memoria.get(clave, -1)
        if fila["rss_kb"] > actual:
            memoria[clave] = fila["rss_kb"]
    return memoria


def _etiqueta_normal(fila: dict) -> str:
    if fila.get("motor") == "kokoro":
        cabecera = "Kokoro" if not fila["ref"] else "Kokoro (referencia)"
        return ("%s · voz %s · idioma %s · velocidad %s · síntesis %.2f s" % (
            cabecera, fila["voz"], fila.get("lang"), fila.get("speed"),
            fila["ms"] / 1000.0))
    if fila["ref"]:
        cabecera = "Voz actual (referencia) — %s" % fila["voz"]
    else:
        cabecera = fila["voz"]
    velocidad = VELOCIDAD.get(fila["ls"], fila["ls"])
    return ("%s · velocidad %s (%s) · pausa entre frases %s s · "
            "síntesis %.2f s" % (cabecera, fila["ls"], velocidad, fila["ss"],
                                 fila["ms"] / 1000.0))


def _cabecera_html(ronda: dict) -> list:
    partes = []
    partes.append("<!DOCTYPE html>")
    partes.append('<html lang="es"><head><meta charset="utf-8">')
    partes.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    partes.append("<title>%s</title>" % html.escape(
        ronda.get("titulo_pagina") or ronda["titulo"]))
    partes.append("<style>")
    partes.append(
        "body{font-family:-apple-system,system-ui,sans-serif;font-size:20px;"
        "line-height:1.5;margin:0;padding:16px;background:#f7f7f7;color:#111}")
    partes.append("h1{font-size:26px;margin:0 0 8px}")
    partes.append("h2{font-size:22px;margin:28px 0 8px;padding-top:12px;border-top:2px solid #ccc}")
    partes.append(".nota{font-size:16px;color:#555;margin:0 0 16px}")
    partes.append(".voz{background:#fff;border:1px solid #ddd;border-radius:10px;"
                  "padding:10px 12px;margin:10px 0}")
    partes.append(".voz.ref{border:2px solid #2a7;background:#eefbf4}")
    partes.append(".et{font-size:16px;color:#333;margin:0 0 6px}")
    partes.append(".ref .et{font-weight:700;color:#186}")
    partes.append(".letra{font-size:34px;font-weight:700;margin:0 0 6px}")
    partes.append("audio{width:100%}")
    partes.append("details{margin:28px 0 8px;background:#fff;border:1px solid #ddd;"
                  "border-radius:10px;padding:10px 12px}")
    partes.append("summary{font-size:20px;font-weight:700;cursor:pointer}")
    partes.append("table{border-collapse:collapse;width:100%;margin:8px 0 16px}")
    partes.append("th,td{border:1px solid #ccc;padding:4px 8px;text-align:left;font-size:16px}")
    partes.append("</style></head><body>")
    partes.append("<h1>%s</h1>" % html.escape(ronda["titulo"]))
    partes.append('<p class="nota">%s</p>' % html.escape(ronda["nota"]))
    return partes


def escribir_index_normal(ronda: dict, resultados: list) -> str:
    por_id = {}
    for fila in resultados:
        por_id.setdefault(fila["id"], {})[fila["f"]] = fila

    partes = _cabecera_html(ronda)
    for numero, frase in enumerate(ronda["frases"], 1):
        partes.append("<h2>Frase %d. %s</h2>" % (numero, html.escape(frase)))
        for variante in ronda["variantes"]:
            fila = por_id[variante["id"]][numero]
            clases = "voz ref" if fila["ref"] else "voz"
            partes.append('<div class="%s">' % clases)
            partes.append('<p class="et">%s</p>' % html.escape(_etiqueta_normal(fila)))
            partes.append('<audio controls preload="none" src="%s"></audio>'
                          % html.escape(fila["archivo"]))
            partes.append("</div>")
    partes.extend(_seccion_humo(ronda))
    partes.append("</body></html>\n")
    return _escribir(ronda["salida"], "\n".join(partes))


def orden_por_seccion(ronda: dict) -> list:
    """Orden barajado (indices de variante) de cada seccion, con semilla fija."""
    rnd = random.Random(SEMILLA_CIEGO)
    ordenes = []
    for _ in ronda["frases"]:
        indices = list(range(len(ronda["variantes"])))
        rnd.shuffle(indices)
        ordenes.append(indices)
    return ordenes


def _clave_variante(variante: dict) -> str:
    if variante.get("motor") == "kokoro":
        extra = "Kokoro, voz %s, idioma %s, velocidad %s" % (
            variante["voz"], variante.get("lang", KOKORO_LANG),
            variante.get("speed", 1.0))
    else:
        extra = "ls %s, ss %s, ns %s, nw %s" % (
            variante["ls"], variante["ss"], variante["ns"], variante["nw"])
        if variante.get("pitch"):
            extra += ", tono +%d cents" % int(variante["pitch"])
    return "%s · %s (%s)" % (variante["id"], variante["desc"], extra)


def escribir_index_ciego(ronda: dict, resultados: list) -> tuple:
    por_id = {}
    for fila in resultados:
        por_id.setdefault(fila["id"], {})[fila["f"]] = fila

    ordenes = orden_por_seccion(ronda)
    clave_secciones = []

    partes = _cabecera_html(ronda)
    for numero, frase in enumerate(ronda["frases"], 1):
        partes.append("<h2>Frase %d. %s</h2>" % (numero, html.escape(frase)))
        clave = []
        for posicion, indice in enumerate(ordenes[numero - 1], 1):
            variante = ronda["variantes"][indice]
            fila = por_id[variante["id"]][numero]
            letra = str(posicion)
            partes.append('<div class="voz">')
            partes.append('<p class="letra">%s</p>' % html.escape(letra))
            partes.append('<audio controls preload="none" src="%s"></audio>'
                          % html.escape(fila["archivo"]))
            partes.append("</div>")
            clave.append({"letra": letra, "id": variante["id"],
                          "clave": _clave_variante(variante)})
        clave_secciones.append({"f": numero, "frase": frase, "letras": clave})

    partes.append("<details>")
    partes.append("<summary>%s</summary>" % html.escape(
        ronda.get("resumen_clave") or "Ver qué es cada letra"))
    for seccion in clave_secciones:
        partes.append("<h2>Frase %d. %s</h2>" % (seccion["f"], html.escape(seccion["frase"])))
        partes.append("<table><tr><th>%s</th><th>Qué es</th></tr>"
                      % html.escape(ronda.get("etiqueta_col") or "Letra"))
        for item in seccion["letras"]:
            partes.append("<tr><td>%s</td><td>%s</td></tr>"
                          % (html.escape(item["letra"]), html.escape(item["clave"])))
        partes.append("</table>")
    partes.append("</details>")
    partes.extend(_seccion_humo(ronda))
    partes.append("</body></html>\n")
    ruta = _escribir(ronda["salida"], "\n".join(partes))
    return ruta, clave_secciones


def _seccion_humo(ronda: dict) -> list:
    """Seccion aparte, NO a ciegas, con audios etiquetados (p. ej. prueba de acento)."""
    humo = ronda.get("humo")
    if not humo:
        return []
    partes = ["<h2>%s</h2>" % html.escape(humo["titulo"])]
    partes.append('<p class="nota">%s</p>' % html.escape(humo["nota"]))
    for pista in humo["pistas"]:
        partes.append('<div class="voz">')
        partes.append('<p class="et">%s</p>' % html.escape(pista["etiqueta"]))
        partes.append('<audio controls preload="none" src="%s"></audio>'
                      % html.escape(pista["archivo"]))
        partes.append("</div>")
    return partes


def _escribir(salida: str, contenido: str) -> str:
    ruta = os.path.join(salida, "index.html")
    with open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write(contenido)
    return ruta


def main() -> int:
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("--ronda", choices=sorted(RONDAS), default="1",
                            help="ronda de muestras a generar (por defecto 1)")
    analizador.add_argument("--salida", default=None,
                            help="carpeta de salida (por defecto la de la ronda); "
                                 "útil para verificar sin tocar la carpeta buena")
    args = analizador.parse_args()
    ronda = RONDAS[args.ronda]
    ronda = {**ronda, "nombre": args.ronda}
    if args.salida:
        ronda = {**ronda, "salida": os.path.expanduser(args.salida)}

    print("Generando ronda %s en %s" % (args.ronda, ronda["salida"]))
    resultados = generar(ronda)
    memoria = memoria_por_motor(resultados)

    extra = {}
    if ronda["modo"] == "ciego":
        ruta, clave_secciones = escribir_index_ciego(ronda, resultados)
        extra["clave"] = clave_secciones
    else:
        ruta = escribir_index_normal(ronda, resultados)

    print("\nindex: %s" % ruta)
    print("WAV: %d" % len(resultados))
    print("\nMemoria maxima por motor/voz (kB):")
    for clave, kb in memoria.items():
        print("  %-24s %d" % (clave, kb))

    cargas = {}
    for fila in resultados:
        if fila.get("motor") == "kokoro" and fila.get("load_ms"):
            cargas[fila["id"]] = fila["load_ms"]
    if cargas:
        print("\nCarga del modelo Kokoro por variante (ms):")
        for vid, ms in cargas.items():
            print("  %-6s %6.0f" % (vid, ms))

    resumen = os.path.join(ronda["salida"], "resumen.json")
    with open(resumen, "w", encoding="utf-8") as archivo:
        json.dump({"ronda": args.ronda, "resultados": resultados,
                   "memoria_kb": memoria, **extra},
                  archivo, ensure_ascii=False, indent=2)
    print("\nresumen: %s" % resumen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
