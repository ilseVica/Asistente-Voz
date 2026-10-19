import 'package:shared_preferences/shared_preferences.dart';

/// El token y la bandera del aviso de privacidad, en `localStorage`.
///
/// `shared_preferences` en web es `localStorage`, que es por origen. Por eso
/// importa canjear siempre en el origen donde está abierto el panel: una sesión
/// iniciada en `villaca...` no sirve en `192.168.1.169`, y es la razón de que la
/// pantalla de acceso acepte el enlace completo y no sólo el código.
class Sesion {
  // `panel_token` y `panel_nombre` SON LAS CLAVES REALES que escribe la página
  // intermedia de `PAGINA_CANJE` en `panel/api.py`, con `localStorage.setItem`.
  // Si aquí se pusiera un nombre distinto, el canje funcionaría, la página saltaría
  // a `/` y el panel arrancaría sin sesión: el token existiría, pero debajo de otra
  // clave. Un nombre distinto entre dos escrituras del mismo dato es la forma más
  // silenciosa de romper un flujo que sólo se prueba a mano.
  static const String _claveToken = 'panel_token';
  static const String _claveNombre = 'panel_nombre';
  static const String _claveAviso = 'aviso_privacidad_visto';

  SharedPreferences? _prefs;

  Future<SharedPreferences> _obtener() async {
    _prefs ??= await SharedPreferences.getInstance();
    return _prefs!;
  }

  Future<String?> leerToken() async {
    final SharedPreferences prefs = await _obtener();
    return prefs.getString(_claveToken);
  }

  Future<void> guardarToken(String valor) async {
    final SharedPreferences prefs = await _obtener();
    await prefs.setString(_claveToken, valor);
  }

  Future<void> borrarToken() async {
    final SharedPreferences prefs = await _obtener();
    await prefs.remove(_claveToken);
    await prefs.remove(_claveNombre);
  }

  Future<void> guardarNombre(String valor) async {
    final SharedPreferences prefs = await _obtener();
    await prefs.setString(_claveNombre, valor);
  }

  Future<String?> leerNombre() async {
    final SharedPreferences prefs = await _obtener();
    return prefs.getString(_claveNombre);
  }

  /// Si ya se ha visto el aviso en este navegador.
  Future<bool> avisoVisto() async {
    final SharedPreferences prefs = await _obtener();
    return prefs.getBool(_claveAviso) ?? false;
  }

  Future<void> marcarAvisoVisto() async {
    final SharedPreferences prefs = await _obtener();
    await prefs.setBool(_claveAviso, true);
  }

  /// Para las pruebas, que necesitan un navegador limpio sin reiniciar nada.
  Future<void> limpiar() async {
    final SharedPreferences prefs = await _obtener();
    await prefs.remove(_claveToken);
    await prefs.remove(_claveAviso);
    await prefs.remove(_claveNombre);
  }
}

/// Normaliza el código de acceso que se teclea o se pega en la pantalla.
///
/// El código son SEIS caracteres del alfabeto `23456789ABCDEFGHJKMNPQRSTUVWXYZ`
/// (sin 0, 1, O, I ni L, para que no se confundan al dictarlo). Se acepta en
/// minúsculas y con espacios o guiones de más porque se copia y se pega a mano, y
/// una minúscula no debe ser un fallo. Devuelve `null` si, ya normalizado, no
/// tiene la forma exacta: la pantalla lo usa para avisar antes de molestar al
/// servidor, que es quien de verdad decide.
///
/// El servidor normaliza igual (mayúsculas y sin espacios), así que este filtro
/// no es la puerta: es para que un código incompleto no gaste un intento del
/// límite de accesos.
String? normalizarCodigo(String entrada) {
  final String limpio = entrada.replaceAll(RegExp(r'[\s\-_]'), '').toUpperCase();
  if (!RegExp(r'^[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}$').hasMatch(limpio)) {
    return null;
  }
  return limpio;
}
