import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/tema.dart';
import 'package:asistente_panel/modelos.dart';

void main() {
  for (final (int pct, String esperado) in <(int, String)>[(91, 'Nivel alto'), (80, 'Normal')]) {
    testWidgets('tarjeta de agua a $pct % muestra $esperado', (tester) async {
      tester.view.physicalSize = const Size(1200, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final int ahora = DateTime.now().millisecondsSinceEpoch ~/ 1000;
      final EstadoPanel estado = EstadoPanel();
      estado.api.ponerToken('token-de-prueba');
      estado.preparando = false;
      estado.avisoPendiente = false;
      estado.errorArranque = null;
      estado.conectadoAlServidor = true;
      estado.anotarMensajeVivo(ahora);
      estado.destino = Destino.inicio;
      estado.estado = Estado(
        servidorHora: ahora,
        luz: Luz(estado: conectado, encendida: false, origen: null, recibidoEn: ahora),
        sensores: Sensores(
            estado: conectado,
            temperaturaC: 22.5,
            humedadPct: 45,
            movimiento: false,
            gasAdc: 312,
            aguaPct: pct,
            aguaCm: null,
            aguaEstado: 'normal',
            recibidoEn: ahora),
        nodo: Componente(estado: conectado, recibidoEn: ahora),
        satelite: Componente(estado: conectado, recibidoEn: ahora),
        sitio: Componente(estado: conectado, recibidoEn: ahora),
        inactividad: Inactividad(ultimoMovimiento: ahora - 120, tiempoMin: 120, minimo: 2, maximo: 720),
        modo: 'normal',
        alertas: const <String, int>{},
        familiares: 2,
        conexiones: 1,
        gasOnAdc: 1800,
      );

      await tester.pumpWidget(MaterialApp(
        theme: Tema.construir(),
        home: AsistentePanel(estado: estado, arrancar: false),
      ));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(tester.takeException(), isNull);
      expect(find.text(esperado), findsWidgets);
    });
  }
}
