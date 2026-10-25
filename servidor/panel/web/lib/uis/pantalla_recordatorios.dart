import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';
import 'navegacion.dart';
import 'tarjetas.dart';

Future<void> abrirNuevoRecordatorio(BuildContext contexto, EstadoPanel estado) {
  return showModalBottomSheet<void>(
    context: contexto,
    isScrollControlled: true,
    builder: (BuildContext contexto) => _DialogoNuevoRecordatorio(estado: estado),
  );
}

/// Los recordatorios de la casa: lo que el asistente dirá en voz alta.
///
/// Cada tarjeta enseña, debajo del texto, la próxima vez y UNA línea de la
/// última entrega. No es un historial: es lo justo para saber si el recordatorio
/// está funcionando, sin convertir la pantalla en un registro.
class PantallaRecordatorios extends StatelessWidget {
  const PantallaRecordatorios({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;

    return EstructuraPanel(
      contenido: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          LayoutBuilder(builder: (BuildContext contexto, BoxConstraints c) {
            final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;
            final Widget titulo = Text(Textos.recordatorios,
                style: TextStyle(
                    fontSize: movil ? 24 : 28,
                    fontWeight: FontWeight.w700,
                    color: Tema.texto,
                    fontFamily: Tema.familia));
            final Widget boton = FilledButton.icon(
              onPressed: estado.cargandoRecordatorio
                  ? null
                  : () => _abrirNuevo(contexto, estado),
              icon: const Icon(Icons.add),
              label: const Text(Textos.nuevoRecordatorio),
            );
            if (c.maxWidth < 600) {
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  titulo,
                  const SizedBox(height: 12),
                  boton,
                ],
              );
            }
            return Row(
              children: <Widget>[
                Expanded(child: titulo),
                boton,
              ],
            );
          }),
          const SizedBox(height: 6),
          Text(Textos.recordatoriosSubtitulo,
              style: TextStyle(
                  fontSize: movil ? 15 : 17,
                  color: Tema.secundario,
                  fontFamily: Tema.familia)),
          const SizedBox(height: 20),
          if (estado.recordatorios.isEmpty)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  Textos.recordatoriosVacio,
                  style: Theme.of(contexto).textTheme.bodyMedium,
                ),
              ),
            )
          else
            for (final Recordatorio r in estado.recordatorios)
              _TarjetaRecordatorio(recordatorio: r, estado: estado),
          const SizedBox(height: 8),
          const PiePanel(),
        ],
      ),
    );
  }

  static Future<void> _abrirNuevo(BuildContext contexto, EstadoPanel estado) {
    return showDialog<void>(
      context: contexto,
      builder: (BuildContext contexto) => _DialogoNuevoRecordatorio(estado: estado),
    );
  }
}

class _TarjetaRecordatorio extends StatelessWidget {
  const _TarjetaRecordatorio({required this.recordatorio, required this.estado});

  final Recordatorio recordatorio;
  final EstadoPanel estado;

  @override
  Widget build(BuildContext contexto) {
    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;
    final TextTheme tipografia = Theme.of(contexto).textTheme;
    final Widget? ultima = _lineaUltima(tipografia);
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: EdgeInsets.all(movil ? 14 : 18),
        child: movil
            ? Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      Text(recordatorio.hora,
                          style: const TextStyle(
                              fontSize: 22,
                              fontWeight: FontWeight.w700,
                              fontFamily: Tema.familia)),
                      const SizedBox(width: 12),
                      Text(_repeticionCorta(),
                          style: const TextStyle(
                              fontSize: 14,
                              color: Tema.secundario,
                              fontFamily: Tema.familia)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(recordatorio.texto,
                      style: const TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w700,
                          fontFamily: Tema.familia)),
                  if (ultima != null) ...<Widget>[
                    const SizedBox(height: 4),
                    ultima,
                  ],
                  const SizedBox(height: 8),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: OutlinedButton(
                      onPressed: estado.cargandoRecordatorio
                          ? null
                          : () => _confirmarCancelar(contexto),
                      style: OutlinedButton.styleFrom(
                          minimumSize: const Size(48, 44)),
                      child: Text(Textos.cancelarRecordatorio,
                          style: const TextStyle(fontSize: 16)),
                    ),
                  ),
                ],
              )
            : Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  SizedBox(
                    width: movil ? 60 : 80,
                    child: Text(recordatorio.hora,
                        style: TextStyle(
                            fontSize: movil ? 18 : 24,
                            fontWeight: FontWeight.w700,
                            fontFamily: Tema.familia)),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(recordatorio.texto,
                            style: const TextStyle(
                                fontSize: 18,
                                fontWeight: FontWeight.w700,
                                fontFamily: Tema.familia)),
                        const SizedBox(height: 4),
                        Text(_repeticion(),
                            style: const TextStyle(
                                fontSize: 15,
                                color: Tema.secundario,
                                fontFamily: Tema.familia)),
                        if (ultima != null) ...<Widget>[
                          const SizedBox(height: 4),
                          ultima,
                        ],
                      ],
                    ),
                  ),
                  OutlinedButton(
                    onPressed: estado.cargandoRecordatorio
                        ? null
                        : () => _confirmarCancelar(contexto),
                    style: OutlinedButton.styleFrom(
                        minimumSize: const Size(48, 44)),
                    child: Text(Textos.cancelar,
                        style: const TextStyle(
                            color: Tema.peligro, fontSize: 16)),
                  ),
                ],
              ),
      ),
    );
  }

  String _repeticion() {
    if (recordatorio.deUnaSolaVez) {
      return '${Textos.unaSolaVez} · ${_fechaCorta(recordatorio.fecha)}';
    }
    if (recordatorio.repeticion == 'diario') return Textos.todosLosDias;
    return diasDe(recordatorio.dias);
  }

  String _repeticionCorta() {
    if (recordatorio.repeticion == 'diario') return Textos.todosLosDias;
    if (recordatorio.deUnaSolaVez) return Textos.unaSolaVez;
    return diasDe(recordatorio.dias);
  }

  Widget? _lineaUltima(TextTheme tipografia) {
    final EntregaRecordatorio? entrega = recordatorio.ultimaEntrega;
    if (entrega == null) return null;
    final String texto;
    if (entrega.seDijo) {
      final int? cuando = entrega.cuando;
      texto = cuando == null
          ? Textos.noSePudoDecir
          : ultimaVezSeDijo(cuandoSeDijo(cuando));
    } else {
      texto = Textos.noSePudoDecir;
    }
    return Text(texto,
        style: const TextStyle(
            fontSize: 15, color: Tema.ambarPildora, fontFamily: Tema.familia));
  }

  Future<void> _confirmarCancelar(BuildContext contexto) async {
    final bool? confirmado = await showDialog<bool>(
      context: contexto,
      builder: (BuildContext c) => AlertDialog(
        title: const Text(Textos.cancelarRecordatorio),
        content: Text(recordatorio.texto),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text(Textos.cancelar),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text(Textos.cancelarRecordatorio),
          ),
        ],
      ),
    );
    if (confirmado == true) {
      await estado.cancelarRecordatorio(recordatorio.id);
    }
  }
}

String _fechaCorta(String? fecha) {
  if (fecha == null || fecha.isEmpty) return '';
  final List<String> partes = fecha.split('-');
  if (partes.length != 3) return fecha;
  final String dia = partes[2].padLeft(2, '0');
  final String mes = partes[1].padLeft(2, '0');
  return '$dia/$mes/${partes[0]}';
}

/// El formulario de un recordatorio nuevo, en un diálogo.
///
/// La hora y la fecha se eligen con desplegables propios y no con
/// `showTimePicker`/`showDatePicker`: el panel no tiene localizaciones cargadas y
/// esos dos salen en inglés. Con desplegables, todo queda en español y sin añadir
/// una dependencia de idioma por un solo formulario.
class _DialogoNuevoRecordatorio extends StatefulWidget {
  const _DialogoNuevoRecordatorio({required this.estado});

  final EstadoPanel estado;

  @override
  State<_DialogoNuevoRecordatorio> createState() =>
      _DialogoNuevoRecordatorioState();
}

class _DialogoNuevoRecordatorioState extends State<_DialogoNuevoRecordatorio> {
  final TextEditingController _texto = TextEditingController();
  int _hora = 8;
  int _minuto = 0;
  String _repeticion = 'diario';
  int _dia = 1;
  int _mes = 1;
  int _anio = DateTime.now().year;
  final Set<int> _dias = <int>{};

  @override
  void initState() {
    super.initState();
    final DateTime hoy = DateTime.now();
    _dia = hoy.day;
    _mes = hoy.month;
    _anio = hoy.year;
  }

  @override
  void dispose() {
    _texto.dispose();
    super.dispose();
  }

  String get _horaTexto =>
      '${_hora.toString().padLeft(2, '0')}:${_minuto.toString().padLeft(2, '0')}';

  String get _fechaTexto =>
      '${_anio.toString().padLeft(4, '0')}-${_mes.toString().padLeft(2, '0')}'
      '-${_dia.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext contexto) {
    return ListenableBuilder(
      listenable: widget.estado,
      builder: (BuildContext contexto, Widget? _) {
        return AlertDialog(
          title: const Text(Textos.nuevoRecordatorio),
          content: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Text(Textos.nuevoRecordatorioSubtitulo,
                      style: const TextStyle(
                          fontSize: 16, color: Tema.secundario,
                          fontFamily: Tema.familia)),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _texto,
                    maxLength: 120,
                    minLines: 2,
                    maxLines: 2,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(
                      labelText: Textos.queTieneQueDecir,
                      hintText: Textos.pistaTextoRecordatorio,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(Textos.aQueHora,
                      style: Theme.of(contexto).textTheme.labelLarge),
                  const SizedBox(height: 6),
                  Row(
                    children: <Widget>[
                      Expanded(
                        child: _Selector(
                          etiqueta: Textos.elegirHora,
                          valor: _hora,
                          opciones: <int>[
                            for (int h = 0; h < 24; h++) h,
                          ],
                          rotulo: (int v) => v.toString().padLeft(2, '0'),
                          alCambiar: (int v) => setState(() => _hora = v),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: _Selector(
                          etiqueta: ' ',
                          valor: _minuto,
                          opciones: <int>[for (int m = 0; m < 60; m += 5) m],
                          rotulo: (int v) => v.toString().padLeft(2, '0'),
                          alCambiar: (int v) => setState(() => _minuto = v),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 16),
                  Text(Textos.cuando,
                      style: Theme.of(contexto).textTheme.labelLarge),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: <Widget>[
                      _ChipRepeticion(
                        texto: Textos.unaSolaVez,
                        elegido: _repeticion == 'una_vez',
                        alPulsar: () => setState(() => _repeticion = 'una_vez'),
                      ),
                      _ChipRepeticion(
                        texto: Textos.todosLosDias,
                        elegido: _repeticion == 'diario',
                        alPulsar: () => setState(() => _repeticion = 'diario'),
                      ),
                      _ChipRepeticion(
                        texto: Textos.ciertosDias,
                        elegido: _repeticion == 'semanal',
                        alPulsar: () => setState(() => _repeticion = 'semanal'),
                      ),
                    ],
                  ),
                  if (_repeticion == 'una_vez') ...<Widget>[
                    const SizedBox(height: 16),
                    Text(Textos.queDia,
                        style: Theme.of(contexto).textTheme.labelLarge),
                    const SizedBox(height: 6),
                    Row(
                      children: <Widget>[
                        Expanded(
                          flex: 2,
                          child: _Selector(
                            etiqueta: Textos.elegirDia,
                            valor: _dia,
                            opciones: <int>[for (int d = 1; d <= 31; d++) d],
                            rotulo: (int v) => v.toString().padLeft(2, '0'),
                            alCambiar: (int v) => setState(() => _dia = v),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          flex: 3,
                          child: _Selector(
                            etiqueta: ' ',
                            valor: _mes,
                            opciones: <int>[for (int m = 1; m <= 12; m++) m],
                            rotulo: (int v) => Textos.meses[v - 1],
                            alCambiar: (int v) => setState(() => _mes = v),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          flex: 2,
                          child: _Selector(
                            etiqueta: ' ',
                            valor: _anio,
                            opciones: <int>[
                              for (int a = DateTime.now().year;
                                  a <= DateTime.now().year + 3;
                                  a++)
                                a,
                            ],
                            rotulo: (int v) => '$v',
                            alCambiar: (int v) => setState(() => _anio = v),
                          ),
                        ),
                      ],
                    ),
                  ],
                  if (_repeticion == 'semanal') ...<Widget>[
                    const SizedBox(height: 16),
                    Text(Textos.queDia,
                        style: Theme.of(contexto).textTheme.labelLarge),
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: <Widget>[
                        for (int d = 1; d <= 7; d++)
                          FilterChip(
                            label: Text(Textos.diasSemana[d - 1]),
                            selected: _dias.contains(d),
                            onSelected: (bool sel) => setState(() {
                              if (sel) {
                                _dias.add(d);
                              } else {
                                _dias.remove(d);
                              }
                            }),
                          ),
                        TextButton(
                          onPressed: () => setState(
                              () => _dias..clear()..addAll(<int>[1, 2, 3, 4, 5])),
                          child: const Text(Textos.lunesAViernes),
                        ),
                      ],
                    ),
                  ],
                  if (widget.estado.aviso != null) ...<Widget>[
                    const SizedBox(height: 16),
                    Text(widget.estado.aviso!,
                        style: const TextStyle(color: Tema.peligro, fontSize: 15)),
                  ],
                ],
              ),
            ),
          ),
          actions: <Widget>[
            TextButton(
              onPressed: () => Navigator.pop(contexto),
              child: const Text(Textos.cancelar),
            ),
            FilledButton(
              onPressed: widget.estado.cargandoRecordatorio ? null : _crear,
              child: const Text(Textos.crearRecordatorio),
            ),
          ],
        );
      },
    );
  }

  Future<void> _crear() async {
    final bool creado = await widget.estado.crearRecordatorio(
      texto: _texto.text,
      hora: _horaTexto,
      repeticion: _repeticion,
      fecha: _repeticion == 'una_vez' ? _fechaTexto : null,
      dias: _repeticion == 'semanal' ? _dias.toList() : null,
    );
    if (creado && mounted) {
      Navigator.pop(context);
    }
  }
}

class _ChipRepeticion extends StatelessWidget {
  const _ChipRepeticion({
    required this.texto,
    required this.elegido,
    required this.alPulsar,
  });

  final String texto;
  final bool elegido;
  final VoidCallback alPulsar;

  @override
  Widget build(BuildContext contexto) {
    return ChoiceChip(
      label: Text(texto),
      selected: elegido,
      onSelected: (_) => alPulsar(),
    );
  }
}

class _Selector extends StatelessWidget {
  const _Selector({
    required this.etiqueta,
    required this.valor,
    required this.opciones,
    required this.rotulo,
    required this.alCambiar,
  });

  final String etiqueta;
  final int valor;
  final List<int> opciones;
  final String Function(int) rotulo;
  final ValueChanged<int> alCambiar;

  @override
  Widget build(BuildContext contexto) {
    return InputDecorator(
      decoration: InputDecoration(
        labelText: etiqueta,
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<int>(
          isExpanded: true,
          value: valor,
          items: <DropdownMenuItem<int>>[
            for (final int opcion in opciones)
              DropdownMenuItem<int>(
                value: opcion,
                child: Text(rotulo(opcion)),
              ),
          ],
          onChanged: (int? v) {
            if (v != null) alCambiar(v);
          },
        ),
      ),
    );
  }
}
