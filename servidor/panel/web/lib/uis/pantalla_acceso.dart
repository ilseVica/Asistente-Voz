import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../main.dart';
import '../tema.dart';
import '../textos.dart';

/// Sin token: explica cómo conseguir el código y lo pide.
///
/// El campo es para el código de SEIS caracteres que manda `/panel`. Se teclea a
/// mano y también se puede pegar: si el familiar lo copia del mensaje de Telegram
/// y lo pega, entra igual. El campo pone en mayúsculas lo que se escribe y no
/// admite nada que no esté en el alfabeto del código (sin 0, 1, O, I ni L), para
/// que un carácter que no existe no llegue siquiera al servidor.
///
/// El teclado es de texto y no numérico a propósito: el código lleva letras, y
/// con un teclado numérico no se podría escribir.
class PantallaAcceso extends StatefulWidget {
  const PantallaAcceso({super.key});

  @override
  State<PantallaAcceso> createState() => _PantallaAccesoState();
}

class _PantallaAccesoState extends State<PantallaAcceso> {
  final TextEditingController _control = TextEditingController();

  @override
  void dispose() {
    _control.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final TextTheme tipografia = Theme.of(contexto).textTheme;

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
                textCapitalization: TextCapitalization.characters,
                autocorrect: false,
                enableSuggestions: false,
                maxLength: 6,
                inputFormatters: <TextInputFormatter>[_SoloCodigo()],
                style: const TextStyle(fontSize: 22, letterSpacing: 6),
                textAlign: TextAlign.center,
                decoration: const InputDecoration(
                  hintText: Textos.pistaCodigo,
                  counterText: '',
                ),
                onSubmitted: (String valor) => _entrar(estado, valor),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: estado.preparando
                    ? null
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
                    Textos.ayudaCodigo,
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
    await estado.entrar(valor);
  }
}

/// Deja pasar solo caracteres del alfabeto del código, en mayúsculas.
///
/// Es el mismo alfabeto que `panel_acceso.ALFABETO_CODIGO` en el orquestador
/// (sin 0, 1, O, I ni L). Se filtra al escribir y al pegar: pegar un código en
/// minúsculas del portapapeles lo mete en mayúsculas, y pegar el mensaje entero
/// del bot se queda solo con los caracteres que pueden ser del código.
class _SoloCodigo extends TextInputFormatter {
  static final RegExp _validos = RegExp(r'[23456789ABCDEFGHJKMNPQRSTUVWXYZ]');

  @override
  TextEditingValue formatEditUpdate(
      TextEditingValue anterior, TextEditingValue nuevo) {
    final List<String> limpio = <String>[];
    for (final int runa in nuevo.text.toUpperCase().runes) {
      final String caracter = String.fromCharCode(runa);
      if (_validos.hasMatch(caracter)) limpio.add(caracter);
    }
    final String texto = limpio.join();
    final String recortado = texto.length > 6 ? texto.substring(0, 6) : texto;
    return TextEditingValue(
      text: recortado,
      selection: TextSelection.collapsed(offset: recortado.length),
    );
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
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Tema.suave,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        texto,
        style: Theme.of(contexto).textTheme.bodySmall,
      ),
    );
  }
}