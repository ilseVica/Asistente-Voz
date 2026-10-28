"""Medicion local aislada de Vosk (sin MQTT ni WebSocket).

Importa las funciones REALES de servidor_voz y arnes_wav (sintesis Piper).
"""
import os, sys, time, resource, tempfile, json
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRAMATICA = os.path.join(RAIZ, "servidor", "voz", "gramatica.json")
os.environ["VOZ_GRAMATICA"] = GRAMATICA

sys.path.insert(0, os.path.join(RAIZ, "servidor", "orquestador"))
sys.path.insert(0, os.path.join(RAIZ, "servidor", "voz"))
sys.path.insert(0, os.path.join(RAIZ, "herramientas"))

import numpy as np
import vosk
import servidor_voz as sv
import arnes_wav as arnes

PIPER_DIR = os.path.join(RAIZ, "herramientas", "piper")
VOZ = os.path.join(RAIZ, "herramientas", "voces", "es_MX-ald-medium.onnx")
GRAMATICA = os.path.join(RAIZ, "servidor", "voz", "gramatica.json")

def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

def normalizar(t):
    return arnes.normalizar(t)

antes_rss = rss_mb()
antes_cpu = resource.getrusage(resource.RUSAGE_SELF)
t0 = time.time()
modelo = vosk.Model(os.path.join(RAIZ, "servidor", "voz", "modelos", "vosk-model-small-es-0.42"))
frases, advertencias = sv.cargar_gramatica(modelo)
gramatica_json = json.dumps(frases, ensure_ascii=False)
rec_g, rec_l = sv.crear_reconocedores(modelo, gramatica_json)
despues_carga_rss = rss_mb()
print("=== CARGA ===")
print("modelo cargado en %.2f s" % (time.time() - t0))
print("RSS antes: %.1f MiB" % antes_rss)
print("RSS despues de modelo+2 reconocedores: %.1f MiB" % despues_carga_rss)
print("frases gramatica: %d  advertencias: %d" % (len(frases), len(advertencias)))

# Gramatica: 25 frases de intencion (sin SENUELO)
import json as _json
cfg = _json.load(open(GRAMATICA, encoding="utf-8"))
intenciones = cfg["intenciones"]
comandos = []
comando_set = set()
for k, v in intenciones.items():
    if k == "SENUELO":
        continue
    comandos.extend(v)
    for x in v:
        comando_set.add(normalizar(x))
print("\n=== GRAMATICA (%d frases) ===" % len(comandos))
aciertos = 0
for frase in comandos:
    pcm = arnes.sintetizar(frase, PIPER_DIR, VOZ)
    r1, r2 = sv.crear_reconocedores(modelo, gramatica_json)
    t = time.time()
    segs1=[]; segs2=[]
    for i in range(0, len(pcm), 3200):
        trama = pcm[i:i+3200]
        if len(trama) < 3200:
            trama += b"\x00" * (3200 - len(trama))
        a=sv._aceptar(r1, trama)
        if a: segs1.append(a)
        b=sv._aceptar(r2, trama)
        if b: segs2.append(b)
    texto = " ".join(segs1 + [sv._final(r1)]).strip()
    libre = " ".join(segs2 + [sv._final(r2)]).strip()
    lat = (time.time() - t) * 1000
    ok = normalizar(texto) == normalizar(frase)
    if ok:
        aciertos += 1
    print("  %-40s -> %-40s %s (%.0f ms)" % (frase, texto, "OK" if ok else "FAIL", lat))
print("Aciertos gramatica: %d/%d" % (aciertos, len(comandos)))

# Ajenas
fuera = arnes.FRASES_FUERA
print("\n=== AJENAS (%d frases) ===" % len(fuera))
mal = 0
for frase in fuera:
    pcm = arnes.sintetizar(frase, PIPER_DIR, VOZ)
    r1, r2 = sv.crear_reconocedores(modelo, gramatica_json)
    segs1=[]
    for i in range(0, len(pcm), 3200):
        trama = pcm[i:i+3200]
        if len(trama) < 3200:
            trama += b"\x00" * (3200 - len(trama))
        a=sv._aceptar(r1, trama)
        if a: segs1.append(a)
        sv._aceptar(r2, trama)
    texto = " ".join(segs1 + [sv._final(r1)]).strip()
    es_comando = normalizar(texto) in comando_set
    if es_comando:
        mal += 1
    print("  %-30s -> %-30s %s" % (frase, texto, "COLADO" if es_comando else "ok"))
print("Ajenas reconocidas como comando: %d/%d" % (mal, len(fuera)))

# Transcripcion libre
respuestas = [
    "estoy bien gracias",
    "si ya comi",
    "llamame mas tarde",
    "te quiero mucho",
    "ya me voy a dormir",
    "no tengo hambre",
    "todo bien por aqui",
    "gracias por preguntar",
    "estoy un poco cansado",
    "hasta manana",
    "que descanses",
    "te extrano",
]
print("\n=== TRANSCRIPCION LIBRE (%d respuestas) ===" % len(respuestas))
total_palabras = 0
correctas = 0
latencias = []
for frase in respuestas:
    pcm = arnes.sintetizar(frase, PIPER_DIR, VOZ)
    r1, r2 = sv.crear_reconocedores(modelo, gramatica_json)
    t = time.time()
    segs1=[]; segs2=[]
    for i in range(0, len(pcm), 3200):
        trama = pcm[i:i+3200]
        if len(trama) < 3200:
            trama += b"\x00" * (3200 - len(trama))
        a=sv._aceptar(r1, trama)
        if a: segs1.append(a)
        b=sv._aceptar(r2, trama)
        if b: segs2.append(b)
    texto = " ".join(segs1 + [sv._final(r1)]).strip()
    libre = " ".join(segs2 + [sv._final(r2)]).strip()
    lat = (time.time() - t) * 1000
    latencias.append(lat)
    esperadas = normalizar(frase).split()
    obtenidas = normalizar(libre).split()
    total_palabras += len(esperadas)
    correctas += sum(1 for w in esperadas if w in obtenidas)
    print("  esperado: %-30s | libre: %-30s | lat %.0f ms" % (frase, libre, lat))
print("Palabras correctas: %d/%d (%.1f%%)" % (correctas, total_palabras, 100.0*correctas/total_palabras))
print("Latencia media: %.0f ms  min %.0f  max %.0f" % (sum(latencias)/len(latencias), min(latencias), max(latencias)))

final_rss = rss_mb()
final_cpu = resource.getrusage(resource.RUSAGE_SELF)
print("\n=== RECURSOS ===")
print("RSS final: %.1f MiB" % final_rss)
print("CPU usuario: %.2f s  sistema: %.2f s" % (final_cpu.ru_utime, final_cpu.ru_stime))
