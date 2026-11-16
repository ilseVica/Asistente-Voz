import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/modelos.dart';
import 'package:asistente_panel/tema.dart';

void main() {
  testWidgets('recordatorios abre un solo AlertDialog con fondo neutro', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final EstadoPanel estado = EstadoPanel();
    estado.api.ponerToken('token-de-prueba');
    estado.preparando = false;
    estado.avisoPendiente = false;
    estado.errorArranque = null;
    final int ahora = estado.ahoraLocal();
    estado.estado = Estado(
      servidorHora: ahora,
      luz: Luz(estado: conectado, encendida: true, origen: 'voz', recibidoEn: null),
      sensores: Sensores(estado: conectado, temperaturaC: 22.5, humedadPct: 45, movimiento: false, gasAdc: 312, recibidoEn: ahora - 10),
      nodo: Componente(estado: conectado, recibidoEn: null),
      satelite: Componente(estado: conectado, recibidoEn: null),
      sitio: Componente(estado: conectado, recibidoEn: null),
      inactividad: Inactividad(ultimoMovimiento: ahora - 120, tiempoMin: 120, minimo: 2, maximo: 720),
      modo: 'normal',
      alertas: const <String, int>{},
      familiares: 2,
      conexiones: 1,
      gasOnAdc: 1800,
    );
    estado.alertas = <Alerta>[];
    estado.conectadoAlServidor = true;
    estado.anotarMensajeVivo(ahora);
    estado.destino = Destino.recordatorios;

    await tester.pumpWidget(
      MaterialApp(
        theme: Tema.construir(),
        home: AsistentePanel(estado: estado, arrancar: false),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('Nuevo recordatorio'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);

    expect(find.byType(AlertDialog), findsOneWidget);
    final AlertDialog dialogo = tester.widget(find.byType(AlertDialog));
    expect(dialogo.backgroundColor, isNull);
    final ThemeData tema = Theme.of(tester.element(find.byType(AlertDialog)));
    expect(tema.dialogTheme.backgroundColor, Tema.tarjeta);
    expect(tema.dialogTheme.surfaceTintColor, Colors.transparent);
    final Size tamanoContenido = tester.getSize(
        find.descendant(of: find.byType(AlertDialog), matching: find.byType(ConstrainedBox)).first);
    expect(tamanoContenido.height, lessThan(600));
  });
}
