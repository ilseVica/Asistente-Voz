#!/usr/bin/env python3
"""Pruebas automaticas de prueba_audio.py (sin audio real)."""

import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prueba_audio
import satelite


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestEscucha(unittest.TestCase):
    def test_solo_127_0_0_1(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__))) as tmp:
            cfg = os.path.join(tmp, "config.toml")
            Path(cfg).write_text("[audio]\nentrada_nodo = \"x\"\n", encoding="utf-8")
            srv = prueba_audio.crear_servidor(puerto=0, config_path=cfg)
            try:
                self.assertEqual(srv.server_address[0], "127.0.0.1")
            finally:
                srv.server_close()


class TestConfigSegura(unittest.TestCase):
    CONFIG = """[servidor]
ws_url = "ws://secreto.local:2700"
token = "TOKEN_FALSO_123"
token_archivo = "/tmp/token"

[mqtt]
host = "mqtt.secreto"
password = "PASS_FALSA_456"
password_archivo = "/tmp/pass"

[audio]
entrada_nodo = "mic-real"
salida_nodo = "bocina-real"

[voz]
modelo = "/v/voz.onnx"
length_scale = 1.0

[rutas]
piper = "/bin/piper"

[tecla]
socket = "/tmp/tecla.sock"
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        self.cfg_path = os.path.join(self.tmp.name, "config.toml")
        Path(self.cfg_path).write_text(self.CONFIG, encoding="utf-8")
        prueba_audio.CONFIG_PATH = self.cfg_path

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_lee_secretos(self):
        cfg = prueba_audio.cargar_config()
        # Campos de conexion/secretos quedan por defecto, no del archivo.
        self.assertEqual(cfg.token, "")
        self.assertEqual(cfg.mqtt_password, "")
        self.assertNotEqual(cfg.ws_url, "ws://secreto.local:2700")
        self.assertNotEqual(cfg.mqtt_host, "mqtt.secreto")
        # Las secciones permitidas sí se leen.
        self.assertEqual(cfg.entrada_nodo, "mic-real")
        self.assertEqual(cfg.piper, "/bin/piper")
        self.assertEqual(cfg.voz, "/v/voz.onnx")

    def test_api_config_no_expone_secretos(self):
        data = prueba_audio.config_a_dict(prueba_audio.cargar_config())
        texto = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("TOKEN_FALSO_123", texto)
        self.assertNotIn("PASS_FALSA_456", texto)
        self.assertNotIn("ws_url", texto)
        self.assertNotIn("mqtt", texto)
        self.assertNotIn("servidor", texto)

    def test_api_dispositivos_no_expone_secretos(self):
        pw = json.dumps([{
            "info": {"props": {"node.name": "mic-real", "media.class": "Audio/Source",
                               "node.description": "Mic"}}
        }])
        with patch.object(prueba_audio, "run_command",
                          return_value=_Completed(0, pw, "")):
            data = prueba_audio.listar_dispositivos()
        texto = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("TOKEN_FALSO_123", texto)
        self.assertNotIn("PASS_FALSA_456", texto)


class TestNodosPipeWire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        cfg = os.path.join(self.tmp.name, "config.toml")
        Path(cfg).write_text("[audio]\nentrada_nodo = \"mic-actual\"\nsalida_nodo = \"bocina-actual\"\n",
                             encoding="utf-8")
        prueba_audio.CONFIG_PATH = cfg

    def tearDown(self):
        self.tmp.cleanup()

    def test_marca_actuales(self):
        pw = json.dumps([
            {"info": {"props": {"node.name": "mic-actual", "media.class": "Audio/Source",
                                "node.description": "Micrófono actual"}}},
            {"info": {"props": {"node.name": "mic-otro", "media.class": "Audio/Source",
                                "node.description": "Otro mic"}}},
            {"info": {"props": {"node.name": "bocina-actual", "media.class": "Audio/Sink",
                                "node.description": "Bocina actual"}}},
        ])
        with patch.object(prueba_audio, "run_command",
                          return_value=_Completed(0, pw, "")):
            data = prueba_audio.listar_dispositivos()
        fuentes = {n["nombre"]: n for n in data["fuentes"]}
        salidas = {n["nombre"]: n for n in data["salidas"]}
        self.assertTrue(fuentes["mic-actual"]["actual"])
        self.assertFalse(fuentes["mic-otro"]["actual"])
        self.assertTrue(salidas["bocina-actual"]["actual"])


class _FakePopen:
    """Simula pw-record: no termina hasta recibir SIGINT."""

    def __init__(self, args, stdout=None, stderr=None, text=False, **kwargs):
        self.args = list(args)
        self._stderr_text = ""
        self._terminado = False
        self.returncode = None
        self._ignore_sigint = kwargs.pop("_ignore_sigint", False)
        self._fail_stderr = kwargs.pop("_fail_stderr", None)
        self._write_on_signal = kwargs.pop("_write_on_signal", True)
        self._exit_before_signal = kwargs.pop("_exit_before_signal", False)
        self._stderr_text = kwargs.pop("_stderr_text", "")
        if self._exit_before_signal:
            self._terminado = True
            self.returncode = kwargs.pop("_returncode", 1)
            import io
            self.stderr = io.StringIO(self._stderr_text)
        # stderr como archivo de texto
        import io
        self.stderr = io.StringIO()

    def send_signal(self, sig):
        import signal as _signal
        if sig != _signal.SIGINT:
            self.returncode = -sig
            self._terminado = True
            return
        if self._ignore_sigint:
            return
        if self._fail_stderr is not None:
            self._stderr_text = self._fail_stderr
            self.stderr = __import__("io").StringIO(self._stderr_text)
            self.returncode = 1
            self._terminado = True
            return
        if self._write_on_signal:
            import wave as _w, struct as _s
            ruta = self.args[-1]
            with _w.open(ruta, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(_s.pack("<h", 10000) * 16000)
        self._stderr_text = ""
        self.stderr = __import__("io").StringIO("")
        self.returncode = 1
        self._terminado = True

    def communicate(self, timeout=None):
        import subprocess as _sp
        if not self._terminado:
            raise _sp.TimeoutExpired(self.args, timeout)
        return ("", self._stderr_text)

    def wait(self, timeout=None):
        import subprocess as _sp
        if not self._terminado:
            raise _sp.TimeoutExpired(self.args, timeout)
        return self.returncode

    def terminate(self):
        if not self._terminado:
            self.returncode = -15
            self._terminado = True

    def kill(self):
        if not self._terminado:
            self.returncode = -9
            self._terminado = True

    def poll(self):
        return self.returncode if self._terminado else None


class TestGrabacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        self.runtime = os.path.join(self.tmp.name, "run")
        os.makedirs(self.runtime)
        self.old_xdg = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.runtime
        cfg = os.path.join(self.tmp.name, "config.toml")
        Path(cfg).write_text("[audio]\n", encoding="utf-8")
        prueba_audio.CONFIG_PATH = cfg
        self._old_seg = prueba_audio.SEGUNDOS_GRABACION
        prueba_audio.SEGUNDOS_GRABACION = 0

    def tearDown(self):
        if self.old_xdg is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self.old_xdg
        prueba_audio.SEGUNDOS_GRABACION = getattr(self, "_old_seg", 5)
        self.tmp.cleanup()

    def _patch_popen(self, **kwargs):
        def factory(*a, **kw):
            kw = dict(kw)
            kw.update(kwargs)
            # args passed as first positional
            args = a[0] if a else kw.pop("args", [])
            return _FakePopen(args, **kw)
        return factory

    def test_grabacion_comando_y_nivel(self):
        llamadas = []

        def factory(args, **kw):
            llamadas.append(list(args))
            return _FakePopen(args, **kw)

        with patch.object(prueba_audio.subprocess, "Popen", side_effect=factory):
            res = prueba_audio.grabar_fuente("mic-prueba")
        self.assertTrue(res["ok"])
        self.assertEqual(llamadas[0][:6], ["pw-record", "--target", "mic-prueba",
                                           "--rate", "16000", "--channels"])
        self.assertEqual(res["pico"], 10000 / 32768.0)
        self.assertFalse(res["silencio"])
        self.assertFalse(res["saturada"])
        self.assertTrue(Path(res["ruta"]).parent == Path(self.runtime))

    def test_grabacion_silencio(self):
        def factory(args, **kw):
            # escribir silencio al recibir SIGINT
            class P(_FakePopen):
                def send_signal(self, sig):
                    import signal as _s
                    if sig == _s.SIGINT:
                        import wave as _w
                        with _w.open(self.args[-1], "wb") as w:
                            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                            w.writeframes(b"\x00\x00" * 16000)
                        self.returncode = 0
                        self._terminado = True
                    else:
                        super().send_signal(sig)
            return P(args, **kw)
        with patch.object(prueba_audio.subprocess, "Popen", side_effect=factory):
            res = prueba_audio.grabar_fuente("mic-prueba")
        self.assertTrue(res["silencio"])
        self.assertIn("silencio", res["aviso"])

    def test_grabacion_saturada(self):
        def factory(args, **kw):
            class P(_FakePopen):
                def send_signal(self, sig):
                    import signal as _s, struct as _st, wave as _w
                    if sig == _s.SIGINT:
                        with _w.open(self.args[-1], "wb") as w:
                            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                            w.writeframes(_st.pack("<h", 32767) * 16000)
                        self.returncode = 0
                        self._terminado = True
                    else:
                        super().send_signal(sig)
            return P(args, **kw)
        with patch.object(prueba_audio.subprocess, "Popen", side_effect=factory):
            res = prueba_audio.grabar_fuente("mic-prueba")
        self.assertTrue(res["saturada"])

    def test_grabacion_ignora_sigint(self):
        def factory(args, **kw):
            return _FakePopen(args, _ignore_sigint=True, **kw)
        with patch.object(prueba_audio.subprocess, "Popen", side_effect=factory):
            res = prueba_audio.grabar_fuente("mic-prueba")
        self.assertFalse(res["ok"])

    def test_grabacion_stderr_error(self):
        def factory(args, **kw):
            return _FakePopen(args, _fail_stderr="pw-record: error de dispositivo", **kw)
        with patch.object(prueba_audio.subprocess, "Popen", side_effect=factory):
            res = prueba_audio.grabar_fuente("mic-prueba")
        self.assertFalse(res["ok"])
        self.assertIn("error de dispositivo", res["error"])

    def test_grabacion_sale_antes_de_senial(self):
        def factory(args, **kw):
            return _FakePopen(args, _exit_before_signal=True,
                              _stderr_text="pw-record: nodo inexistente", **kw)
        with patch.object(prueba_audio.subprocess, "Popen", side_effect=factory):
            res = prueba_audio.grabar_fuente("mic-prueba")
        self.assertFalse(res["ok"])
        self.assertIn("nodo inexistente", res["error"])

class TestReproduccion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        self.runtime = os.path.join(self.tmp.name, "run")
        os.makedirs(self.runtime)
        self.old_xdg = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.runtime
        cfg = os.path.join(self.tmp.name, "config.toml")
        Path(cfg).write_text("[audio]\n", encoding="utf-8")
        prueba_audio.CONFIG_PATH = cfg
        self._old_seg = prueba_audio.SEGUNDOS_GRABACION
        prueba_audio.SEGUNDOS_GRABACION = 0

    def tearDown(self):
        if self.old_xdg is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self.old_xdg
        self.tmp.cleanup()

    def test_pw_play_con_salida(self):
        ruta = os.path.join(self.runtime, "g.wav")
        Path(ruta).write_bytes(b"x")
        prueba_audio.GRABACIONES["abc"] = ruta
        llamadas = []

        def fake(args, **kwargs):
            llamadas.append(list(args))
            return _Completed(0, "", "")

        with patch.object(prueba_audio, "run_command", side_effect=fake):
            res = prueba_audio.reproducir_grabacion("abc", "salida-x")
        self.assertTrue(res["ok"])
        self.assertEqual(llamadas[0][:4], ["pw-play", "--target", "salida-x", ruta])


class TestVoz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        cfg = os.path.join(self.tmp.name, "config.toml")
        Path(cfg).write_text(
            '[audio]\nentrada_nodo = "m"\n[rutas]\npiper = "/bin/piper"\n[voz]\nmodelo = "/v/voz.onnx"\n',
            encoding="utf-8")
        prueba_audio.CONFIG_PATH = cfg

    def tearDown(self):
        self.tmp.cleanup()

    def test_usa_reproductor_y_mide(self):
        class FakeRep:
            def __init__(self, cfg, salida_nula=False):
                self.cfg = cfg
                self.salida_nula = salida_nula
                self.al_sintesis = None

            async def decir(self, texto, **kw):
                if self.al_sintesis is not None:
                    self.al_sintesis(12345)
                return 12345

            async def cerrar(self):
                pass

        with patch.object(prueba_audio.satelite, "Reproductor", FakeRep):
            with patch.object(prueba_audio.satelite, "reloj_ms", side_effect=[10000, 10100]):
                res = prueba_audio.decir_texto("hola")
        self.assertTrue(res["ok"])
        self.assertEqual(res["tiempo_ms"], 2345)


class TestHablar(unittest.IsolatedAsyncioTestCase):
    async def test_p_y_r(self):
        tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        sock_path = os.path.join(tmp.name, "tecla.sock")
        recibido = []

        async def handler(reader, writer):
            data = await reader.read(10)
            recibido.append(data)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handler, path=sock_path)
        cfg_path = os.path.join(tmp.name, "config.toml")
        Path(cfg_path).write_text('[tecla]\nsocket = "%s"\n' % sock_path, encoding="utf-8")
        prueba_audio.CONFIG_PATH = cfg_path
        try:
            r1 = prueba_audio.hablar_accion("presionar")
            r2 = prueba_audio.hablar_accion("soltar")
            self.assertTrue(r1["ok"])
            self.assertTrue(r2["ok"])
            await asyncio.sleep(0.1)
            self.assertEqual(recibido, [b"P", b"R"])
        finally:
            server.close()
            await server.wait_closed()
            tmp.cleanup()


class TestGuardarMic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        self.cfg_path = os.path.join(self.tmp.name, "config.toml")
        contenido = (
            "[audio]\n"
            'entrada_nodo = "viejo"\n'
            'salida_nodo = "salida"\n'
            "[mqtt]\n"
            'password = "NO_DEBE_CAMBIAR"\n'
        )
        Path(self.cfg_path).write_text(contenido, encoding="utf-8")
        prueba_audio.CONFIG_PATH = self.cfg_path

    def tearDown(self):
        self.tmp.cleanup()

    def test_solo_entrada_nodo_y_backup_600(self):
        res = prueba_audio.guardar_microfono("nuevo-mic")
        self.assertTrue(res["ok"])
        texto = Path(self.cfg_path).read_text(encoding="utf-8")
        self.assertIn('entrada_nodo = "nuevo-mic"', texto)
        self.assertIn('salida_nodo = "salida"', texto)
        self.assertIn('password = "NO_DEBE_CAMBIAR"', texto)
        st = os.stat(self.cfg_path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        backup = Path(res["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)
        original = backup.read_text(encoding="utf-8")
        self.assertIn('entrada_nodo = "viejo"', original)


class TestReinicio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        cfg = os.path.join(self.tmp.name, "config.toml")
        Path(cfg).write_text("[audio]\n", encoding="utf-8")
        prueba_audio.CONFIG_PATH = cfg

    def tearDown(self):
        self.tmp.cleanup()

    def test_sin_confirmacion_no_reinicia(self):
        llamadas = []

        def fake(args, **kwargs):
            llamadas.append(list(args))
            return _Completed(0, "", "")

        with patch.object(prueba_audio, "run_command", side_effect=fake):
            res = prueba_audio.reiniciar_servicio(False)
        self.assertFalse(res["ok"])
        self.assertEqual(llamadas, [])

    def test_con_confirmacion_reinicia(self):
        llamadas = []

        def fake(args, **kwargs):
            llamadas.append(list(args))
            return _Completed(0, "", "")

        with patch.object(prueba_audio, "run_command", side_effect=fake):
            res = prueba_audio.reiniciar_servicio(True)
        self.assertTrue(res["ok"])
        self.assertEqual(llamadas[0], ["systemctl", "--user", "restart", "asistente-satelite"])


class TestEnmascarar(unittest.TestCase):
    def test_oculta_secretos(self):
        linea = "password=abc123 token: xyz Bearer qwe"
        salida = prueba_audio.enmascarar(linea)
        self.assertNotIn("abc123", salida)
        self.assertNotIn("xyz", salida)
        self.assertNotIn("qwe", salida)


class TestHablarReal(unittest.IsolatedAsyncioTestCase):
    async def test_presionar_soltar_real(self):
        tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        old_xdg = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = tmp.name
        try:
            cfg_path = os.path.join(tmp.name, "config.toml")
            Path(cfg_path).write_text(
                '[tecla]\nsocket = "$XDG_RUNTIME_DIR/asistente-satelite.sock"\n',
                encoding="utf-8")
            prueba_audio.CONFIG_PATH = cfg_path

            class Nodo:
                def __init__(self):
                    self.veces = []

                def presionar(self):
                    self.veces.append("presionar")

                def soltar(self):
                    self.veces.append("soltar")

            nodo = Nodo()
            socket_path = os.path.join(tmp.name, "asistente-satelite.sock")
            server = await satelite.ServidorTecla(socket_path, nodo).servir()
            try:
                r1 = prueba_audio.hablar_accion("presionar")
                r2 = prueba_audio.hablar_accion("soltar")
                await asyncio.sleep(0.1)
                self.assertTrue(r1["ok"], r1)
                self.assertTrue(r2["ok"], r2)
                self.assertEqual(nodo.veces, ["presionar", "soltar"])
            finally:
                server.close()
                await server.wait_closed()
        finally:
            if old_xdg is None:
                os.environ.pop("XDG_RUNTIME_DIR", None)
            else:
                os.environ["XDG_RUNTIME_DIR"] = old_xdg
            tmp.cleanup()

    async def test_socket_inexistente(self):
        tmp = tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.dirname(__file__)))
        old_xdg = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = tmp.name
        try:
            cfg_path = os.path.join(tmp.name, "config.toml")
            Path(cfg_path).write_text(
                '[tecla]\nsocket = "$XDG_RUNTIME_DIR/no-existe.sock"\n',
                encoding="utf-8")
            prueba_audio.CONFIG_PATH = cfg_path
            ruta = os.path.join(tmp.name, "no-existe.sock")
            res = prueba_audio.hablar_accion("presionar")
            self.assertFalse(res["ok"])
            self.assertIn(ruta, res["error"])
        finally:
            if old_xdg is None:
                os.environ.pop("XDG_RUNTIME_DIR", None)
            else:
                os.environ["XDG_RUNTIME_DIR"] = old_xdg
            tmp.cleanup()



if __name__ == "__main__":
    unittest.main()
