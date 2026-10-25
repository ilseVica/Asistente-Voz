#!/usr/bin/env python3
"""Prueba de audio del satelite, pagina local para la laptop.

Solo escucha en 127.0.0.1. Usa la biblioteca estandar de Python.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import datetime
import json
import math
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import satelite

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CONFIG_PATH = "~/.config/asistente-satelite/config.toml"
SEGUNDOS_GRABACION = 5
GRABACIONES: dict[str, str] = {}
ULTIMA_GRABACION: str | None = None
CONFIG_PATH = os.path.expanduser(DEFAULT_CONFIG_PATH)


def run_command(args, timeout=None, input=None):
    return subprocess.run(args, input=input, capture_output=True, text=True,
                         timeout=timeout, check=False)


def runtime_dir() -> Path:
    d = os.environ.get("XDG_RUNTIME_DIR")
    if not d:
        d = "/run/user/%d" % os.getuid()
    p = Path(d)
    if not p.is_dir():
        raise RuntimeError("XDG_RUNTIME_DIR no existe: %s" % d)
    return p


def cargar_config():
    return satelite.cargar_config_publica(CONFIG_PATH)


def config_a_dict(cfg):
    return {
        "audio": {
            "entrada_nodo": cfg.entrada_nodo,
            "salida_nodo": cfg.salida_nodo,
            "latencia_captura": cfg.latencia_captura,
            "altura_max_s": cfg.altura_max_s,
        },
        "voz": {
            "modelo": cfg.voz,
            "length_scale": cfg.length_scale,
            "sentence_silence": cfg.sentence_silence,
            "noise_scale": cfg.noise_scale,
            "noise_w": cfg.noise_w,
        },
        "rutas": {
            "piper": cfg.piper,
            "precargar_piper": cfg.precargar_piper,
        },
        "tecla": {
            "socket": cfg.socket_ruta,
        },
    }


def enmascarar(linea: str) -> str:
    linea = re.sub(
        r"(?i)(token|password|passwd|secret|api[_-]?key|contraseñ[aá])\s*[:=]\s*\S+",
        lambda m: "%s=***" % m.group(1), linea)
    linea = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer ***", linea)
    return linea


def listar_dispositivos():
    cfg = cargar_config()
    nodos = []
    try:
        res = run_command(["pw-dump"], timeout=15)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            for item in data:
                props = (item.get("info") or {}).get("props") or {}
                nombre = props.get("node.name")
                if not nombre:
                    continue
                media = props.get("media.class", "")
                if media == "Audio/Source":
                    tipo = "fuente"
                elif media == "Audio/Sink":
                    tipo = "salida"
                else:
                    continue
                descripcion = (props.get("node.description")
                                or props.get("node.nick")
                                or nombre)
                nodos.append({
                    "nombre": nombre,
                    "descripcion": descripcion,
                    "tipo": tipo,
                })
    except Exception:  # noqa: BLE001
        pass

    fuentes = []
    salidas = []
    for n in nodos:
        info = dict(n)
        if n["tipo"] == "fuente":
            info["actual"] = n["nombre"] == cfg.entrada_nodo
            fuentes.append(info)
        else:
            info["actual"] = n["nombre"] == cfg.salida_nodo
            salidas.append(info)
    return {"fuentes": fuentes, "salidas": salidas, "config": config_a_dict(cfg)}


def _toml_string(valor: str) -> str:
    return "\"" + valor.replace("\\", "\\\\").replace("\"", "\\\"") + "\""


def reemplazar_entrada_nodo(texto: str, nuevo: str):
    lineas = texto.splitlines(keepends=True)
    en_audio = False
    cambiado = False
    for i, linea in enumerate(lineas):
        s = linea.strip()
        if s.startswith("[") and s.endswith("]"):
            en_audio = (s == "[audio]")
            continue
        if en_audio:
            if re.match(r"^\s*entrada_nodo\s*=", linea):
                salto = "\n" if linea.endswith("\n") else ""
                lineas[i] = "entrada_nodo = %s%s" % (_toml_string(nuevo), salto)
                cambiado = True
                break
    return "".join(lineas), cambiado


def guardar_microfono(nodo: str):
    if not nodo or not isinstance(nodo, str):
        return {"ok": False, "error": "micrófono no indicado"}
    ruta = Path(CONFIG_PATH).expanduser()
    if not ruta.is_file():
        return {"ok": False, "error": "no existe el config"}
    texto = ruta.read_text(encoding="utf-8")
    fecha = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ruta.with_name("%s.respaldo-%s" % (ruta.name, fecha))
    contador = 1
    while backup.exists():
        backup = ruta.with_name("%s.respaldo-%s-%d" % (ruta.name, fecha, contador))
        contador += 1
    shutil.copy2(ruta, backup)
    os.chmod(backup, 0o600)

    nuevo_texto, cambiado = reemplazar_entrada_nodo(texto, nodo)
    if not cambiado:
        try:
            backup.unlink()
        except OSError:
            pass
        return {"ok": False, "error": "no se encontró entrada_nodo en [audio]"}

    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_text(nuevo_texto, encoding="utf-8")
    os.chmod(temporal, 0o600)
    os.replace(temporal, ruta)
    os.chmod(ruta, 0o600)
    return {"ok": True, "backup": str(backup)}


def analizar_wav(ruta: Path):
    with wave.open(str(ruta), "rb") as w:
        nframes = w.getnframes()
        rate = w.getframerate()
        canales = w.getnchannels()
        ancho = w.getsampwidth()
        raw = w.readframes(nframes)
    if ancho != 2:
        raise ValueError("se esperaba audio de 16 bits")
    muestras = array.array("h")
    muestras.frombytes(raw[:len(raw) // 2 * 2])
    if sys.byteorder == "big":
        muestras.byteswap()
    if canales > 1:
        mono = array.array("h")
        for i in range(0, len(muestras), canales):
            bloque = muestras[i:i + canales]
            mono.append(int(sum(bloque) / len(bloque)))
        muestras = mono
    if not muestras:
        pico = 0.0
        rms = 0.0
    else:
        pico = max(abs(x) for x in muestras) / 32768.0
        rms = math.sqrt(sum(x * x for x in muestras) / len(muestras)) / 32768.0
    duracion = nframes / rate if rate else 0.0
    silencio = pico < 0.01 or rms < 0.005
    saturada = pico >= 0.99
    return {
        "duracion_s": duracion,
        "pico": pico,
        "rms": rms,
        "silencio": silencio,
        "saturada": saturada,
    }


def grabar_fuente(nodo: str):
    if not nodo:
        return {"ok": False, "error": "micrófono no indicado"}
    try:
        base = runtime_dir()
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}
    gid = uuid.uuid4().hex
    ruta = base / ("prueba_audio_%s.wav" % gid)
    cmd = ["pw-record", "--target", nodo, "--rate", "16000",
           "--channels", "1", "--format", "s16", str(ruta)]
    try:
        proceso = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        return {"ok": False, "error": "no se pudo iniciar pw-record: %s" % error}

    senial_enviada = False
    # pw-record no termina solo: se deja grabar SEGUNDOS_GRABACION y se pide
    # que cierre. Su código de salida al recibir la señal no es fiable (pw-record
    # sale con 1 aunque el WAV quede bien), así que la decisión se toma por el
    # archivo resultante.
    time.sleep(SEGUNDOS_GRABACION)
    salio_antes = proceso.poll() is not None
    if not salio_antes:
        senial_enviada = True
        try:
            proceso.send_signal(signal.SIGINT)
        except OSError:
            pass
    stderr = ""
    try:
        _, stderr = proceso.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proceso.terminate()
            senial_enviada = True
            _, stderr = proceso.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proceso.kill()
                senial_enviada = True
                _, stderr = proceso.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stderr = stderr or ""
    if stderr is None:
        stderr = ""

    def _error(mensaje):
        codigo = proceso.returncode
        texto = (stderr or "").strip()
        if texto:
            lineas = [l for l in texto.splitlines() if l.strip()]
            texto = "\n".join(lineas[-5:])
        if texto:
            return {"ok": False, "error": "%s (código %s)" % (texto, codigo)}
        return {"ok": False, "error": "pw-record terminó con código %s" % codigo}

    # Si salió antes de la señal, es un error real (nodo inexistente, etc.).
    if not senial_enviada or salio_antes:
        return _error("pw-record terminó antes de tiempo")

    # Si llegó hasta aquí, se le envió la señal. Se acepta solo si el archivo
    # es un WAV legible con al menos ~1 s de audio.
    stats = None
    if ruta.is_file() and ruta.stat().st_size > 0:
        try:
            stats = analizar_wav(ruta)
        except Exception:  # noqa: BLE001
            stats = None
    if stats is None or stats["duracion_s"] < 1.0:
        return _error("no se generó una grabación válida")

    global ULTIMA_GRABACION
    ULTIMA_GRABACION = gid
    GRABACIONES[gid] = str(ruta)
    resultado = {"ok": True, "id": gid, "ruta": str(ruta)}
    resultado.update(stats)
    if stats["silencio"]:
        resultado["aviso"] = "La grabación quedó en silencio."
    elif stats["saturada"]:
        resultado["aviso"] = "La grabación está saturada."
    else:
        resultado["aviso"] = "Nivel correcto."
    return resultado

def reproducir_grabacion(grabacion_id: str, salida: str):
    ruta = GRABACIONES.get(grabacion_id)
    if not ruta or not Path(ruta).is_file():
        return {"ok": False, "error": "grabación no encontrada"}
    cmd = ["pw-play"]
    if salida:
        cmd += ["--target", salida]
    cmd += [ruta]
    try:
        res = run_command(cmd, timeout=60)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}
    return {"ok": res.returncode == 0, "error": res.stderr.strip()}


def decir_texto(texto: str):
    texto = (texto or "").strip()
    if not texto:
        return {"ok": False, "error": "texto vacío"}
    cfg = cargar_config()
    if not cfg.piper:
        return {"ok": False, "error": "falta rutas.piper en el config"}
    if not cfg.voz:
        return {"ok": False, "error": "falta voz.modelo en el config"}

    rep = satelite.Reproductor(cfg, salida_nula=False)
    synth_end = {"t": None}

    def hook(t):
        synth_end["t"] = t

    rep.al_sintesis = hook
    inicio_ms = satelite.reloj_ms()

    async def _run():
        try:
            t_sonido = await rep.decir(texto)
            await rep.cerrar()
            return t_sonido
        except Exception:
            try:
                await rep.cerrar()
            except Exception:  # noqa: BLE001
                pass
            raise

    try:
        t_sonido = asyncio.run(_run())
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}
    fin_ms = synth_end["t"] if synth_end["t"] is not None else t_sonido
    return {"ok": True, "tiempo_ms": fin_ms - inicio_ms}


def hablar_accion(accion: str):
    accion = (accion or "").lower()
    if accion not in ("presionar", "soltar"):
        return {"ok": False, "error": "acción no válida"}
    cfg = cargar_config()
    socket_path = cfg.socket_ruta or satelite._ruta_socket_defecto()
    letra = b"P" if accion == "presionar" else b"R"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(socket_path)
            s.sendall(letra)
    except OSError as error:
        return {"ok": False, "error": "no se pudo contactar al satélite: %s" % error}
    return {"ok": True}


def estado_servicio():
    try:
        res = run_command(["systemctl", "--user", "is-active", "asistente-satelite"],
                          timeout=10)
    except Exception as error:  # noqa: BLE001
        return {"activo": False, "estado": str(error), "lineas": []}
    estado = res.stdout.strip() or res.stderr.strip()
    activo = res.returncode == 0 and estado == "active"
    try:
        res2 = run_command(
            ["journalctl", "--user", "-u", "asistente-satelite", "-n", "20",
             "--no-pager", "--output=short"], timeout=15)
    except Exception as error:  # noqa: BLE001
        return {"activo": activo, "estado": estado, "lineas": [enmascarar(str(error))]}
    lineas = []
    if res2.returncode == 0:
        lineas = [enmascarar(l) for l in res2.stdout.splitlines()]
    else:
        lineas = [enmascarar(l) for l in (res2.stderr or "").splitlines()]
    return {"activo": activo, "estado": estado, "lineas": lineas}


def reiniciar_servicio(confirmado: bool):
    if not confirmado:
        return {"ok": False, "error": "falta confirmación"}
    try:
        res = run_command(["systemctl", "--user", "restart", "asistente-satelite"],
                          timeout=30)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}
    return {"ok": res.returncode == 0, "error": res.stderr.strip()}


HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prueba de audio del satélite</title>
<style>
body { font-family: system-ui, sans-serif; font-size: 1.25rem; margin: 1.5rem; }
h1 { font-size: 2rem; }
h2 { font-size: 1.6rem; margin-top: 1.5rem; }
button { font-size: 1.25rem; min-height: 3rem; padding: 0.8rem 1.2rem; margin: 0.4rem 0.4rem 0.4rem 0; cursor: pointer; }
select, textarea, input { font-size: 1.2rem; padding: 0.5rem; }
textarea { width: 100%; min-height: 5rem; }
section { border: 1px solid #ccc; border-radius: 0.75rem; padding: 1rem; margin: 1rem 0; }
.row { display: flex; flex-wrap: wrap; gap: 0.8rem; align-items: center; }
.meter { width: 100%; height: 1.5rem; }
pre { font-size: 1rem; white-space: pre-wrap; background: #f4f4f4; padding: 0.8rem; border-radius: 0.5rem; max-height: 20rem; overflow: auto; }
.aviso { font-weight: bold; }
.ok { color: #0a7; }
.bad { color: #c33; }
</style>
</head>
<body>
<h1>Prueba de audio del satélite</h1>

<section>
<h2>1. Dispositivos</h2>
<div class="row">
<button onclick="cargarDispositivos()">Actualizar dispositivos</button>
</div>
<div class="row">
<label>Fuentes (micrófonos):</label>
<select id="fuente"></select>
</div>
<div class="row">
<label>Salidas (bocinas):</label>
<select id="salida"></select>
</div>
<div id="infoConfig"></div>
</section>

<section>
<h2>2. Micrófono</h2>
<div class="row">
<button onclick="grabar()">Grabar 5 s</button>
<button onclick="escuchar()">Escuchar</button>
</div>
<progress id="medidor" class="meter" max="1" value="0"></progress>
<div id="nivel"></div>
</section>

<section>
<h2>3. Voz</h2>
<textarea id="texto">Buenos días. Esta es una prueba de la voz del asistente.</textarea>
<div class="row">
<button onclick="decir()">Decir con la voz del asistente</button>
</div>
<div id="vozResultado"></div>
</section>

<section>
<h2>4. Hablar con el asistente</h2>
<button id="btnHablar">Mantener para hablar</button>
<div id="hablarEstado"></div>
</section>

<section>
<h2>5. Estado del satélite</h2>
<div class="row">
<button onclick="actualizarEstado()">Actualizar</button>
</div>
<div id="estado"></div>
<pre id="journal"></pre>
</section>

<section>
<h2>6. Guardar micrófono</h2>
<div class="row">
<button onclick="usarMic()">Usar este micrófono en el asistente</button>
</div>
<div id="guardar"></div>
<div class="row">
<button id="btnReiniciar" style="display:none" onclick="reiniciar()">Reiniciar el asistente</button>
</div>
</section>

<script>
let ultimoId = null;
let presionado = false;

async function cargarDispositivos() {
  const r = await fetch('/api/dispositivos');
  const d = await r.json();
  const selF = document.getElementById('fuente');
  const selS = document.getElementById('salida');
  selF.innerHTML = '';
  selS.innerHTML = '';
  (d.fuentes || []).forEach(n => {
    const o = document.createElement('option');
    o.value = n.nombre;
    o.textContent = n.descripcion + (n.actual ? ' (actual)' : '');
    if (n.actual) o.selected = true;
    selF.appendChild(o);
  });
  (d.salidas || []).forEach(n => {
    const o = document.createElement('option');
    o.value = n.nombre;
    o.textContent = n.descripcion + (n.actual ? ' (actual)' : '');
    if (n.actual) o.selected = true;
    selS.appendChild(o);
  });
  document.getElementById('infoConfig').textContent =
    'Entrada actual: ' + (d.config.audio.entrada_nodo || '(vacía)') +
    ' | Salida actual: ' + (d.config.audio.salida_nodo || '(vacía)');
}

async function grabar() {
  const fuente = document.getElementById('fuente').value;
  document.getElementById('nivel').textContent = 'Grabando 5 segundos...';
  const r = await fetch('/api/grabar', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({nodo: fuente})
  });
  const d = await r.json();
  if (!d.ok) { document.getElementById('nivel').textContent = 'Error: ' + d.error; return; }
  ultimoId = d.id;
  document.getElementById('medidor').value = d.pico;
  const txt = 'Pico: ' + (d.pico*100).toFixed(1) + '% | RMS: ' + (d.rms*100).toFixed(1) + '% | Duración: ' + d.duracion_s.toFixed(2) + ' s';
  const el = document.getElementById('nivel');
  el.textContent = txt + ' — ' + d.aviso;
  el.className = d.silencio || d.saturada ? 'bad' : 'ok';
}

async function escuchar() {
  if (!ultimoId) { alert('Primero grabe una prueba.'); return; }
  const salida = document.getElementById('salida').value;
  const r = await fetch('/api/reproducir', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: ultimoId, salida: salida})
  });
  const d = await r.json();
  if (!d.ok) alert('No se pudo reproducir: ' + d.error);
}

async function decir() {
  const texto = document.getElementById('texto').value;
  document.getElementById('vozResultado').textContent = 'Sintetizando...';
  const r = await fetch('/api/decir', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({texto: texto})
  });
  const d = await r.json();
  if (!d.ok) document.getElementById('vozResultado').textContent = 'Error: ' + d.error;
  else document.getElementById('vozResultado').textContent = 'Tiempo de síntesis: ' + d.tiempo_ms + ' ms';
}

function presionar() {
  if (presionado) return;
  presionado = true;
  document.getElementById('hablarEstado').textContent = 'Hablando...';
  fetch('/api/hablar', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({accion: 'presionar'})});
}
function soltar() {
  if (!presionado) return;
  presionado = false;
  document.getElementById('hablarEstado').textContent = 'Suelto.';
  fetch('/api/hablar', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({accion: 'soltar'})});
}

const btnHablar = document.getElementById('btnHablar');
btnHablar.addEventListener('mousedown', presionar);
btnHablar.addEventListener('mouseup', soltar);
btnHablar.addEventListener('mouseleave', soltar);
btnHablar.addEventListener('touchstart', (e) => { e.preventDefault(); presionar(); });
btnHablar.addEventListener('touchend', (e) => { e.preventDefault(); soltar(); });
btnHablar.addEventListener('touchcancel', soltar);
window.addEventListener('blur', soltar);

async function actualizarEstado() {
  const r = await fetch('/api/estado');
  const d = await r.json();
  document.getElementById('estado').textContent =
    'Servicio: ' + d.estado + (d.activo ? ' (activo)' : ' (no activo)');
  document.getElementById('journal').textContent = (d.lineas || []).join('\n');
}

async function usarMic() {
  const nodo = document.getElementById('fuente').value;
  const r = await fetch('/api/guardar_microfono', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({nodo: nodo})
  });
  const d = await r.json();
  const el = document.getElementById('guardar');
  if (!d.ok) { el.textContent = 'Error: ' + d.error; el.className = 'bad'; return; }
  el.textContent = 'Micrófono guardado. Respaldo: ' + d.backup;
  el.className = 'ok';
  document.getElementById('btnReiniciar').style.display = 'inline-block';
}

async function reiniciar() {
  if (!confirm('¿Reiniciar el asistente ahora?')) return;
  const r = await fetch('/api/reiniciar', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({confirmar: true})
  });
  const d = await r.json();
  if (!d.ok) alert('No se pudo reiniciar: ' + d.error);
  else alert('Reinicio solicitado.');
}

cargarDispositivos();
actualizarEstado();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        ruta = urlparse(self.path).path
        if ruta == "/":
            self._send_html(HTML)
        elif ruta == "/api/dispositivos":
            self._send_json(listar_dispositivos())
        elif ruta == "/api/estado":
            self._send_json(estado_servicio())
        elif ruta == "/api/config":
            self._send_json(config_a_dict(cargar_config()))
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        ruta = urlparse(self.path).path
        if ruta == "/api/grabar":
            self._send_json(grabar_fuente(body.get("nodo", "")))
        elif ruta == "/api/reproducir":
            self._send_json(reproducir_grabacion(body.get("id", ""),
                                                  body.get("salida", "")))
        elif ruta == "/api/decir":
            self._send_json(decir_texto(body.get("texto", "")))
        elif ruta == "/api/hablar":
            self._send_json(hablar_accion(body.get("accion", "")))
        elif ruta == "/api/guardar_microfono":
            self._send_json(guardar_microfono(body.get("nodo", "")))
        elif ruta == "/api/reiniciar":
            self._send_json(reiniciar_servicio(bool(body.get("confirmar", False))))
        else:
            self.send_error(404)

    def log_message(self, *args):
        return


def crear_servidor(puerto=DEFAULT_PORT, config_path=DEFAULT_CONFIG_PATH):
    global CONFIG_PATH
    CONFIG_PATH = os.path.expanduser(config_path)
    return ThreadingHTTPServer((HOST, puerto), Handler)


def main():
    global CONFIG_PATH
    parser = argparse.ArgumentParser(description="Prueba de audio del satélite")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--puerto", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    CONFIG_PATH = os.path.expanduser(args.config)
    servidor = ThreadingHTTPServer((HOST, args.puerto), Handler)
    print("Prueba de audio del satélite: http://%s:%d/" % (HOST, args.puerto))
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
