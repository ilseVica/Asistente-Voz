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

    test('el arranque en frío del nodo tiene su propia frase', () {
      expect(origenDeLuz('arranque'), 'Tras un reinicio');
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

  group('minutosFueraDeRango', () {
    test('dice la franja que le dan, sin números escritos aquí', () {
      expect(minutosFueraDeRango(2, 720),
          'Escriba un número entre 2 y 720 minutos.');
      expect(minutosFueraDeRango(5, 60),
          'Escriba un número entre 5 y 60 minutos.');
    });
  });

  group('parrafoDeGas', () {
    test('con umbral enseña la lectura y contra qué saltó', () {
      expect(parrafoDeGas(1900, 1800), 'Lectura: 1900 · Umbral: 1800');
    });

    test('sin umbral sólo la lectura: no se inventa un número', () {
      expect(parrafoDeGas(1900, null), 'Lectura: 1900');
    });
  });

  group('quePasoDe', () {
    test('gas con su lectura real', () {
      expect(
          quePasoDe('gas', <String, dynamic>{'gas_adc': 1900}, umbralGas: 1800),
          'Lectura: 1900 · Umbral: 1800');
    });

    test('inactividad con sus minutos bien contados', () {
      expect(quePasoDe('inactividad', <String, dynamic>{'minutos': 1}),
          'Sin movimiento durante 1 minuto');
      expect(quePasoDe('inactividad', <String, dynamic>{'minutos': 120}),
          'Sin movimiento durante 120 minutos');
    });

    test('ayuda con su origen, como lo cuenta el bot', () {
      expect(quePasoDe('ayuda', <String, dynamic>{'origen': 'boton'}),
          'Se pidió por medio del botón de ayuda');
    });

    test('sin nada que contar devuelve null y no una línea vacía', () {
      expect(quePasoDe('gas', <String, dynamic>{}), isNull);
      expect(quePasoDe('miedo', <String, dynamic>{}), isNull);
    });
  });

  group('atendidaCuando', () {
    test('con nombre: «Atendida … por Ana»', () {
      expect(atendidaCuando('hoy, 09:12', 'Ana'),
          'Atendida hoy, 09:12 por Ana');
    });

    test('sin nombre sólo la hora: no se le pone nombre a nadie', () {
      expect(atendidaCuando('hoy, 09:12', null), 'Atendida hoy, 09:12');
      expect(atendidaCuando('hoy, 09:12', '   '), 'Atendida hoy, 09:12');
    });
  });

  group('eventoAlerta', () {
    test('los cinco hechos que el servidor manda, con su nombre cuando lo hay',
        () {
      expect(eventoAlerta('creada', null), Textos.seCreoLaAlerta);
      expect(eventoAlerta('enviada', 'Ana'),
          'Se envió el aviso por Telegram a Ana.');
      expect(eventoAlerta('enviada', null),
          'Se envió el aviso por Telegram.');
      expect(eventoAlerta('atendida', 'Luis'), 'Luis la atendió.');
      expect(eventoAlerta('descartada', null), Textos.seDescartoLaAlerta);
    });

    test('un tipo desconocido se enseña tal cual, sin inventarle hechos', () {
      expect(eventoAlerta('algo_nuevo', null), 'algo_nuevo');
    });
  });

  group('tituloAlerta', () {
    test('traduce los cuatro tipos del servidor', () {
      expect(tituloAlerta('gas'), Textos.alertaDeGas);
      expect(tituloAlerta('ayuda'), Textos.peticionDeAyuda);
      expect(tituloAlerta('inactividad'), Textos.sinMovimientoEnLaCasa);
      expect(tituloAlerta('miedo'), Textos.haDichoTenerMiedo);
    });
  });

  group('avisoInactividadAlos', () {
    test('con minutos en formato corto', () {
      expect(avisoInactividadAlos(1), 'Aviso de inactividad a los 1 min');
      expect(avisoInactividadAlos(120), 'Aviso de inactividad a los 120 min');
    });
  });
}
