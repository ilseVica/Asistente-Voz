import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';
import '../presentacion_alerta.dart';
import 'navegacion.dart';

class PantallaChat extends StatefulWidget {
  const PantallaChat({super.key});

  @override
  State<PantallaChat> createState() => _PantallaChatState();
}

class _PantallaChatState extends State<PantallaChat> {
  final TextEditingController _controlador = TextEditingController();
  final ScrollController _scroll = ScrollController();
  bool _cargandoMas = false;

  @override
  void dispose() {
    _controlador.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _enviar() async {
    final EstadoPanel estado = PanelScope.de(context);
    final String texto = _controlador.text;
    if (texto.trim().isEmpty) return;
    if (estado.enviandoMensaje) return;
    final bool ok = await estado.enviarMensaje(texto);
    if (ok) {
      _controlador.clear();
      _irAlFinal();
    }
  }

  Future<void> _cargarMas() async {
    if (_cargandoMas) return;
    final EstadoPanel estado = PanelScope.de(context);
    if (estado.conversacion.isEmpty) return;
    _cargandoMas = true;
    setState(() {});
    await estado.cargarConversacion(antesId: estado.conversacion.first.id);
    _cargandoMas = false;
    if (mounted) setState(() {});
  }

  void _irAlFinal() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 250),
            curve: Curves.easeOut);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final EstadoPanel estado = PanelScope.de(context);
    final double ancho = MediaQuery.sizeOf(context).width;
    final bool compacto = ancho < Tema.anchoTableta;

    return EstructuraPanel(
      contenido: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          // Encabezado
          Row(
            children: <Widget>[
              Expanded(
                child: Text(Textos.mensajes,
                    style: Theme.of(context).textTheme.titleLarge),
              ),
              if (estado.cargandoConversacion)
                const SizedBox(
                    width: 18, height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2)),
            ],
          ),
          const SizedBox(height: 12),
          // Lista
          Container(
            constraints: BoxConstraints(minHeight: compacto ? 320 : 420),
            child: estado.conversacion.isEmpty && !estado.cargandoConversacion
                ? Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(Textos.mensajesVacio,
                        style: Theme.of(context).textTheme.bodySmall),
                  )
                : Column(
                    children: <Widget>[
                      if (estado.conversacion.length >= 50)
                        TextButton(
                          onPressed: _cargandoMas ? null : _cargarMas,
                          child: Text(_cargandoMas
                              ? Textos.cargando
                              : Textos.verMasAntiguos),
                        ),
                      ListView.builder(
                        controller: _scroll,
                        shrinkWrap: true,
                        physics: const NeverScrollableScrollPhysics(),
                        itemCount: estado.conversacion.length,
                        itemBuilder: (BuildContext ctx, int i) {
                          return _Burbuja(entrada: estado.conversacion[i]);
                        },
                      ),
                    ],
                  ),
          ),
          const SizedBox(height: 12),
          // Entrada
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              Expanded(
                child: TextField(
                  controller: _controlador,
                  maxLength: 200,
                  maxLines: 3,
                  minLines: 1,
                  decoration: InputDecoration(
                    hintText: Textos.chatPlaceholder,
                    counterText: '',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: estado.enviandoMensaje ? null : _enviar,
                child: Text(Textos.chatEnviar),
              ),
            ],
          ),
          Align(
            alignment: Alignment.centerRight,
            child: ValueListenableBuilder<TextEditingValue>(
              valueListenable: _controlador,
              builder: (_, TextEditingValue v, __) {
                return Text('${v.text.length}/200',
                    style: Theme.of(context).textTheme.bodySmall);
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _Burbuja extends StatelessWidget {
  const _Burbuja({required this.entrada});
  final EntradaConversacion entrada;

  @override
  Widget build(BuildContext context) {
    final bool propio = entrada.tipo == 'mensaje' &&
        entrada.origen == 'panel'; // burbuja derecha para los del panel
    // Alineación: mensajes del usuario actual a la derecha no se puede saber sin
    // chat_id; se alinea por autor != 'La sala' a la derecha por simplicidad.
    final bool derecha = entrada.tipo == 'mensaje';

    if (entrada.tipo == 'aviso') {
      return Card(
        color: Tema.caja,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Text(entrada.texto ?? '',
              style: const TextStyle(color: Tema.secundario)),
        ),
      );
    }

    if (entrada.esAlerta) {
      final PresentacionAlerta pres = presentacionAlerta(
        entrada.alerta?.tipo ?? 'aviso',
        origen: entrada.alerta?.carga['origen']?.toString(),
        carga: entrada.alerta?.carga,
      );
      return Card(
        color: pres.colorFondo,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: <Widget>[
              Icon(pres.icono, color: pres.colorTexto),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(pres.titulo,
                        style: TextStyle(
                            color: pres.colorTexto,
                            fontWeight: FontWeight.w700)),
                    Text(pres.cuerpo,
                        style: TextStyle(color: pres.colorTexto)),
                    if (entrada.alerta != null &&
                        entrada.alerta!.sinResolver)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: FilledButton(
                          onPressed: () {
                            // Atender via API
                          },
                          child: Text(Textos.yaLoAtendi),
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
    }

    final Color fondo = derecha ? Tema.suave : Tema.tarjeta;
    return Align(
      alignment: derecha ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.all(10),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.sizeOf(context).width * 0.7),
        decoration: BoxDecoration(
          color: fondo,
          borderRadius: BorderRadius.circular(Tema.radio),
          border: Border.all(color: Tema.borde),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(entrada.autor ?? '',
                style: const TextStyle(
                    fontSize: 12, fontWeight: FontWeight.w700,
                    color: Tema.secundario)),
            if (entrada.texto != null) Text(entrada.texto!),
            if (entrada.estadoTexto != null && entrada.estadoTexto!.isNotEmpty)
              Text(entrada.estadoTexto!,
                  style: const TextStyle(
                      fontSize: 11, color: Tema.secundario)),
            if (entrada.esRespuesta)
              Text('La sala respondió',
                  style: const TextStyle(
                      fontSize: 11, color: Tema.primario)),
          ],
        ),
      ),
    );
  }
}
