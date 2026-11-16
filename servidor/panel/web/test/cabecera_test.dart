import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/tema.dart';

void main() {
  for (final Size tamano in <Size>[const Size(1200, 900), const Size(390, 844)]) {
    testWidgets('cabecera muestra logo y Lucía a ${tamano.width} px', (tester) async {
      tester.view.physicalSize = tamano;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final estado = EstadoPanel();
      estado.api.ponerToken('token-de-prueba');
      estado.preparando = false;
      estado.avisoPendiente = false;
      estado.errorArranque = null;
      estado.destino = Destino.inicio;

      await tester.pumpWidget(MaterialApp(
        theme: Tema.construir(),
        home: AsistentePanel(estado: estado, arrancar: false),
      ));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(tester.takeException(), isNull);
      expect(find.text('Lucía'), findsWidgets);
      expect(find.byType(Image), findsWidgets);
    });
  }
}
