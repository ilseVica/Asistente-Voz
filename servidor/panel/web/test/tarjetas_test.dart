import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/textos.dart';
import 'package:asistente_panel/uis/tarjetas.dart';

/// La regla que decide entre «Sin datos» y «Sin sensor» en las tarjetas de
/// ambiente. Es función pura y se prueba aquí, sin montar la interfaz.
///
/// Viene del cambio de 2026-10-19: con los sensores reales, el nodo puede estar
/// conectado y mandar un campo en `null` (el DHT22 falló tres veces, el MQ-2
/// todavía calienta). Eso NO es lo mismo que no saber nada del nodo, y la tarjeta
/// tiene que decir cuál de las dos cosas pasa.
void main() {
  group('datoDeSensor', () {
    test('con valor, se enseña el valor y no importa si el nodo está conectado',
        () {
      expect(
          datoDeSensor(
              nodoConectado: true, valor: 21.5, conValor: () => '21,5 °C'),
          '21,5 °C');
      expect(
          datoDeSensor(
              nodoConectado: false, valor: 21.5, conValor: () => '21,5 °C'),
          '21,5 °C');
    });

    test('sin valor y con el nodo conectado, es «Sin sensor»', () {
      expect(
          datoDeSensor(
              nodoConectado: true, valor: null, conValor: () => 'nunca'),
          Textos.sinSensor);
    });

    test('sin valor y sin nodo, es «Sin datos»', () {
      expect(
          datoDeSensor(
              nodoConectado: false, valor: null, conValor: () => 'nunca'),
          Textos.sinDatos);
    });

    test('un cero SÍ es un valor: no se confunde con «sin dato»', () {
      expect(
          datoDeSensor(nodoConectado: true, valor: 0, conValor: () => '0 %'),
          '0 %');
      expect(
          datoDeSensor(nodoConectado: false, valor: 0, conValor: () => '0 %'),
          '0 %');
    });
  });
}
