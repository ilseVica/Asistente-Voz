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
    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: movil ? double.infinity : 760),
            child: SingleChildScrollView(
              padding: EdgeInsets.all(movil ? 20 : 32),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      Container(
                        width: 48,
                        height: 48,
                        decoration: BoxDecoration(
                            color: Tema.suave,
                            borderRadius: BorderRadius.circular(14)),
                        child: const Icon(Icons.shield_outlined,
                            color: Tema.primario, size: 26),
                      ),
                      const SizedBox(width: 14),
                      Text(Textos.susDatos,
                          style: TextStyle(
                              fontSize: 30,
                              fontWeight: FontWeight.w700,
                              color: Tema.texto,
                              fontFamily: Tema.familia)),
                    ],
                  ),
                  const SizedBox(height: 20),
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
          style: const TextStyle(
              fontSize: 18, color: Tema.secundario, height: 1.6,
              fontFamily: Tema.familia)),
    );
  }
}
