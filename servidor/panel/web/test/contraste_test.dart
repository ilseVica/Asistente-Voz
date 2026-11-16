import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/tema.dart';

double _luminancia(Color c) {
  double canal(int v) {
    final double x = v / 255.0;
    if (x <= 0.03928) return x / 12.92;
    return math.pow((x + 0.055) / 1.055, 2.4).toDouble();
  }
  return 0.2126 * canal((c.r * 255).round()) +
      0.7152 * canal((c.g * 255).round()) +
      0.0722 * canal((c.b * 255).round());
}

double _contraste(Color a, Color b) {
  final double la = _luminancia(a);
  final double lb = _luminancia(b);
  final double hi = la > lb ? la : lb;
  final double lo = la > lb ? lb : la;
  return (hi + 0.05) / (lo + 0.05);
}

void main() {
  test('contraste de los pares de texto y fondo del tema', () {
    final List<(Color, Color, bool)> pares = <(Color, Color, bool)>[
      (Tema.texto, Tema.fondo, false),
      (Tema.texto, Tema.tarjeta, false),
      (Tema.texto, Tema.suave, false),
      (Tema.secundario, Tema.fondo, false),
      (Tema.secundario, Tema.tarjeta, false),
      (Tema.secundario, Tema.suave, false),
      (Tema.tarjeta, Tema.primario, false),
      (Tema.primario, Tema.amarillo, false),
      (Tema.verdeOscuro, Tema.verdeHoja, false),
      (Tema.ambarTexto, Tema.ambarFondo, false),
      (Tema.ambarPildora, Tema.ambarFondo, false),
      (Tema.peligro, Tema.fondo, false),
      (Tema.peligro, Tema.tarjeta, false),
    ];
    for (final (Color fg, Color bg, bool grande) in pares) {
      final double c = _contraste(fg, bg);
      final double minimo = grande ? 3.0 : 4.5;
      expect(c, greaterThanOrEqualTo(minimo),
          reason: 'par tiene ${c.toStringAsFixed(2)}:1');
    }
  });
}
