import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/sesion.dart';

void main() {
  group('normalizarCodigo', () {
    test('acepta un código válido de seis caracteres', () {
      expect(normalizarCodigo('A2B3C4'), 'A2B3C4');
      expect(normalizarCodigo('234567'), '234567');
    });

    test('lo pasa a mayúsculas y quita espacios y guiones al pegarlo', () {
      expect(normalizarCodigo('a2b3c4'), 'A2B3C4');
      expect(normalizarCodigo('  A2B3C4  '), 'A2B3C4');
      expect(normalizarCodigo('A2B 3C4'), 'A2B3C4');
      expect(normalizarCodigo('A2B-3C4'), 'A2B3C4');
    });

    test('rechaza lo que no tiene seis caracteres del alfabeto', () {
      expect(normalizarCodigo(''), isNull);
      expect(normalizarCodigo('     '), isNull);
      expect(normalizarCodigo('A2B3C'), isNull); // cinco
      expect(normalizarCodigo('A2B3C45'), isNull); // siete
    });

    test('rechaza los caracteres que el alfabeto no usa (0, 1, O, I, L)', () {
      expect(normalizarCodigo('A0B3C4'), isNull);
      expect(normalizarCodigo('A1B3C4'), isNull);
      expect(normalizarCodigo('AOB3C4'), isNull);
      expect(normalizarCodigo('AIB3C4'), isNull);
      expect(normalizarCodigo('ALB3C4'), isNull);
    });
  });
}
