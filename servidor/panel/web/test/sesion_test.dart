import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/sesion.dart';

void main() {
  group('codigoDeEnlace', () {
    test('acepta el enlace completo de Telegram', () {
      expect(
        codigoDeEnlace(
            'http://villaca.tail722206.ts.net:8080/acceso/abc123DEF'),
        'abc123DEF',
      );
    });

    test('acepta el enlace con otros parámetros detrás', () {
      // Los enlaces de Telegram pueden llegar con `?` o `#`. El código no lleva
      // ninguno de los dos, así que se corta por ahí antes de partir por `/`.
      expect(
        codigoDeEnlace('https://192.168.1.169:8080/acceso/abc123DEF?utm=x'),
        'abc123DEF',
      );
      expect(
        codigoDeEnlace('https://ejemplo/acceso/abc123DEF#seccion'),
        'abc123DEF',
      );
    });

    test('acepta el código suelto, por si alguien lo teclea', () {
      expect(codigoDeEnlace('abc123DEF'), 'abc123DEF');
      expect(codigoDeEnlace('  abc123DEF  '), 'abc123DEF');
    });

    test('no devuelve nada para lo que no es un enlace ni un código', () {
      expect(codigoDeEnlace(''), isNull);
      expect(codigoDeEnlace('     '), isNull);
      expect(codigoDeEnlace('hola'), isNull); // muy corto
      expect(codigoDeEnlace('http://'), isNull); // sin último segmento
      expect(codigoDeEnlace('<script>alert(1)</script>'), isNull);
    });

    test('no deja pasar de lado cualquier cosa pegada en el campo', () {
      // El campo acepta el enlace completo, pero eso no significa que acepte
      // cualquier cosa: sólo el último segmento si parece un código.
      expect(codigoDeEnlace('https://ejemplo/acceso/abc def'), isNull);
      // Un último segmento demasiado corto no es un código: si se aceptara
      // cualquier cosa que haya después de la última barra, un `/acceso/` sin
      // código mandaría una petición inútil al servidor.
      expect(codigoDeEnlace('https://ejemplo/acceso/abc'), isNull);
    });
  });
}
