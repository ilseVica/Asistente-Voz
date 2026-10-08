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

/// De un enlace de acceso al código.
///
/// Acepta dos cosas, y la razón es práctica y no estética:
///
///  - El enlace completo. En algunos Android el enlace de Telegram abre un
///    navegador distinto del que está autenticado, y sin poder pegar el enlace a
///    mano la sesión se pierde. El código es el último segmento de la URL.
///  - El código suelto, por si alguien lo teclea a mano.
///
/// El canje se hace SIEMPRE en el origen donde está abierto el panel, porque el
/// `localStorage` es por origen: canjear en el origen abierto es lo que hace que
/// pegar el enlace funcione igual en `villaca...` y en `192.168.1.169`.
String? codigoDeEnlace(String entrada) {
  final String limpio = entrada.trim();
  if (limpio.isEmpty) return null;

  final String ultima;
  if (limpio.contains('/')) {
    // Se corta por `/` o por `?` o por `#`: el código nunca lleva ninguno de los
    // tres, y un enlace de Telegram puede llevar los otros dos detrás.
    final String fin = limpio
        .replaceAll(RegExp(r'[?#].*$'), '')
        .split('/')
        .last
        .trim();
    ultima = fin;
  } else {
    ultima = limpio;
  }

  if (ultima.isEmpty) return null;
  // Sólo letras, dígitos y guiones bajos: el código de canje es alfanumérico, y
  // filtrar aquí evita mandar cualquier cosa que se haya pegado en el campo.
  if (!RegExp(r'^[A-Za-z0-9_-]{6,128}$').hasMatch(ultima)) return null;
  return ultima;
}
