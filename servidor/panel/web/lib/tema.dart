import 'package:flutter/material.dart';

/// Los valores de la identidad visual de Lucía, en un solo sitio.
class Tema {
  Tema._();

  // --- Colores ---------------------------------------------------------------

  static const Color primario = Color(0xFF5A3563);
  static const Color primarioOscuro = Color(0xFF46284F);
  static const Color ciruela = primario;

  static const Color fondo = Color(0xFFF6F2F9);
  static const Color lavandaClara = fondo;

  static const Color texto = Color(0xFF5A3563);
  static const Color secundario = Color(0xFF6E5276);
  static const Color textoSecundario = secundario;

  static const Color borde = Color(0xFFD8CFE5);
  static const Color tarjeta = Color(0xFFFFFFFF);
  static const Color blanco = tarjeta;

  static const Color suave = Color(0xFFE3DCEC);
  static const Color lavanda = suave;
  static const Color cuadroLogo = suave;

  static const Color peligro = Color(0xFF9F1D1D);

  static const Color ambarFondo = Color(0xFFFBEFD9);
  static const Color ambarTexto = Color(0xFF5C3204);
  static const Color ambarBorde = Color(0xFFE8C98F);
  static const Color ambarPildora = Color(0xFF7A4205);

  static const Color caja = Color(0xFFECE6F5);

  static const Color gris = Color(0xFFB7BCB8);

  static const Color amarillo = Color(0xFFFFDB8C);
  static const Color rosa = Color(0xFFF89BA6);

  static const Color verdeHoja = Color(0xFFB9CB98);
  static const Color verdeOscuro = Color(0xFF2E4A1F);

  static const List<Color> escalaGas = <Color>[
    Color(0xFF5A3563),
    Color(0xFF8A6D1D),
    Color(0xFF9F1D1D),
  ];

  // --- Tipografía -------------------------------------------------------------

  static const String familia = 'AtkinsonHyperlegible';
  static const String familiaLogotipo = 'Fredoka';

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

  static const double altoMinimo = 52;
  static const double altoNavegacion = 56;

  static const double anchoEscritorio = 900;
  static const double anchoTableta = 600;

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
