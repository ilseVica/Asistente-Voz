import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/modelos.dart';
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
    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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
    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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
    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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

    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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

    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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

    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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

    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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

    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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

    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
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

}
