import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/tema.dart';
import 'package:asistente_panel/uis/navegacion.dart';

void main() {
  testWidgets('cabecera muestra marca Lucía a 1200 px', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
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

    final SemanticsHandle handle = tester.ensureSemantics();
    expect(find.bySemanticsLabel('Lucía'), findsWidgets);
    handle.dispose();

    final Rect indicador = tester.getRect(find.byType(PastillaConexion));
    expect(indicador.right, greaterThan(1100));
  });

  testWidgets('cabecera muestra marca Lucía a 390 px', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
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

    final SemanticsHandle handle = tester.ensureSemantics();
    expect(find.bySemanticsLabel('Lucía'), findsWidgets);
    handle.dispose();

    final Rect marca = tester.getRect(find.byType(MarcaLucia));
    final Rect indicador = tester.getRect(find.byType(PastillaConexion));
    expect(marca.overlaps(indicador), isFalse);
  });
}
