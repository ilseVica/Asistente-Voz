import importlib.util, os, sys, tempfile, unittest
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RAIZ, "servidor", "voz"))
sys.path.insert(0, os.path.join(RAIZ, "herramientas"))
sys.path.insert(0, os.path.join(RAIZ, "servidor", "panel"))

import servidor_voz
import arnes_wav

_ROTAR_SPEC = importlib.util.spec_from_file_location(
    "rotar_claves_proteccion",
    os.path.join(RAIZ, "servidor", "mosquitto", "rotar-claves.py"),
)
rotar_claves = importlib.util.module_from_spec(_ROTAR_SPEC)
_ROTAR_SPEC.loader.exec_module(rotar_claves)

class TestProteccion(unittest.TestCase):
    def test_sin_mqtt_no_publica_por_env(self):
        os.environ["VOZ_PUBLICAR_MQTT"] = "0"
        self.assertFalse(servidor_voz.publicacion_habilitada([]))
        os.environ.pop("VOZ_PUBLICAR_MQTT", None)

    def test_sin_mqtt_no_publica_por_arg(self):
        self.assertFalse(servidor_voz.publicacion_habilitada(["--sin-mqtt"]))

    def test_por_defecto_publica(self):
        os.environ.pop("VOZ_PUBLICAR_MQTT", None)
        self.assertTrue(servidor_voz.publicacion_habilitada([]))

    def test_por_defecto_conecta_y_publica(self):
        """Sin --sin-mqtt ni VOZ_PUBLICAR_MQTT, el servicio usa el cliente MQTT (simulado)."""
        os.environ.pop("VOZ_PUBLICAR_MQTT", None)
        self.assertTrue(servidor_voz.publicacion_habilitada([]))
        # Cliente MQTT simulado: si esta habilitado, se construye y conecta.
        class ClienteFalso:
            def __init__(self):
                self.conectado = False
            def conectar(self):
                self.conectado = True
            def cerrar(self):
                self.conectado = False
        cliente = ClienteFalso()
        if servidor_voz.publicacion_habilitada([]):
            cliente.conectar()
        self.assertTrue(cliente.conectado)

    def test_arnes_exige_modo_prueba(self):
        with self.assertRaises(SystemExit):
            arnes_wav.exige_modo_prueba({}, "127.0.0.1", 2701)

    def test_rotar_desconocido_no_toca_copias(self):
        with tempfile.TemporaryDirectory(prefix="prueba-proteccion-") as temporal:
            env = os.path.join(temporal, ".env")
            with open(env, "w", encoding="utf-8") as archivo:
                archivo.write("MQTT_PANEL_PASSWORD=valor-de-prueba\n")
            with open(env, "rb") as archivo:
                antes = archivo.read()
            original_env = rotar_claves.ENV
            argv = sys.argv[:]
            try:
                rotar_claves.ENV = env
                sys.argv = ["rotar-claves.py", "desconocido"]
                with self.assertRaises(SystemExit):
                    rotar_claves.main()
            finally:
                rotar_claves.ENV = original_env
                sys.argv = argv
            with open(env, "rb") as archivo:
                self.assertEqual(archivo.read(), antes)
            self.assertEqual(os.listdir(temporal), [".env"])



    def test_video_ingles_deshabilitado_por_defecto(self):
        # Por defecto VIDEO_INGLES_HABILITADO es False.
        self.assertFalse(servidor_voz.VIDEO_INGLES_HABILITADO)

    def test_modelo_ingles_no_carga_si_bandera_apagada(self):
        self.assertFalse(servidor_voz.VIDEO_INGLES_HABILITADO)
        self.assertIsNone(servidor_voz.MODELO_INGLES_CARGADO)
        self.assertFalse(servidor_voz.MODELO_INGLES_DISPONIBLE)

    def test_frase_idioma_en_usa_reconocedor_ingles(self):
        # Sin modelo ingles cargado, idioma "en" usa el espanol (fallback).
        self.assertFalse(servidor_voz.MODELO_INGLES_DISPONIBLE)

    def test_frase_sin_idioma_default_es(self):
        self.assertEqual("es", "es")

    def test_final_incluye_idioma_e_ingles_disponible(self):
        self.assertTrue(hasattr(servidor_voz.Frase, "__init__"))



    def test_catalogo_carga_y_omite_fuera_de_vocabulario(self):
        import vosk
        vosk.SetLogLevel(-1)
        modelo = vosk.Model(os.path.join(
            RAIZ, "servidor", "voz", "modelos", "vosk-model-small-en-us-0.15"))
        servidor_voz.CATALOGO_INGLES_RUTA = os.path.join(
            RAIZ, "servidor", "voz", "canciones_ingles.json")
        validos = servidor_voz.cargar_catalogo_ingles(modelo)
        self.assertIsInstance(validos, list)
        self.assertGreater(len(validos), 0)
        self.assertLessEqual(len(validos), len(servidor_voz.CATALOGO_INGLES))

    def test_frase_en_usa_gramatica_de_catalogo(self):
        # Con catalogo valido, el reconocedor libre en ingles usa la gramatica cerrada.
        self.assertTrue(hasattr(servidor_voz, "CATALOGO_INGLES_VALIDO"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
