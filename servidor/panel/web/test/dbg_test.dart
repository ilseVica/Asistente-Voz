import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:asistente_panel/tema.dart';
import 'package:asistente_panel/uis/navegacion.dart';

void main() {
  for (final double t in [18.0, 20.0]) {
    testWidgets('dbg$t', (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
        theme: Tema.construir(),
        home: Scaffold(body: Center(child: MarcaLucia(tamano: t))),
      ));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      print('T=$t SIZE=${tester.getSize(find.byType(MarcaLucia))}');
    });
  }
}
