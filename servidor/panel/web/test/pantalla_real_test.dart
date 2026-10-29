import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/modelos.dart';
import 'package:asistente_panel/api.dart';
import 'package:asistente_panel/textos.dart';
import 'package:asistente_panel/tema.dart';

void main() {
  testWidgets('escritorio 1200 sin nodo', (tester) async {
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
      luz: Luz(estado: sinDatos, encendida: null, origen: null, recibidoEn: null),
      sensores: Sensores(
          estado: sinDatos,
          temperaturaC: null,
          humedadPct: null,
          movimiento: null,
          gasAdc: null,
          recibidoEn: null),
      nodo: Componente(estado: desconectado, recibidoEn: ahora - 600),
      satelite: Componente(estado: desconectado, recibidoEn: null),
      sitio: Componente(estado: desconectado, recibidoEn: ahora - 600),
      inactividad: Inactividad(ultimoMovimiento: null, tiempoMin: 120, minimo: 2, maximo: 720),
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
    expect(tester.takeException(), isNull);

    expect(find.text('Panel'), findsWidgets);
    expect(find.text('Historial de alertas'), findsWidgets);
    expect(find.text('Recordatorios'), findsWidgets);
    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.textContaining('Sin conexión'), findsWidgets);
    expect(find.text('Sin datos'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });

  testWidgets('escritorio 1200 con nodo', (tester) async {
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
      sensores: Sensores(
          estado: conectado,
          temperaturaC: 22.5,
          humedadPct: 45,
          movimiento: false,
          gasAdc: 312,
          recibidoEn: ahora - 10),
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

    expect(find.text('Panel'), findsWidgets);
    expect(find.text('Historial de alertas'), findsWidgets);
    expect(find.text('Recordatorios'), findsWidgets);
    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Sala conectada'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
    expect(find.text('Normal'), findsWidgets);
    expect(find.textContaining('Lectura'), findsWidgets);
    expect(find.textContaining('Umbral'), findsWidgets);
  });

  testWidgets('escritorio 1200 alerta de gas', (tester) async {
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
      sensores: Sensores(
          estado: conectado,
          temperaturaC: 22.5,
          humedadPct: 45,
          movimiento: false,
          gasAdc: 2100,
          recibidoEn: ahora - 10),
      nodo: Componente(estado: conectado, recibidoEn: null),
      satelite: Componente(estado: conectado, recibidoEn: null),
      sitio: Componente(estado: conectado, recibidoEn: null),
      inactividad: Inactividad(ultimoMovimiento: ahora - 120, tiempoMin: 120, minimo: 2, maximo: 720),
      modo: 'normal',
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
    expect(tester.takeException(), isNull);

    expect(find.text('Panel'), findsWidgets);
    expect(find.text('Historial de alertas'), findsWidgets);
    expect(find.text('Recordatorios'), findsWidgets);
    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Alerta activa'), findsWidgets);
    expect(find.textContaining('Gas detectado'), findsWidgets);
    expect(find.text('Pendiente'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });

  testWidgets('movil 390 sin nodo', (tester) async {
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
      luz: Luz(estado: sinDatos, encendida: null, origen: null, recibidoEn: null),
      sensores: Sensores(
          estado: sinDatos,
          temperaturaC: null,
          humedadPct: null,
          movimiento: null,
          gasAdc: null,
          recibidoEn: null),
      nodo: Componente(estado: desconectado, recibidoEn: ahora - 600),
      satelite: Componente(estado: desconectado, recibidoEn: null),
      sitio: Componente(estado: desconectado, recibidoEn: ahora - 600),
      inactividad: Inactividad(ultimoMovimiento: null, tiempoMin: 120, minimo: 2, maximo: 720),
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
    expect(tester.takeException(), isNull);

    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Sin datos'), findsWidgets);
    expect(find.textContaining('Sin conexión'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });

  testWidgets('movil 390 con nodo', (tester) async {
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
      luz: Luz(estado: conectado, encendida: true, origen: 'voz', recibidoEn: null),
      sensores: Sensores(
          estado: conectado,
          temperaturaC: 22.5,
          humedadPct: 45,
          movimiento: false,
          gasAdc: 312,
          recibidoEn: ahora - 10),
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

    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Conectada'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });

  testWidgets('movil 390 alerta de gas', (tester) async {
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
      luz: Luz(estado: conectado, encendida: true, origen: 'voz', recibidoEn: null),
      sensores: Sensores(
          estado: conectado,
          temperaturaC: 22.5,
          humedadPct: 45,
          movimiento: false,
          gasAdc: 2100,
          recibidoEn: ahora - 10),
      nodo: Componente(estado: conectado, recibidoEn: null),
      satelite: Componente(estado: conectado, recibidoEn: null),
      sitio: Componente(estado: conectado, recibidoEn: null),
      inactividad: Inactividad(ultimoMovimiento: ahora - 120, tiempoMin: 120, minimo: 2, maximo: 720),
      modo: 'normal',
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
    expect(tester.takeException(), isNull);

    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Alerta activa'), findsWidgets);
    expect(find.textContaining('Gas detectado'), findsWidgets);
    expect(find.text('Pendiente'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });
  testWidgets('tableta 820 sin nodo', (tester) async {
    tester.view.physicalSize = const Size(820, 1180);
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
      luz: Luz(estado: sinDatos, encendida: null, origen: null, recibidoEn: null),
      sensores: Sensores(
          estado: sinDatos,
          temperaturaC: null,
          humedadPct: null,
          movimiento: null,
          gasAdc: null,
          recibidoEn: null),
      nodo: Componente(estado: desconectado, recibidoEn: ahora - 600),
      satelite: Componente(estado: desconectado, recibidoEn: null),
      sitio: Componente(estado: desconectado, recibidoEn: ahora - 600),
      inactividad: Inactividad(ultimoMovimiento: null, tiempoMin: 120, minimo: 2, maximo: 720),
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
    expect(tester.takeException(), isNull);

    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Sin datos'), findsWidgets);
    expect(find.textContaining('Sin conexión'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });

  testWidgets('tableta 820 con nodo', (tester) async {
    tester.view.physicalSize = const Size(820, 1180);
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
      sensores: Sensores(
          estado: conectado,
          temperaturaC: 22.5,
          humedadPct: 45,
          movimiento: false,
          gasAdc: 312,
          recibidoEn: ahora - 10),
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

    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Sala conectada'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });

  testWidgets('tableta 820 alerta de gas', (tester) async {
    tester.view.physicalSize = const Size(820, 1180);
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
      sensores: Sensores(
          estado: conectado,
          temperaturaC: 22.5,
          humedadPct: 45,
          movimiento: false,
          gasAdc: 2100,
          recibidoEn: ahora - 10),
      nodo: Componente(estado: conectado, recibidoEn: null),
      satelite: Componente(estado: conectado, recibidoEn: null),
      sitio: Componente(estado: conectado, recibidoEn: null),
      inactividad: Inactividad(ultimoMovimiento: ahora - 120, tiempoMin: 120, minimo: 2, maximo: 720),
      modo: 'normal',
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
    expect(tester.takeException(), isNull);

    expect(find.text('Familiares').evaluate().isNotEmpty ||
        find.text('Familia').evaluate().isNotEmpty, isTrue);
    expect(find.text('Mensajes'), findsWidgets);
    expect(find.text('Alerta activa'), findsWidgets);
    expect(find.textContaining('Gas detectado'), findsWidgets);
    expect(find.text('Pendiente'), findsWidgets);
    expect(find.text('Cambiar'), findsOneWidget);
  });

  testWidgets('movil 390 recordatorios titulo y boton no se superponen', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final EstadoPanel estado = EstadoPanel();
    estado.api.ponerToken('token-de-prueba');
    estado.preparando = false;
    estado.avisoPendiente = false;
    estado.errorArranque = null;
    estado.destino = Destino.recordatorios;
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

    await tester.pumpWidget(
      MaterialApp(
        theme: Tema.construir(),
        home: AsistentePanel(estado: estado, arrancar: false),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);

    expect(find.text('Recordatorios'), findsWidgets);
    expect(find.text('Nuevo recordatorio'), findsOneWidget);

    final Rect titulo = tester.getRect(find.text('Recordatorios').first);
    final Rect boton = tester.getRect(find.text('Nuevo recordatorio'));
    expect(boton.top >= titulo.bottom || titulo.top >= boton.bottom, isTrue);
  });

// --- Alertas por tipo (Fase 2 del ajuste del panel) -------------------------

Future<void> _montarConAlertas(WidgetTester tester, int ancho, List<Alerta> alertas) async {
  tester.view.physicalSize = Size(ancho.toDouble(), 900);
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
    luz: Luz(estado: conectado, encendida: true, origen: 'panel', recibidoEn: null),
    sensores: Sensores(
        estado: conectado,
        temperaturaC: 22.5,
        humedadPct: 45,
        movimiento: true,
        gasAdc: 312,
        recibidoEn: ahora - 10),
    nodo: Componente(estado: conectado, recibidoEn: null),
    satelite: Componente(estado: conectado, recibidoEn: null),
    sitio: Componente(estado: conectado, recibidoEn: null),
    inactividad: Inactividad(ultimoMovimiento: ahora - 120, tiempoMin: 120, minimo: 2, maximo: 720),
    modo: 'normal',
    alertas: <String, int>{'pendiente': alertas.length},
    familiares: 2,
    conexiones: 1,
    gasOnAdc: 1800,
  );
  estado.alertas = alertas;
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
  // expect(tester.takeException(), isNull);
}

testWidgets('alerta de gas en 390, 820 y 1280', (tester) async {
  for (final int ancho in <int>[390, 820, 1280]) {
    await _montarConAlertas(tester, ancho, <Alerta>[
      Alerta(
        id: 'g1',
        tipo: 'gas',
        estado: 'pendiente',
        creadoEn: DateTime.now().millisecondsSinceEpoch ~/ 1000 - 20,
        carga: const <String, dynamic>{'gas_adc': 2100},
        resueltaEn: null,
      ),
    ]);
    expect(find.text('Gas detectado'), findsWidgets);
    expect(find.text('Alerta activa en la sala'), findsWidgets);
  }
});

testWidgets('ayuda por voz en 390, 820 y 1280', (tester) async {
  for (final int ancho in <int>[390, 820, 1280]) {
    await _montarConAlertas(tester, ancho, <Alerta>[
      Alerta(
        id: 'a1',
        tipo: 'ayuda',
        estado: 'pendiente',
        creadoEn: DateTime.now().millisecondsSinceEpoch ~/ 1000 - 20,
        carga: const <String, dynamic>{'origen': 'voz'},
        resueltaEn: null,
      ),
    ]);
    expect(find.text('Piden ayuda'), findsWidgets);
    expect(find.text('Mensaje para la sala'), findsWidgets);
    expect(find.textContaining('Piden ayuda en la sala'), findsWidgets);
    expect(find.text('Se pidió ayuda por voz.'), findsWidgets);
    expect(find.text('Gas detectado'), findsNothing);
  }
});

testWidgets('ayuda por Alexa en 390, 820 y 1280', (tester) async {
  for (final int ancho in <int>[390, 820, 1280]) {
    await _montarConAlertas(tester, ancho, <Alerta>[
      Alerta(
        id: 'a2',
        tipo: 'ayuda',
        estado: 'pendiente',
        creadoEn: DateTime.now().millisecondsSinceEpoch ~/ 1000 - 20,
        carga: const <String, dynamic>{'origen': 'alexa'},
        resueltaEn: null,
      ),
    ]);
    expect(find.text('Piden ayuda'), findsWidgets);
    expect(find.text('Se pidió ayuda por Alexa.'), findsWidgets);
    expect(find.text('Gas detectado'), findsNothing);
  }
});

testWidgets('inactividad en 390, 820 y 1280', (tester) async {
  for (final int ancho in <int>[390, 820, 1280]) {
    await _montarConAlertas(tester, ancho, <Alerta>[
      Alerta(
        id: 'i1',
        tipo: 'inactividad',
        estado: 'pendiente',
        creadoEn: DateTime.now().millisecondsSinceEpoch ~/ 1000 - 20,
        carga: const <String, dynamic>{'minutos': 120},
        resueltaEn: null,
      ),
    ]);
    expect(find.text('Sin movimiento'), findsWidgets);
    expect(find.text('Sin movimiento en la sala'), findsWidgets);
    expect(find.text('No se ha detectado movimiento en 120 minutos.'), findsWidgets);
    expect(find.text('Gas detectado'), findsNothing);
  }
});

testWidgets('tipo desconocido en 390, 820 y 1280', (tester) async {
  for (final int ancho in <int>[390, 820, 1280]) {
    await _montarConAlertas(tester, ancho, <Alerta>[
      Alerta(
        id: 'x1',
        tipo: 'desconocido',
        estado: 'pendiente',
        creadoEn: DateTime.now().millisecondsSinceEpoch ~/ 1000 - 20,
        carga: const <String, dynamic>{},
        resueltaEn: null,
      ),
    ]);
    expect(find.text('Aviso'), findsWidgets);
    expect(find.text('Gas detectado'), findsNothing);
  }
});

testWidgets('gas y ayuda a la vez: manda gas en 390, 820 y 1280', (tester) async {
  for (final int ancho in <int>[390, 820, 1280]) {
    await _montarConAlertas(tester, ancho, <Alerta>[
      Alerta(
        id: 'g2',
        tipo: 'gas',
        estado: 'pendiente',
        creadoEn: DateTime.now().millisecondsSinceEpoch ~/ 1000 - 30,
        carga: const <String, dynamic>{'gas_adc': 2100},
        resueltaEn: null,
      ),
      Alerta(
        id: 'a3',
        tipo: 'ayuda',
        estado: 'pendiente',
        creadoEn: DateTime.now().millisecondsSinceEpoch ~/ 1000 - 10,
        carga: const <String, dynamic>{'origen': 'voz'},
        resueltaEn: null,
      ),
    ]);
    expect(find.text('Gas detectado'), findsWidgets);
    expect(find.text('Piden ayuda'), findsNothing);
  }
});


  for (final Size tamano in <Size>[const Size(390, 844), const Size(820, 1180), const Size(1280, 900)]) {
    testWidgets('chat a ${tamano.width} px no se rompe', (tester) async {
      tester.view.physicalSize = tamano;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final EstadoPanel estado = EstadoPanel();
      estado.api.ponerToken('token-de-prueba');
      estado.preparando = false;
      estado.avisoPendiente = false;
      estado.errorArranque = null;
      estado.destino = Destino.mensajes;
      estado.conversacion = <EntradaConversacion>[
        EntradaConversacion(id: 1, creadoEn: estado.ahoraLocal(), tipo: 'mensaje', autor: 'Ana', texto: 'hola', estado: 'enviado', estadoTexto: 'Enviado'),
        EntradaConversacion(id: 2, creadoEn: estado.ahoraLocal(), tipo: 'respuesta', autor: 'La sala', texto: 'bien', estadoTexto: ''),
      ];
      await tester.pumpWidget(MaterialApp(theme: Tema.construir(), home: AsistentePanel(estado: estado, arrancar: false)));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(tester.takeException(), isNull);
      expect(find.text('Enviar a la sala'), findsWidgets);
    });
  }


  testWidgets('conversacion real: sin ":" suelto y titulo de alerta', (tester) async {
    tester.view.physicalSize = const Size(1280, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final EstadoPanel estado = EstadoPanel();
    estado.api.ponerToken('token-de-prueba');
    estado.preparando = false;
    estado.avisoPendiente = false;
    estado.errorArranque = null;
    estado.conectadoAlServidor = true;
    estado.destino = Destino.inicio;
    final int ahora = estado.ahoraLocal();
    final Map<String, dynamic> alertaJson = <String, dynamic>{
      'id': 'a1',
      'tipo': 'ayuda',
      'estado': 'pendiente',
      'creado_en': ahora,
      'carga': <String, dynamic>{'origen': 'voz'},
      'resuelta_en': null,
      'resuelto_por': null,
      'descartada_en': null,
      'enviados': 0,
      'sin_mandar': 1,
    };
    estado.conversacion = <EntradaConversacion>[
      EntradaConversacion.desde(<String, dynamic>{
        'id': 1,
        'creado_en': ahora,
        'tipo': 'mensaje',
        'autor': 'Ana',
        'origen': 'panel',
        'texto': 'hola',
        'estado': 'enviado',
        'estado_texto': 'Enviado',
      }),
      EntradaConversacion.desde(<String, dynamic>{
        'id': 2,
        'creado_en': ahora,
        'tipo': 'respuesta',
        'autor': 'La sala',
        'origen': null,
        'texto': 'bien',
        'estado': 'se_dijo',
        'estado_texto': 'Se dijo en la sala',
      }),
      EntradaConversacion.desde(<String, dynamic>{
        'id': 3,
        'creado_en': ahora,
        'tipo': 'alerta',
        'autor': null,
        'origen': null,
        'texto': null,
        'estado': 'pendiente',
        'estado_texto': 'Pendiente',
        'alerta': alertaJson,
        'alerta_id': 'a1',
      }),
    ];

    await tester.pumpWidget(MaterialApp(theme: Tema.construir(), home: AsistentePanel(estado: estado, arrancar: false)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);
    expect(estado.conversacion[2].alerta!.sinResolver, isTrue);
    expect(find.text(':'), findsNothing);
    expect(find.textContaining('Piden ayuda en la sala'), findsWidgets);

    estado.irA(Destino.mensajes);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);
    expect(find.text(':'), findsNothing);
    expect(find.textContaining('Piden ayuda en la sala'), findsWidgets);
    expect(find.byType(FilledButton), findsNWidgets(2));
  });


  testWidgets('boton Enviar desactivado con campo vacio', (tester) async {
    tester.view.physicalSize = const Size(1280, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final EstadoPanel estado = EstadoPanel();
    estado.api.ponerToken('token-de-prueba');
    estado.preparando = false;
    estado.avisoPendiente = false;
    estado.errorArranque = null;
    estado.conectadoAlServidor = true;
    estado.destino = Destino.inicio;
    estado.conversacion = <EntradaConversacion>[];

    await tester.pumpWidget(MaterialApp(theme: Tema.construir(), home: AsistentePanel(estado: estado, arrancar: false)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);

    final Finder botonFinder = find.widgetWithText(FilledButton, Textos.enviarYDecirEnVozAlta);
    expect(botonFinder, findsOneWidget);
    final FilledButton boton = tester.widget<FilledButton>(botonFinder);
    expect(boton.onPressed, isNull);
  });


  testWidgets('conversacion real a 1200 y 390 px', (tester) async {
    for (final int ancho in <int>[1200, 390]) {
      tester.view.physicalSize = Size(ancho.toDouble(), 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final EstadoPanel estado = EstadoPanel();
      estado.api.ponerToken('token-de-prueba');
      estado.preparando = false;
      estado.avisoPendiente = false;
      estado.errorArranque = null;
      estado.conectadoAlServidor = true;
      estado.destino = Destino.inicio;
      final int ahora = estado.ahoraLocal();
      final Map<String, dynamic> alertaJson = <String, dynamic>{
        'id': 'a1',
        'tipo': 'ayuda',
        'estado': 'pendiente',
        'creado_en': ahora,
        'carga': <String, dynamic>{'origen': 'voz'},
        'resuelta_en': null,
        'resuelto_por': null,
        'descartada_en': null,
        'enviados': 0,
        'sin_mandar': 1,
      };
      estado.conversacion = <EntradaConversacion>[
        EntradaConversacion.desde(<String, dynamic>{
          'id': 1,
          'creado_en': ahora,
          'tipo': 'mensaje',
          'autor': 'Ana',
          'origen': 'panel',
          'texto': 'hola',
          'estado': 'enviado',
          'estado_texto': 'Enviado',
        }),
        EntradaConversacion.desde(<String, dynamic>{
          'id': 2,
          'creado_en': ahora,
          'tipo': 'respuesta',
          'autor': 'La sala',
          'origen': null,
          'texto': 'bien',
          'estado': 'se_dijo',
          'estado_texto': 'Se dijo en la sala',
        }),
        EntradaConversacion.desde(<String, dynamic>{
          'id': 3,
          'creado_en': ahora,
          'tipo': 'alerta',
          'autor': null,
          'origen': null,
          'texto': null,
          'estado': 'pendiente',
          'estado_texto': 'Pendiente',
          'alerta': alertaJson,
          'alerta_id': 'a1',
        }),
      ];

      await tester.pumpWidget(MaterialApp(theme: Tema.construir(), home: AsistentePanel(estado: estado, arrancar: false)));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(tester.takeException(), isNull);
      expect(find.text(':'), findsNothing);
      expect(find.textContaining('Piden ayuda en la sala'), findsWidgets);
    }
  });

  testWidgets('Enviar llama a la API y limpia el campo', (tester) async {
    tester.view.physicalSize = const Size(1280, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final EstadoPanel estado = EstadoPanel(api: _ApiFalsaExito());
    estado.api.ponerToken('token-de-prueba');
    estado.preparando = false;
    estado.avisoPendiente = false;
    estado.errorArranque = null;
    estado.conectadoAlServidor = true;
    estado.destino = Destino.inicio;
    estado.conversacion = <EntradaConversacion>[];

    await tester.pumpWidget(MaterialApp(theme: Tema.construir(), home: AsistentePanel(estado: estado, arrancar: false)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    await tester.enterText(find.byType(TextField), 'hola');
    await tester.pump();
    final FilledButton boton = tester.widget<FilledButton>(find.widgetWithText(FilledButton, Textos.enviarYDecirEnVozAlta));
    expect(boton.onPressed, isNotNull);
    await tester.tap(find.widgetWithText(FilledButton, Textos.enviarYDecirEnVozAlta));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(tester.takeException(), isNull);
    expect(find.text('hola'), findsWidgets);
    final TextField campo = tester.widget<TextField>(find.byType(TextField));
    expect(campo.controller!.text, isEmpty);
  });

  testWidgets('429 muestra su aviso', (tester) async {
    tester.view.physicalSize = const Size(1280, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final EstadoPanel estado = EstadoPanel(api: _ApiFalsa429());
    estado.api.ponerToken('token-de-prueba');
    estado.preparando = false;
    estado.avisoPendiente = false;
    estado.errorArranque = null;
    estado.conectadoAlServidor = true;
    estado.destino = Destino.inicio;
    estado.conversacion = <EntradaConversacion>[];

    await tester.pumpWidget(MaterialApp(theme: Tema.construir(), home: AsistentePanel(estado: estado, arrancar: false)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    await tester.enterText(find.byType(TextField), 'hola');
    await tester.pump();
    await tester.tap(find.widgetWithText(FilledButton, Textos.enviarYDecirEnVozAlta));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text(Textos.chatDemasiadoRapido), findsWidgets);
  });

  testWidgets('boton hablar visible si el satelite reporta hablar true', (tester) async {
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
      luz: Luz(estado: sinDatos, encendida: null, origen: null, recibidoEn: null),
      sensores: Sensores(
          estado: sinDatos,
          temperaturaC: null,
          humedadPct: null,
          movimiento: null,
          gasAdc: null,
          recibidoEn: null),
      nodo: Componente(estado: conectado, recibidoEn: ahora),
      satelite: Componente(
          estado: conectado,
          recibidoEn: ahora,
          satelites: const <String, bool>{'satelite-sala': true},
          satelitesHablar: const <String, bool>{'satelite-sala': true}),
      sitio: Componente(estado: conectado, recibidoEn: ahora),
      inactividad: Inactividad(ultimoMovimiento: null, tiempoMin: 120, minimo: 2, maximo: 720),
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
    expect(tester.takeException(), isNull);
    expect(find.text('Mantener para hablar'), findsOneWidget);
  });

  testWidgets('boton hablar oculto si el satelite no reporta hablar', (tester) async {
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
      luz: Luz(estado: sinDatos, encendida: null, origen: null, recibidoEn: null),
      sensores: Sensores(
          estado: sinDatos,
          temperaturaC: null,
          humedadPct: null,
          movimiento: null,
          gasAdc: null,
          recibidoEn: null),
      nodo: Componente(estado: conectado, recibidoEn: ahora),
      satelite: Componente(
          estado: conectado,
          recibidoEn: ahora,
          satelites: const <String, bool>{'satelite-sala': true},
          satelitesHablar: const <String, bool>{'satelite-sala': false}),
      sitio: Componente(estado: conectado, recibidoEn: ahora),
      inactividad: Inactividad(ultimoMovimiento: null, tiempoMin: 120, minimo: 2, maximo: 720),
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
    expect(tester.takeException(), isNull);
    expect(find.text('Mantener para hablar'), findsNothing);
  });

}


class _ApiFalsaExito extends ClienteApi {
  @override
  Future<EntradaConversacion> enviarMensaje(String texto) async {
    return EntradaConversacion.desde(<String, dynamic>{
      'id': 99,
      'creado_en': DateTime.now().millisecondsSinceEpoch ~/ 1000,
      'tipo': 'mensaje',
      'autor': 'Yo',
      'origen': 'panel',
      'texto': texto,
      'estado': 'enviado',
      'estado_texto': 'Enviado',
    });
  }
}

class _ApiFalsa429 extends ClienteApi {
  @override
  Future<EntradaConversacion> enviarMensaje(String texto) async {
    throw const ErrorApi(Textos.chatDemasiadoRapido, codigo: 429);
  }
}
