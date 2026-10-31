#!/usr/bin/env python3
"""Herramienta local para decidir si el modelo ingles sirve.

Lee una carpeta de WAV (16 kHz, mono, 16 bits) y, por cada archivo, imprime
el nombre, el texto del reconocedor libre en ingles, el texto del reconocedor
libre en espanol y los milisegundos hasta el resultado final. No usa MQTT ni
WebSocket ni ningun servicio: solo carga los dos modelos Vosk en local.

Uso:
    python probar_ingles.py /ruta/a/wavs
    python probar_ingles.py /ruta/a/wavs --modelo-ingles /modelo/... --modelo-espanol /modelo/...
"""

import argparse
import json
import os
import sys
import time
import wave

import numpy as np
import vosk

vosk.SetLogLevel(-1)


def cargar_modelo(ruta):
    return vosk.Model(ruta)


def leer_pcm_16k(ruta):
    """Devuelve PCM 16 kHz mono 16 bits. Convierte en memoria si hace falta."""
    with wave.open(ruta, "rb") as w:
        canales = w.getnchannels()
        ancho = w.getsampwidth()
        frecuencia = w.getframerate()
        datos = w.readframes(w.getnframes())

    if ancho != 2:
        raise ValueError("ancho de muestra %d bits (se necesita 16)" % (8 * ancho))

    muestras = np.frombuffer(datos, dtype="<i2")
    if canales > 1:
        muestras = muestras.reshape(-1, canales).mean(axis=1).astype("<i2")
    if frecuencia != 16000:
        n_salida = int(round(len(muestras) * 16000 / frecuencia))
        indices = np.linspace(0, len(muestras) - 1, n_salida)
        muestras = np.interp(indices, np.arange(len(muestras)),
                             muestras.astype(np.float64)).astype("<i2")
    return muestras.astype("<i2").tobytes()


def reconocer(modelo, pcm):
    rec = vosk.KaldiRecognizer(modelo, 16000)
    t0 = time.time()
    segs = []
    for i in range(0, len(pcm), 3200):
        trama = pcm[i:i + 3200]
        if len(trama) < 3200:
            trama += b"\x00" * (3200 - len(trama))
        if rec.AcceptWaveform(trama):
            segs.append(json.loads(rec.Result()).get("text", ""))
    final = json.loads(rec.FinalResult()).get("text", "")
    if final:
        segs.append(final)
    texto = " ".join(s for s in segs if s).strip()
    return texto, int((time.time() - t0) * 1000)


def main():
    parser = argparse.ArgumentParser(description="Prueba local ES vs EN sobre WAV.")
    parser.add_argument("carpeta", help="carpeta con archivos .wav")
    parser.add_argument("--modelo-ingles", default=os.environ.get(
        "VOZ_MODELO_INGLES", "/modelo/vosk-model-small-en-us-0.15"))
    parser.add_argument("--modelo-espanol", default=os.environ.get(
        "VOZ_MODELO", "/modelo/vosk-model-small-es-0.42"))
    args = parser.parse_args()

    if not os.path.isdir(args.carpeta):
        print("no existe la carpeta: %s" % args.carpeta, file=sys.stderr)
        return 2

    modelo_en = cargar_modelo(args.modelo_ingles)
    modelo_es = cargar_modelo(args.modelo_espanol)

    wavs = sorted(f for f in os.listdir(args.carpeta)
                  if f.lower().endswith(".wav"))
    print("archivo\tingles\tespanol\tms_final")
    for nombre in wavs:
        ruta = os.path.join(args.carpeta, nombre)
        try:
            pcm = leer_pcm_16k(ruta)
        except Exception as error:  # noqa: BLE001
            print("%s\tERROR: %s" % (nombre, error))
            continue
        texto_en, ms_en = reconocer(modelo_en, pcm)
        texto_es, _ = reconocer(modelo_es, pcm)
        print("%s\t%s\t%s\t%d" % (nombre, texto_en, texto_es, ms_en))
    return 0


if __name__ == "__main__":
    sys.exit(main())
