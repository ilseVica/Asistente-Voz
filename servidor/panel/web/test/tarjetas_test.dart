import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/tema.dart';
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

  group('estadoDelGas', () {
    test('por debajo de tres cuartas partes del umbral es «Normal»', () {
      expect(
          estadoDelGas(gasAdc: 1300, umbral: 1800), Textos.gasNormal);
      expect(estadoDelGas(gasAdc: 1349, umbral: 1800), Textos.gasNormal);
    });

    test('de 0,75×umbral hasta el umbral es «Gas elevado»', () {
      expect(
          estadoDelGas(gasAdc: 1350, umbral: 1800), Textos.gasElevado);
      expect(
          estadoDelGas(gasAdc: 1799, umbral: 1800), Textos.gasElevado);
    });

    test('en o por encima del umbral es «Gas detectado»', () {
      expect(
          estadoDelGas(gasAdc: 1800, umbral: 1800), Textos.gasDetectado);
      expect(
          estadoDelGas(gasAdc: 2400, umbral: 1800), Textos.gasDetectado);
    });

    test('sin umbral no se juzga nada: no se dice «Normal» a ciegas', () {
      expect(
          estadoDelGas(gasAdc: 4000, umbral: null), Textos.gasSinUmbral);
    });

    test('sin lectura es «Sin datos»', () {
      expect(estadoDelGas(gasAdc: null, umbral: 1800), Textos.sinDatos);
    });
  });

  group('colorDelGas', () {
    test('cada escalón con su color: normal, ámbar, rojo', () {
      expect(colorDelGas(gasAdc: 100, umbral: 1800), Tema.primario);
      expect(colorDelGas(gasAdc: 1700, umbral: 1800), Tema.ambarPildora);
      expect(colorDelGas(gasAdc: 2000, umbral: 1800), Tema.peligro);
    });

    test('lo que no se puede juzgar va en gris de texto, no en verde', () {
      expect(colorDelGas(gasAdc: 2000, umbral: null), Tema.secundario);
      expect(colorDelGas(gasAdc: null, umbral: 1800), Tema.secundario);
    });
  });
}
