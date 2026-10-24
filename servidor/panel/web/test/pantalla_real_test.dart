import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/modelos.dart';
import 'package:asistente_panel/tema.dart';
import 'package:asistente_panel/uis/tarjetas.dart';

void main() {
  testWidgets('asistentepanel real escritorio 1200x900', (tester) async {
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
      luz: const Luz(estado: conectado, encendida: true, origen: 'voz', recibidoEn: null),
      sensores: const Sensores(
          estado: conectado,
          temperaturaC: 22.5,
          humedadPct: 45,
          movimiento: true,
          gasAdc: 2100,
          recibidoEn: null),
      nodo: const Componente(estado: conectado, recibidoEn: null),
      satelite: const Componente(estado: conectado, recibidoEn: null),
      sitio: const Componente(estado: conectado, recibidoEn: null),
      inactividad: const Inactividad(ultimoMovimiento: null, tiempoMin: 120, minimo: 2, maximo: 720),
      modo: 'instalacion',
      alertas: const <String, int>{'pendiente': 1},
      familiares: 2,
      conexiones: 1,
      gasOnAdc: 1800,
    );
    estado.alertas = <Alerta>[
      Alerta(
        id: 'a1',
        tipo: 'gas',
        estado: 'pendiente',
        creadoEn: ahora - 20,
        carga: const <String, dynamic>{'gas_adc': 2100},
        resueltaEn: null,
      ),
    ];
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

    tester.takeException();
    expect(find.text('Asistente de Voz'), findsWidgets);
    expect(find.text('Sala principal'), findsWidgets);
    expect(find.text('Panel'), findsWidgets);
    expect(find.text('Historial de alertas'), findsWidgets);
    expect(find.text('Recordatorios'), findsWidgets);
    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
    expect(find.textContaining('Actualizado'), findsWidgets);
    expect(find.text('Alerta activa'), findsWidgets);
    expect(find.text('Pendiente'), findsWidgets);
    expect(find.text('Nuevo'), findsWidgets);
    expect(find.byType(TarjetaGas), findsWidgets);
    expect(find.byType(TarjetaAmbiente), findsWidgets);
    expect(find.byIcon(Icons.thermostat), findsWidgets);
  });

  testWidgets('asistentepanel real movil 390x844 sin nodo', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
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
      luz: const Luz(estado: sinDatos, encendida: null, origen: null, recibidoEn: null),
      sensores: const Sensores(
          estado: sinDatos,
          temperaturaC: null,
          humedadPct: null,
          movimiento: null,
          gasAdc: null,
          recibidoEn: null),
      nodo: Componente(estado: desconectado, recibidoEn: ahora - 600),
      satelite: const Componente(estado: desconectado, recibidoEn: null),
      sitio: Componente(estado: desconectado, recibidoEn: ahora - 600),
      inactividad: const Inactividad(ultimoMovimiento: null, tiempoMin: 120, minimo: 2, maximo: 720),
      modo: 'instalacion',
      alertas: const <String, int>{},
      familiares: 2,
      conexiones: 0,
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

    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
    expect(find.text('Sin datos'), findsWidgets);
    expect(find.textContaining('Sin conexión con la sala'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
