import 'package:flutter/material.dart';

import '../main.dart';
import '../tema.dart';
import '../textos.dart';

/// Sin token: explica cómo conseguirlo y acepta un enlace pegado.
///
/// El campo de pegar no es adorno. El enlace abre el navegador del sistema y en
/// algunos Android sale un navegador distinto del que está autenticado; sin poder
/// pegar el enlace a mano, la sesión se pierde y hay que volver a pedir `/panel`.
class PantallaAcceso extends StatefulWidget {
  const PantallaAcceso({super.key});

  @override
  State<PantallaAcceso> createState() => _PantallaAccesoState();
}

class _PantallaAccesoState extends State<PantallaAcceso> {
  final TextEditingController _control = TextEditingController();

  @override
  void initState() {
    super.initState();
    // Si se entró en esta misma sesión, el campo ya lleva el token para copiarlo.
    _control.text = PanelScope.de(context).sesionNueva ?? '';
  }

  @override
  void dispose() {
    _control.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final TextTheme tipografia = Theme.of(contexto).textTheme;
    final bool recienEntrado = estado.sesionNueva != null;

    final Widget campos = estado.preparando
        ? const Padding(
            padding: EdgeInsets.symmetric(vertical: 24),
            child: Center(child: CircularProgressIndicator()),
          )
        : Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              TextField(
                controller: _control,
                keyboardType: TextInputType.text,
                autocorrect: false,
                enableSuggestions: false,
                readOnly: recienEntrado,
                style: const TextStyle(fontSize: 16),
                decoration: InputDecoration(
                  hintText: recienEntrado
                      ? Textos.pistaClave
                      : Textos.pistaEnlace,
                ),
                onSubmitted: recienEntrado
                    ? (_) => estado.entrarAlPanel()
                    : (String valor) => _entrar(estado, valor),
              ),
              if (recienEntrado) ...<Widget>[
                const SizedBox(height: 10),
                Text(
                  Textos.recienEntrado,
                  style: tipografia.bodySmall,
                ),
              ],
              const SizedBox(height: 12),
              FilledButton(
                onPressed: estado.preparando
                    ? null
                    : recienEntrado
                        ? estado.entrarAlPanel
                        : () => _entrar(estado, _control.text),
                child: const Text(Textos.entrarAlPanel),
              ),
            ],
          );

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Text(Textos.panelDeLaSala,
                      style: tipografia.headlineMedium),
                  const SizedBox(height: 6),
                  Text(Textos.asistenteDeVoz,
                      style: tipografia.bodySmall),
                  const SizedBox(height: 28),
                  const _Paso(numero: '1', texto: Textos.pasoUno),
                  const _Paso(numero: '2', texto: Textos.pasoDos),
                  const _Paso(numero: '3', texto: Textos.pasoTres),
                  const SizedBox(height: 24),
                  Text(Textos.yaLoTiene,
                      style: tipografia.titleMedium),
                  const SizedBox(height: 4),
                  Text(
                    Textos.ayudaEnlace,
                    style: tipografia.bodySmall,
                  ),
                  const SizedBox(height: 12),
                  campos,
                  if (estado.aviso != null) ...<Widget>[
                    const SizedBox(height: 16),
                    _Aviso(texto: estado.aviso!),
                  ],
                  const SizedBox(height: 28),
                  Text(
                    Textos.piePrivacidad,
                    style: tipografia.bodySmall,
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _entrar(EstadoPanel estado, String valor) async {
    FocusManager.instance.primaryFocus?.unfocus();
    final String limpio = valor.trim();
    if (limpio.isEmpty) {
      estado.avisar(Textos.faltaEnlace);
      return;
    }
    await estado.entrar(limpio);
    // Entrado el token, el campo se queda con él delante para copiarlo.
    _control.text = estado.sesionNueva ?? '';
  }
}

class _Paso extends StatelessWidget {
  const _Paso({required this.numero, required this.texto});

  final String numero;
  final String texto;

  @override
  Widget build(BuildContext contexto) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Container(
            width: 30,
            height: 30,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
                color: Tema.suave, shape: BoxShape.circle),
            child: Text(numero,
                style: const TextStyle(
                    color: Tema.primario,
                    fontWeight: FontWeight.w700,
                    fontSize: 15)),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(texto,
                style: Theme.of(contexto)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(height: 1.4)),
          ),
        ],
      ),
    );
  }
}

class _Aviso extends StatelessWidget {
  const _Aviso({required this.texto});

  final String texto;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFFF6E9E9),
        borderRadius: BorderRadius.circular(Tema.radioBoton),
        border: Border.all(color: const Color(0xFFE3C9C9)),
      ),
      child: Text(texto,
          style: const TextStyle(color: Tema.peligro, fontSize: 15, height: 1.4)),
    );
  }
}
