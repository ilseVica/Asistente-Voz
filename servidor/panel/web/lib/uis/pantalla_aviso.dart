import 'package:flutter/material.dart';

import '../main.dart';
import '../tema.dart';
import '../textos.dart';

/// El aviso de privacidad, una vez por navegador y SIN bloquear (decisión 8.1).
///
/// No hay casilla ni puerta que impida entrar: se entra, se ve el aviso, y se
/// continúa con un botón. El motivo es que una pantalla que se interpone justo
/// cuando un familiar está mirando si hay gas en casa es un fallo de la casa, no
/// una protección. El registro de verdad sigue siendo el de Telegram, y ahí el
/// mensaje de registro ya dice qué va a pasar.
class PantallaAviso extends StatelessWidget {
  const PantallaAviso({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Text(Textos.susDatos,
                      style: Theme.of(contexto).textTheme.headlineMedium),
                  const SizedBox(height: 16),
                  const _Parrafo(Textos.avisoUno),
                  const _Parrafo(Textos.avisoDos),
                  const _Parrafo(Textos.avisoTres),
                  const _Parrafo(Textos.avisoCuatro),
                  const SizedBox(height: 24),
                  FilledButton(
                    onPressed: estado.aceptarAviso,
                    child: const Text(Textos.entendido),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Parrafo extends StatelessWidget {
  const _Parrafo(this.texto);

  final String texto;

  @override
  Widget build(BuildContext contexto) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Text(texto,
          style: Theme.of(contexto)
              .textTheme
              .bodyMedium
              ?.copyWith(color: Tema.secundario, height: 1.5)),
    );
  }
}
