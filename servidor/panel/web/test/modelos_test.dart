import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/modelos.dart';

void main() {
  group('Estado.desde', () {
    // La forma REAL que devuelve `EstadoSala.instantanea()`. Si cambia allí, esta
    // prueba tiene que fallar aquí, que es el sitio donde se ve que se rompe algo
    // que la interfaz no sabe leer.
    Map<String, dynamic> instantanea() => <String, dynamic>{
          'servidor_hora': 1700000000,
          'luz': <String, dynamic>{
            'encendida': true,
            'origen': 'voz',
            'recibido_en': 1699999940,
            'estado': 'conectado',
          },
          'sensores': <String, dynamic>{
            'temperatura_c': 24.5,
            'humedad_pct': 60,
            'movimiento': false,
            'gas_adc': 312,
            'recibido_en': 1699999940,
            'estado': 'conectado',
          },
          'nodo': <String, dynamic>{
            'estado': 'conectado',
            'recibido_en': 1699999940,
            'ip': '192.168.1.50',
            'fw': '1.2.3',
          },
          'satelite': <String, dynamic>{
            'estado': 'no_instalado',
            'recibido_en': null,
          },
          'sitio': <String, dynamic>{
            'estado': 'conectado',
            'recibido_en': 1699999940,
            'fuente': 'nodo',
          },
          'modo': 'instalacion',
          'alertas': <String, int>{'pendiente': 1, 'enviada': 0,
                                    'resuelta': 3, 'descartada': 0},
          'familiares': 2,
          'conexiones': 1,
        };

    test('lee todos los bloques', () {
      final Estado e = Estado.desde(instantanea());
      expect(e.luz.encendida, isTrue);
      expect(e.luz.origen, 'voz');
      expect(e.sensores.temperaturaC, 24.5);
      expect(e.sensores.humedadPct, 60);
      expect(e.sensores.movimiento, isFalse);
      expect(e.nodo.estado, conectado);
      expect(e.satelite.estado, noInstalado);
      expect(e.sitio.fuente, 'nodo');
      expect(e.modo, 'instalacion');
      expect(e.familiares, 2);
      expect(e.conexiones, 1);
    });

    test('suma las alertas sin resolver de los cuatro estados', () {
      // El servidor manda los cuatro conteos; el total es una cuenta y no una
      // regla, así que lo hace la interfaz. Si lo hiciera con `alertas.length`
      // saldrían 4 en vez de 1.
      expect(Estado.desde(instantanea()).alertasSinResolver, 1);
    });

    test('sin datos, ningún número es cero', () {
      // Es la distinción que sostiene el panel: «no lo sé» y «cero» son cosas
      // distintas, y pintar 0.0 °C por no saber es una mentira con un número.
      final Estado e = Estado.desde(<String, dynamic>{
        'servidor_hora': 0,
        'luz': <String, dynamic>{'encendida': null, 'origen': null,
                                 'recibido_en': null, 'estado': 'sin_datos'},
        'sensores': <String, dynamic>{
          'temperatura_c': null, 'humedad_pct': null, 'movimiento': null,
          'gas_adc': null, 'recibido_en': null, 'estado': 'sin_datos',
        },
        'nodo': <String, dynamic>{'estado': 'sin_datos'},
        'satelite': <String, dynamic>{'estado': 'no_instalado'},
        'sitio': <String, dynamic>{'estado': 'sin_datos', 'fuente': 'nodo'},
        'modo': 'instalacion',
        'alertas': <String, int>{},
        'familiares': 0,
        'conexiones': 0,
      });
      expect(e.luz.encendida, isNull);
      expect(e.sensores.temperaturaC, isNull);
      expect(e.sensores.humedadPct, isNull);
      expect(e.sensores.gasAdc, isNull);
      expect(e.sensores.estado, sinDatos);
      expect(e.alertasSinResolver, 0);
    });

    test('aguanta un JSON sin los bloques que todavía no existen', () {
      // Un día habrá más bloques en la API y uno que otro cliente viejo. Esto no
      // es robustez por robustez: es que el panel no se caiga porque falte un
      // campo que no se ha inventado todavía.
      final Estado e = Estado.desde(<String, dynamic>{'servidor_hora': 5});
      expect(e.luz.estado, sinDatos);
      expect(e.satelite.estado, sinDatos);
      expect(e.familiares, 0);
    });
  });

  group('Alerta', () {
    test('sólo «pendiente» y «enviada» cuentan como sin resolver', () {
      Alerta a(String estado) => Alerta(
            id: 'a1', tipo: 'gas', estado: estado, creadoEn: 0,
            carga: const <String, dynamic>{}, resueltaEn: null);
      expect(a('pendiente').sinResolver, isTrue);
      expect(a('enviada').sinResolver, isTrue);
      expect(a('resuelta').sinResolver, isFalse);
      expect(a('descartada').sinResolver, isFalse);
    });
  });

  group('Recordatorio', () {
    Map<String, dynamic> json() => <String, dynamic>{
          'id': 'rec-abc',
          'texto': 'Tomar la medicina',
          'hora': '08:00',
          'repeticion': 'semanal',
          'fecha': null,
          'dias': <int>[1, 3],
          'proximo_en': 1700003600,
          'activo': true,
          'creado_en': 1700000000,
          'creado_por': 'Marta',
          'cancelado_en': null,
          'hecho_en': null,
          'ultima_entrega': <String, dynamic>{
            'estado': 'entregado',
            'cuando': 1699999000,
          },
        };

    test('lee los campos y la última entrega', () {
      final Recordatorio r = Recordatorio.desde(json());
      expect(r.id, 'rec-abc');
      expect(r.hora, '08:00');
      expect(r.repeticion, 'semanal');
      expect(r.dias, <int>[1, 3]);
      expect(r.activo, isTrue);
      expect(r.ultimaEntrega!.seDijo, isTrue);
      expect(r.ultimaEntrega!.cuando, 1699999000);
    });

    test('sin última entrega es null, y no un objeto vacío', () {
      final Map<String, dynamic> sin = json()..['ultima_entrega'] = null;
      expect(Recordatorio.desde(sin).ultimaEntrega, isNull);
    });

    test('una entrega no entregada no se dijo', () {
      final Map<String, dynamic> sin = json()
        ..['ultima_entrega'] = <String, dynamic>{
          'estado': 'no_entregado',
          'cuando': 1699999000,
        };
      expect(Recordatorio.desde(sin).ultimaEntrega!.seDijo, isFalse);
    });

    test('aguanta un JSON sin los campos opcionales', () {
      final Recordatorio r = Recordatorio.desde(<String, dynamic>{
        'id': 'rec-x',
        'texto': 'Algo',
        'hora': '07:30',
        'repeticion': 'una_vez',
      });
      expect(r.dias, isEmpty);
      expect(r.activo, isFalse);
      expect(r.ultimaEntrega, isNull);
    });
  });

  group('hace', () {
    const int ahora = 1700000000;
    test('sin datos no es «hace 0 min»', () {
      expect(hace(null, ahora: ahora), 'sin datos');
    });

    test('las unidades que se ven en pantalla', () {
      expect(hace(ahora - 10, ahora: ahora), 'hace un momento');
      expect(hace(ahora - 4 * 60, ahora: ahora), 'hace 4 minutos');
      expect(hace(ahora - 3 * 3600, ahora: ahora), 'hace 3 horas');
      expect(hace(ahora - 2 * 86400, ahora: ahora), 'hace 2 días');
    });

    test('singular con una unidad, plural con más', () {
      expect(hace(ahora - 60, ahora: ahora), 'hace 1 minuto');
      expect(hace(ahora - 2 * 60, ahora: ahora), 'hace 2 minutos');
      expect(hace(ahora - 3600, ahora: ahora), 'hace 1 hora');
      expect(hace(ahora - 2 * 3600, ahora: ahora), 'hace 2 horas');
    });

    test('un tiempo que aún no llega es «ahora mismo» y no un número raro', () {
      // Un dispositivo con la hora mal adelantada no puede decir «hace -5 min».
      expect(hace(ahora + 300, ahora: ahora), 'ahora mismo');
    });
  });

  group('fechaAlerta', () {
    test('distingue hoy de ayer', () {
      // Fecha fija: un test que usa `DateTime.now()` y resta una hora se cae en
      // la frontera de medianoche (a las 00:14, «hace 1 hora» es ayer).
      final DateTime ref = DateTime(2026, 10, 6, 12, 0);
      int segundaHora(int anio, int mes, int dia, int hora) =>
          DateTime(anio, mes, dia, hora).millisecondsSinceEpoch ~/ 1000;
      expect(fechaAlerta(segundaHora(2026, 10, 6, 9), hoy: ref).startsWith('hoy, '),
          isTrue);
      expect(fechaAlerta(segundaHora(2026, 10, 5, 23), hoy: ref)
              .startsWith('ayer, '),
          isTrue);
    });
  });

  group('fechaProxima', () {
    final DateTime ref = DateTime(2026, 10, 6, 12, 0); // martes 6
    int aSegundos(int dia, int hora) =>
        DateTime(2026, 10, dia, hora).millisecondsSinceEpoch ~/ 1000;

    test('distingue hoy, mañana y el nombre del día', () {
      expect(fechaProxima(aSegundos(6, 9), hoy: ref).startsWith('hoy, '), isTrue);
      expect(fechaProxima(aSegundos(7, 9), hoy: ref).startsWith('mañana, '),
          isTrue);
      expect(fechaProxima(aSegundos(8, 9), hoy: ref), 'el jueves, 09:00');
    });

    test('más allá de una semana pone la fecha', () {
      expect(fechaProxima(aSegundos(20, 9), hoy: ref), '20/10/2026 · 09:00');
    });
  });

  group('cuandoSeDijo', () {
    final DateTime ref = DateTime(2026, 10, 6, 12, 0);
    int aSegundos(int dia, int hora) =>
        DateTime(2026, 10, dia, hora).millisecondsSinceEpoch ~/ 1000;

    test('hoy y ayer con la hora', () {
      expect(cuandoSeDijo(aSegundos(6, 9), hoy: ref), 'hoy a las 09:00');
      expect(cuandoSeDijo(aSegundos(5, 9), hoy: ref), 'ayer a las 09:00');
    });

    test('más antiguo pone la fecha entera', () {
      expect(cuandoSeDijo(aSegundos(1, 9), hoy: ref),
          'el 1/10/2026 a las 09:00');
    });
  });
}
