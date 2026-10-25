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
                style: const TextStyle(fontSize: 28, letterSpacing: 8, fontWeight: FontWeight.w700),
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

    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Text(Textos.asistenteDeVoz,
                      style: TextStyle(
                          fontSize: movil ? 20 : 22,
                          fontWeight: FontWeight.w700,
                          color: Tema.primario,
                          fontFamily: Tema.familia),
                      textAlign: TextAlign.center),
                  const SizedBox(height: 24),
                  Container(
                    padding: const EdgeInsets.all(36),
                    decoration: BoxDecoration(
                      color: Tema.tarjeta,
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(color: Tema.borde),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: <Widget>[
                        Text(Textos.panelDeLaSala,
                            style: TextStyle(
                                fontSize: 28,
                                fontWeight: FontWeight.w700,
                                color: Tema.texto,
                                fontFamily: Tema.familia)),
                        const SizedBox(height: 8),
                        Text(
                          Textos.ayudaCodigo,
                          style: TextStyle(
                              fontSize: 17,
                              color: Tema.secundario,
                              height: 1.5,
                              fontFamily: Tema.familia),
                        ),
                        const SizedBox(height: 24),
                        Text(Textos.yaLoTiene,
                            style: TextStyle(
                                fontSize: 17,
                                fontWeight: FontWeight.w700,
                                color: Tema.texto,
                                fontFamily: Tema.familia)),
                        const SizedBox(height: 12),
                        campos,
                        if (estado.aviso != null) ...<Widget>[
                          const SizedBox(height: 16),
                          _Aviso(texto: estado.aviso!),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    Textos.piePrivacidad,
                    style: TextStyle(
                        fontSize: 15,
                        color: Tema.secundario,
                        fontFamily: Tema.familia),
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

class _Aviso extends StatelessWidget {
  const _Aviso({required this.texto});

  final String texto;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFF7E1E1),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        texto,
        style: const TextStyle(
            color: Color(0xFF7A1616), fontSize: 16, height: 1.45,
            fontFamily: Tema.familia),
      ),
    );
  }
}
