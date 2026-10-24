import 'package:flutter/material.dart';

import '../main.dart';
import '../tema.dart';
import '../textos.dart';
import 'navegacion.dart';

/// «Próximamente» para Recordatorios y Mensajes.
///
/// No lleva datos de ejemplo. Inventar un recordatorio o un mensaje en una
/// pantalla que todavía no existe es la forma más rápida de que alguien lo
/// pruebe en la demostración y se lleve la idea de que ya funciona.
class PantallaProximamente extends StatelessWidget {
  const PantallaProximamente(
      {super.key, required this.titulo, required this.texto});

  final String titulo;
  final String texto;

  @override
  Widget build(BuildContext contexto) {
    return EstructuraPanel(
      contenido: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(Icons.schedule, color: Tema.secundario, size: 44),
              const SizedBox(height: 16),
              Text(titulo,
                  style: Theme.of(contexto).textTheme.headlineMedium,
                  textAlign: TextAlign.center),
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                    color: Tema.suave,
                    borderRadius: BorderRadius.circular(999)),
                child: const Text(Textos.proximamente,
                    style: TextStyle(
                        color: Tema.primario,
                        fontWeight: FontWeight.w700,
                        fontSize: 14)),
              ),
              const SizedBox(height: 16),
              Text(texto,
                  style: Theme.of(contexto)
                      .textTheme
                      .bodyMedium
                      ?.copyWith(color: Tema.secundario, height: 1.5),
                  textAlign: TextAlign.center),
              const SizedBox(height: 28),
              OutlinedButton(
                onPressed: () => PanelScope.de(contexto).irA(Destino.inicio),
                style: OutlinedButton.styleFrom(
                    minimumSize: const Size(48, 48)),
                child: const Text(Textos.volverAlInicio),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
