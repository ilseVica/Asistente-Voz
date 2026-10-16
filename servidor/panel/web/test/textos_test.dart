import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/textos.dart';

void main() {
  group('origenDeLuz', () {
    test('los cuatro orígenes del contrato, en palabras', () {
      expect(origenDeLuz('voz'), 'Por voz');
      expect(origenDeLuz('panel'), 'Desde el panel');
      expect(origenDeLuz('boton'), 'Con el botón');
      expect(origenDeLuz('alexa'), 'Con Alexa');
    });

    test('lo que no es del contrato no se enseña en crudo', () {
      expect(origenDeLuz(null), 'Origen desconocido');
      expect(origenDeLuz(''), 'Origen desconocido');
      expect(origenDeLuz('nodo'), 'Origen desconocido');
      expect(origenDeLuz('rele'), 'Origen desconocido');
      expect(origenDeLuz('otro-hilo'), 'Origen desconocido');
    });
  });

  group('detalleOrigen', () {
    test('los cuatro orígenes, en palabras del bot', () {
      expect(detalleOrigen('boton'), 'Se pidió por medio del botón de ayuda');
      expect(detalleOrigen('voz'), 'Se pidió por medio del asistente de voz');
      expect(detalleOrigen('alexa'), 'Se pidió por medio de Alexa');
      expect(detalleOrigen('panel'), 'Se pidió desde el panel');
    });

    test('lo que no se sabe decir no pinta línea', () {
      expect(detalleOrigen(null), isNull);
      expect(detalleOrigen(''), isNull);
      expect(detalleOrigen('nodo'), isNull);
      expect(detalleOrigen('telepatía'), isNull);
    });
  });

  group('familiares', () {
    test('singular con uno, plural con más', () {
      expect(familiares(0), '0 familiares registrados');
      expect(familiares(1), '1 familiar registrado');
      expect(familiares(2), '2 familiares registrados');
    });
  });

  group('repeticionDe', () {
    test('los tres nombres del formulario', () {
      expect(repeticionDe('una_vez'), 'Una sola vez');
      expect(repeticionDe('diario'), 'Todos los días');
      expect(repeticionDe('semanal'), 'Ciertos días');
    });

    test('lo que no es del contrato se enseña tal cual, no en blanco', () {
      expect(repeticionDe('raro'), 'raro');
    });
  });

  group('diasDe', () {
    test('ordena y no repite', () {
      expect(diasDe(<int>[5, 1, 5]), 'lunes y viernes');
    });

    test('tres días con comas y «y»', () {
      expect(diasDe(<int>[3, 1, 5]), 'lunes, miércoles y viernes');
    });

    test('un solo día', () {
      expect(diasDe(<int>[7]), 'domingo');
    });

    test('vacío o fuera de rango no pinta índices inválidos', () {
      expect(diasDe(<int>[]), '');
      expect(diasDe(<int>[0, 9]), '');
    });
  });

  group('ultimaVezSeDijo', () {
    test('envuelve el momento ya formateado', () {
      expect(ultimaVezSeDijo('hoy a las 08:00'),
          'La última vez se dijo hoy a las 08:00.');
    });
  });

  group('minutos y horas', () {
    test('singular con uno, plural con más', () {
      expect(minutos(1), '1 minuto');
      expect(minutos(5), '5 minutos');
      expect(horas(1), '1 hora');
      expect(horas(3), '3 horas');
    });
  });
}
