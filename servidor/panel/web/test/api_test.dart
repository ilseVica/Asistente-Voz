import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/api.dart';

void main() {
  group('esperaReintentos (apartado 4.3 de la propuesta)', () {
    test('secuencia 1, 2, 4, 8, 15, 30 y después siempre 30', () {
      expect(esperaReintentos(1), 1);
      expect(esperaReintentos(2), 2);
      expect(esperaReintentos(3), 4);
      expect(esperaReintentos(4), 8);
      expect(esperaReintentos(5), 15);
      expect(esperaReintentos(6), 30);
      expect(esperaReintentos(7), 30);
      expect(esperaReintentos(20), 30);
    });
  });
}