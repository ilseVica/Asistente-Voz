import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/modelos.dart';
import 'package:asistente_panel/tema.dart';

void main() {
  testWidgets('app completa: Nuevo abre AlertDialog, no BottomSheet', (tester) async {
    tester.view.physicalSize = const Size(1849, 939);
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
    estado.destino = Destino.inicio;

    await tester.pumpWidget(
      MaterialApp(
        theme: Tema.construir(),
        home: AsistentePanel(estado: estado, arrancar: false),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('Nuevo'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);

    expect(find.byType(AlertDialog), findsOneWidget);
    expect(find.byType(BottomSheet), findsNothing);
  });
}
