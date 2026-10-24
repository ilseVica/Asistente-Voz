import 'package:flutter/material.dart';

/// Los valores del prototipo HTML, en un solo sitio.
///
/// Nada de esto se inventa aquí: los colores son los de
/// `fase4_md/files/panel-celular.html` y `panel-escritorio.html`, y los radios y
/// espacios salen de ahí midiendo. Si un color se cambia en un sitio y no en otro,
/// el panel y el prototipo dejan de coincidir y nadie se entera de cuál es el bueno.
class Tema {
  Tema._();

  // --- Colores ---------------------------------------------------------------

  static const Color primario = Color(0xFF0E5E5A);
  static const Color primarioOscuro = Color(0xFF0A4441);
  static const Color fondo = Color(0xFFF4F5F2);
  static const Color texto = Color(0xFF1B1F1E);
  static const Color secundario = Color(0xFF4A524F);
  static const Color borde = Color(0xFFDADFDB);
  static const Color tarjeta = Color(0xFFFFFFFF);
  static const Color suave = Color(0xFFE3F0EE);
  static const Color peligro = Color(0xFF9F1D1D);

  /// El ámbar del aviso de conexión del prototipo
  /// (`panel-escritorio-sin-conexion.html`): fondo, texto, borde y píldora. Es
  /// la paleta de «sin conexión» que Ilse aprobó; el rojo queda SOLO para la
  /// alerta activa, que es la única cosa que no es un aviso sino una emergencia.
  static const Color ambarFondo = Color(0xFFFBEFD9);
  static const Color ambarTexto = Color(0xFF5C3204);
  static const Color ambarBorde = Color(0xFFE8C98F);
  static const Color ambarPildora = Color(0xFF7A4205);

  /// El fondo de las cajas de los datos (`#E7EAE8`), el mismo que la barra de
  /// gas y los separadores de las filas del prototipo.
  static const Color caja = Color(0xFFE7EAE8);

  /// La escala de aviso del gas. `0` es la normalidad y `2` el pico, y cada
  /// escalón tiene su color y su texto: es el único dato de la casa que cambia de
  /// aspecto según el valor, porque no es «cuánto», es «cuánta prisa hay».
  static const List<Color> escalaGas = <Color>[
    Color(0xFF0E5E5A),
    Color(0xFF8A6D1D),
    Color(0xFF9F1D1D),
  ];

  // --- Tipografía -------------------------------------------------------------

  /// La familia empaquetada en los assets. No se pide a Google Fonts: una web que
  /// pide la fuente a Internet no funciona en casa sin conexión (P33).
  static const String familia = 'AtkinsonHyperlegible';

  static const TextTheme textoTema = TextTheme(
    headlineMedium: TextStyle(
        fontSize: 26, fontWeight: FontWeight.w700, color: texto, height: 1.15),
    titleLarge: TextStyle(
        fontSize: 18, fontWeight: FontWeight.w700, color: texto, height: 1.2),
    titleMedium: TextStyle(
        fontSize: 17, fontWeight: FontWeight.w700, color: texto, height: 1.25),
    bodyLarge: TextStyle(fontSize: 16, color: texto, height: 1.35),
    bodyMedium: TextStyle(fontSize: 15, color: texto, height: 1.35),
    bodySmall: TextStyle(fontSize: 14, color: secundario, height: 1.3),
    labelMedium: TextStyle(
        fontSize: 13, fontWeight: FontWeight.w700, color: secundario,
        height: 1.2),
  );

  // --- Forma -----------------------------------------------------------------

  static const double radioGrande = 18;
  static const double radio = 16;
  static const double radioBoton = 14;

  /// El mínimo de cualquier cosa pulsable. Sale del HTML: `min-height: 52px` en el
  /// interruptor de la luz y `min-height: 56px` en la barra de abajo.
  static const double altoMinimo = 52;
  static const double altoNavegacion = 56;

  /// A partir de 900 px el panel pasa a la cabecera con pestañas de escritorio;
  /// por debajo, una columna con barra inferior. Es el corte del diseño (A.1):
  /// por encima las cuatro tarjetas caben en fila, por debajo no.
  static const double anchoEscritorio = 900;

  /// La escala completa del ADC de 12 bits del ESP32. La barra de gas mide
  /// `gas_adc` contra esta escala y marca el umbral en su sitio: no es un número
  /// que se configura, es el rango del sensor.
  static const double escalaAdc = 4095;

  static ThemeData construir() {
    final TextTheme base = textoTema.apply(
        fontFamily: familia, bodyColor: texto, displayColor: texto);
    return ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: fondo,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primario,
        primary: primario,
        surface: tarjeta,
        error: peligro,
      ),
      fontFamily: familia,
      textTheme: base,
      dividerTheme: const DividerThemeData(color: borde, space: 1, thickness: 1),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: primario,
          foregroundColor: tarjeta,
          minimumSize: const Size(0, altoMinimo),
          textStyle: const TextStyle(
              fontSize: 18, fontWeight: FontWeight.w700, fontFamily: familia),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(radioBoton)),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: primario,
          minimumSize: const Size(0, altoMinimo),
          side: const BorderSide(color: primario, width: 1.5),
          textStyle: const TextStyle(
              fontSize: 17, fontWeight: FontWeight.w700, fontFamily: familia),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(radioBoton)),
        ),
      ),
      cardTheme: CardThemeData(
        color: tarjeta,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radio),
          side: const BorderSide(color: borde),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: tarjeta,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radioBoton),
          borderSide: const BorderSide(color: borde),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radioBoton),
          borderSide: const BorderSide(color: borde),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(radioBoton),
          borderSide: const BorderSide(color: primario, width: 2),
        ),
      ),
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: texto,
        contentTextStyle: TextStyle(color: tarjeta, fontSize: 15),
      ),
    );
  }
}
