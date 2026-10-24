import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:asistente_panel/main.dart';
import 'package:asistente_panel/tema.dart';
import 'package:asistente_panel/uis/navegacion.dart';

/// Las pruebas de rejilla que se quedan para siempre.
///
/// Reproducen el fallo de 2026-10-18: un `FilledButton` con el tema del panel
/// dentro de un `Row` con un `Expanded` al lado. Con el ancho mínimo del botón en
/// `double.infinity`, el `Expanded` le pasaba un ancho infinito y Flutter
/// reventaba con «BoxConstraints forces an infinite width», que se veía como el
/// título pegando una letra bajo otra. Se comprueba en todos los anchos del
/// diseño: el móvil, la tableta, el escritorio y el muy ancho.
///
/// Los anchos son los de las capturas que se revisaron: 390 (teléfono), 768
/// (tableta), 1024, 1280, 1440 y 1920. El corte de escritorio está en 900 px y
/// se prueba aparte, con la cabecera.
const List<double> anchosDelDiseno = <double>[390, 768, 1024, 1280, 1440, 1920];

void main() {
  testWidgets('los botones del tema no imponen un ancho mínimo infinito', (
    tester,
  ) async {
    final ThemeData tema = Tema.construir();
    final Size? minimo = tema.filledButtonTheme.style!.minimumSize!.resolve({});
    expect(minimo, isNotNull);
    expect(minimo!.width, 0);
    final Size? minimoOutlined = tema.outlinedButtonTheme.style!.minimumSize!
        .resolve({});
    expect(minimoOutlined, isNotNull);
    expect(minimoOutlined!.width, 0);
  });

  testWidgets('el umbral de escritorio es el del diseño', (tester) async {
    expect(Tema.anchoEscritorio, 900);
    expect(Tema.altoMinimo, 52);
  });

  testWidgets('en escritorio la cabecera enseña las cuatro pestañas', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: Tema.construir(),
        home: PanelScope(
          estado: EstadoPanel(),
          child: const Scaffold(
            body: EstructuraPanel(contenido: SizedBox.shrink()),
          ),
        ),
      ),
    );

    expect(find.text('Panel'), findsOneWidget);
    expect(find.text('Historial de alertas'), findsOneWidget);
    expect(find.text('Recordatorios'), findsOneWidget);
    expect(find.text('Familiares'), findsOneWidget);
    expect(find.text('Mensajes'), findsNothing);
    expect(find.byType(BottomNavigationBar), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('en el móvil la barra tiene cuatro con Familiares', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: Tema.construir(),
        home: PanelScope(
          estado: EstadoPanel(),
          child: const Scaffold(
            body: EstructuraPanel(contenido: SizedBox.shrink()),
          ),
        ),
      ),
    );

    expect(find.text('Inicio'), findsOneWidget);
    expect(find.text('Alertas'), findsOneWidget);
    expect(find.text('Recordatorios'), findsOneWidget);
    expect(find.text('Familiares'), findsWidgets);
    expect(find.text('Mensajes'), findsNothing);
    expect(find.byTooltip('Familiares'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  for (final double ancho in anchosDelDiseno) {
    testWidgets(
      'el título de una tarjeta y su botón caben en fila a $ancho px',
      (tester) async {
        tester.view.physicalSize = Size(ancho, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);

        await tester.pumpWidget(
          MaterialApp(
            theme: Tema.construir(),
            home: Scaffold(
              body: SizedBox(
                width: ancho,
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        'Recordatorios',
                        style: Tema.textoTema.titleLarge,
                      ),
                    ),
                    FilledButton(onPressed: () {}, child: const Text('Nueva')),
                  ],
                ),
              ),
            ),
          ),
        );

        // Esto es la prueba entera: con el bug, el `Expanded` recibía un ancho
        // infinito y el layout reventaba aquí.
        expect(tester.takeException(), isNull);

        final Size titulo = tester.getSize(find.text('Recordatorios'));
        expect(
          titulo.width,
          greaterThan(80),
          reason: 'el título ha quedado comprimido a lo ancho',
        );
        expect(
          titulo.height,
          lessThan(2 * Tema.altoMinimo),
          reason: 'el título está partiendo el texto en vertical',
        );
        final Size boton = tester.getSize(find.text('Nueva'));
        expect(boton.width, greaterThan(10));
      },
    );
  }
  testWidgets(
    'el campo de minutos y su botón de guardado caben a todos los anchos',
    (tester) async {
      for (final double ancho in anchosDelDiseno) {
        tester.view.physicalSize = Size(ancho, 900);
        tester.view.devicePixelRatio = 1;

        await tester.pumpWidget(
          MaterialApp(
            theme: Tema.construir(),
            home: Scaffold(
              body: SizedBox(
                width: ancho,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Row(
                      children: <Widget>[
                        Expanded(
                          child: TextField(
                            decoration: const InputDecoration(
                              suffixText: 'minutos',
                            ),
                          ),
                        ),
                        const SizedBox(width: 8),
                        FilledButton(
                          onPressed: () {},
                          child: const Text('Guardar'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        );

        expect(
          tester.takeException(),
          isNull,
          reason: 'a $ancho px el campo seguía pidiendo ancho infinito',
        );
        final Size campo = tester.getSize(find.byType(TextField));
        expect(
          campo.width,
          lessThan(ancho),
          reason: 'a $ancho px el campo ha ocupado el ancho entero',
        );
      }
      tester.view.reset();
    },
  );
}
