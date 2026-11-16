import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/tema.dart';
import 'package:asistente_panel/presentacion_alerta.dart';
import 'package:asistente_panel/textos.dart';

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
      (Tema.verdeOscuro, Tema.verdePastillaFondo, false),
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

  test('contraste del banner de alerta y la pastilla de cabecera', () {
    final List<String> tipos = <String>['agua', 'gas', 'ayuda', 'bienestar'];
    for (final String tipo in tipos) {
      // agua 'lleno' es el caso ámbar; 'bajo' es rojo.
      final Map<String, dynamic> carga = tipo == 'agua'
          ? <String, dynamic>{'tipo': 'lleno', 'agua_pct': 90}
          : <String, dynamic>{};
      final PresentacionAlerta pres = presentacionAlerta(
        tipo,
        carga: carga,
      );
      final double c = _contraste(pres.colorTexto, pres.colorFondo);
      expect(c, greaterThanOrEqualTo(4.5),
          reason: '$tipo: ${c.toStringAsFixed(2)}:1');
    }
    // nodo desconectado (ámbar)
    final double cNodo = _contraste(Tema.ambarPildora, Tema.ambarFondo);
    expect(cNodo, greaterThanOrEqualTo(4.5));
    // gas rojo
    final PresentacionAlerta gas = presentacionAlerta('gas');
    expect(_contraste(gas.colorTexto, gas.colorFondo), greaterThanOrEqualTo(4.5));
  });

  test('bienestar se titula Aviso de bienestar y no muestra su nombre interno', () {
    expect(tituloAlerta('bienestar'), 'Aviso de bienestar');
    final PresentacionAlerta mal = presentacionAlerta(
      'bienestar',
      carga: <String, dynamic>{'texto': 'La sala dice que se siente mal.'},
    );
    expect(mal.titulo, 'Aviso de bienestar');
    expect(mal.cuerpo, 'Dijo que se siente mal');
    final PresentacionAlerta silencio = presentacionAlerta(
      'bienestar',
      carga: <String, dynamic>{'texto': 'Lucía saludó a las 09:00 y a las 09:10.'},
    );
    expect(silencio.cuerpo, 'No contestó el saludo');
    for (final String t in <String>['gas', 'ayuda', 'inactividad', 'agua', 'bienestar', 'miedo']) {
      expect(tituloAlerta(t), isNot(equals(t)),
          reason: '$t muestra su nombre interno');
    }
  });
}
