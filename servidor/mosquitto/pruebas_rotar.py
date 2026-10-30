import os, shutil, sys, tempfile, unittest
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RAIZ, "servidor", "mosquitto"))
import importlib.util
_spec=importlib.util.spec_from_file_location("rotar_claves", os.path.join(RAIZ,"servidor","mosquitto","rotar-claves.py"))
rotar_claves=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rotar_claves)


def leer_bytes(ruta):
    with open(ruta, "rb") as archivo:
        return archivo.read()

class TestRotarClaves(unittest.TestCase):
    def test_usuario_desconocido_falla(self):
        with self.assertRaises(SystemExit):
            rotar_claves.validar_usuarios(["no-existe"])

    def test_desconocido_no_crea_respaldos_ni_modifica_copias(self):
        with tempfile.TemporaryDirectory(prefix="prueba-rotar-") as temporal:
            env = os.path.join(temporal, ".env")
            passwd = os.path.join(temporal, "passwd")
            respaldos = os.path.join(temporal, "respaldos")
            with open(env, "w", encoding="utf-8") as archivo:
                archivo.write("MQTT_PANEL_PASSWORD=valor-de-prueba\n")
            with open(passwd, "w", encoding="utf-8") as archivo:
                archivo.write("panel:$6$hash-de-prueba\n")
            antes_env = leer_bytes(env)
            antes_passwd = leer_bytes(passwd)
            argv = sys.argv[:]
            env_original = rotar_claves.ENV
            try:
                rotar_claves.ENV = env
                sys.argv = ["rotar-claves.py", "desconocido"]
                with self.assertRaises(SystemExit):
                    rotar_claves.main()
            finally:
                rotar_claves.ENV = env_original
                sys.argv = argv
            self.assertEqual(leer_bytes(env), antes_env)
            self.assertEqual(leer_bytes(passwd), antes_passwd)
            self.assertFalse(os.path.exists(respaldos))

    def test_usuario_conocido_ok(self):
        rotar_claves.validar_usuarios(["panel"])

    def test_help_no_toca_archivos(self):
        argv = sys.argv[:]
        try:
            sys.argv = ["rotar-claves.py", "--help"]
            rc = rotar_claves.main()
            self.assertEqual(rc, 0)
        finally:
            sys.argv = argv

if __name__ == "__main__":
    unittest.main(verbosity=2)
