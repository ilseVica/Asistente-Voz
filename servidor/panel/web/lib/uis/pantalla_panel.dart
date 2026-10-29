import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../presentacion_alerta.dart';
import '../textos.dart';
import 'navegacion.dart';
import 'banner_sin_conexion.dart';
import 'pantalla_recordatorios.dart';
import 'tarjetas.dart';

/// Inicio: las tarjetas, dentro del molde de navegación (`EstructuraPanel`).
class PantallaPanel extends StatelessWidget {
  const PantallaPanel({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final double ancho = MediaQuery.sizeOf(contexto).width;
    final bool escritorio = ancho >= Tema.anchoEscritorio;
    final bool tableta = ancho >= Tema.anchoTableta && ancho < Tema.anchoEscritorio;

    final Widget luz = estado.estado?.luz.estado == noInstalado
        ? const Card(
            child: Padding(
              padding: EdgeInsets.all(20),
              child: LineaLuzNoInstalada(),
            ),
          )
        : const TarjetaLuz();

    final Widget cuerpo;
    if (escritorio) {
      cuerpo = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Expanded(child: luz),
                const SizedBox(width: 20),
                const Expanded(child: TarjetaAmbiente()),
                const SizedBox(width: 20),
                const Expanded(child: TarjetaGas()),
                const SizedBox(width: 20),
                const Expanded(child: TarjetaMovimiento()),
              ],
            ),
          ),
          const SizedBox(height: 20),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Expanded(child: _AlertasRecientes()),
              const SizedBox(width: 20),
              const Expanded(child: _RecordatoriosProximos()),
              const SizedBox(width: 20),
              const Expanded(child: _TarjetaMensajes()),
            ],
          ),
          const SizedBox(height: 20),
          const TarjetaEquipos(),
          const SizedBox(height: 8),
          const PiePanel(),
        ],
      );
    } else if (tableta) {
      cuerpo = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(child: luz),
              const SizedBox(width: 16),
              const Expanded(child: TarjetaAmbiente()),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Expanded(child: TarjetaGas()),
              const SizedBox(width: 16),
              const Expanded(child: TarjetaMovimiento()),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Expanded(child: _AlertasRecientes()),
              const SizedBox(width: 16),
              const Expanded(child: _RecordatoriosProximos()),
            ],
          ),
          const SizedBox(height: 16),
          const _TarjetaMensajes(),
          const SizedBox(height: 16),
          const TarjetaEquipos(),
          const SizedBox(height: 8),
          const PiePanel(),
        ],
      );
    } else {
      cuerpo = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          luz,
          const SizedBox(height: 16),
          const _RejillaMovil(),
          const SizedBox(height: 16),
          const _ProximoRecordatorioCompacto(),
          const SizedBox(height: 16),
          const _UltimaAlertaCompacta(),
          const SizedBox(height: 16),
          const _TarjetaMensajes(),
          const SizedBox(height: 16),
          const TarjetaEquipos(),
          const SizedBox(height: 8),
          const PiePanel(),
        ],
      );
    }

    return EstructuraPanel(contenido: cuerpo);
  }
}

/// Próximo recordatorio compacto del móvil.
class _ProximoRecordatorioCompacto extends StatelessWidget {
  const _ProximoRecordatorioCompacto();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final List<Recordatorio> proximos = estado.recordatorios;
    final Recordatorio? r = proximos.isEmpty ? null : proximos.first;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: r == null
            ? Text(Textos.recordatoriosVacio,
                style: Theme.of(contexto).textTheme.bodySmall)
            : Row(
                children: <Widget>[
                  SizedBox(
                    width: 56,
                    child: Text(r.hora,
                        style: const TextStyle(
                            fontSize: 19, fontWeight: FontWeight.w700,
                            fontFamily: Tema.familia)),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(Textos.proximoRecordatorio,
                            style: const TextStyle(
                                fontSize: 14, color: Tema.secundario,
                                fontFamily: Tema.familia)),
                        Text(r.texto,
                            style: const TextStyle(
                                fontSize: 17, fontWeight: FontWeight.w700,
                                fontFamily: Tema.familia)),
                      ],
                    ),
                  ),
                ],
              ),
      ),
    );
  }
}

/// Última alerta compacta del móvil.
class _UltimaAlertaCompacta extends StatelessWidget {
  const _UltimaAlertaCompacta();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final List<Alerta> recientes = estado.alertas;
    final Alerta? a = recientes.isEmpty ? null : recientes.first;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(Textos.ultimaAlerta,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 18, fontWeight: FontWeight.w700,
                          fontFamily: Tema.familia)),
                ),
                TextButton(
                  onPressed: () => estado.irA(Destino.alertas),
                  style: TextButton.styleFrom(
                      foregroundColor: Tema.primario, padding: EdgeInsets.zero),
                  child: Text(Textos.verTodas,
                      style: const TextStyle(fontSize: 15)),
                ),
              ],
            ),
            if (a != null)
              InkWell(
                onTap: () => estado.abrirAlerta(a.id),
                child: Row(
                  children: <Widget>[
                    Container(
                      width: 38,
                      height: 38,
                      decoration: BoxDecoration(
                          color: Tema.suave,
                          borderRadius: BorderRadius.circular(12)),
                      child: const Icon(Icons.notifications,
                          color: Tema.primario, size: 20),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(tituloAlerta(a.tipo),
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                  fontSize: 16, fontWeight: FontWeight.w700,
                                  fontFamily: Tema.familia)),
                          Text(fechaAlerta(a.creadoEn),
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                  fontSize: 14, color: Tema.secundario,
                                  fontFamily: Tema.familia)),
                        ],
                      ),
                    ),
                    PastillaEstado(estado: a.estado),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// La rejilla de 2×2 del móvil: temperatura, humedad, gas y movimiento.
class _RejillaMovil extends StatelessWidget {
  const _RejillaMovil();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;
    final Sensores s = e?.sensores ??
        const Sensores(
            estado: sinDatos,
            temperaturaC: null,
            humedadPct: null,
            movimiento: null,
            gasAdc: null,
            recibidoEn: null);
    final bool nodoConectado = e?.nodo.estado == conectado;
    final int ahora = estado.ahoraServidor();
    final String antiguedad = hace(s.recibidoEn, ahora: ahora);

    final num? umbral = e?.gasOnAdc;
    String gas;
    if (!nodoConectado) {
      gas = Textos.sinDatos;
    } else if (s.gasAdc == null) {
      gas = Textos.sinSensor;
    } else {
      gas = estadoDelGas(gasAdc: s.gasAdc, umbral: umbral);
    }

    String movimiento;
    if (!nodoConectado) {
      movimiento = Textos.sinDatos;
    } else if (s.movimiento == null) {
      movimiento = Textos.sinSensor;
    } else if (s.movimiento!) {
      movimiento = Textos.movimientoDetectado;
    } else {
      movimiento = Textos.sinMovimiento;
    }
    final Inactividad? inact = e?.inactividad;
    final bool sinReloj = inact == null ||
        inact.ultimoMovimiento == null ||
        !nodoConectado ||
        e?.nodo.estado == desconectado;
    final String ultimo = sinReloj
        ? Textos.sinDatosDelSensor
        : hace(inact.ultimoMovimiento!, ahora: ahora);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(
              child: _Celda(
                tamanoValor: 26,
                rotulo: Textos.temperatura,
                valor: datoDeSensor(
                    nodoConectado: nodoConectado,
                    valor: s.temperaturaC,
                    conValor: () => '${_num(s.temperaturaC)} °C'),
                subtitulo: s.recibidoEn == null ? null : antiguedad,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _Celda(
                tamanoValor: 26,
                rotulo: Textos.humedad,
                valor: datoDeSensor(
                    nodoConectado: nodoConectado,
                    valor: s.humedadPct,
                    conValor: () => '${_num(s.humedadPct)} %'),
                subtitulo: s.recibidoEn == null ? null : antiguedad,
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Expanded(
              child: _Celda(
                tamanoValor: 26,
                rotulo: Textos.gas,
                valor: gas,
                color: nodoConectado && s.gasAdc != null
                    ? colorDelGas(gasAdc: s.gasAdc, umbral: umbral)
                    : Tema.secundario,
                subtitulo: (nodoConectado && s.gasAdc != null)
                    ? (umbral == null
                        ? Textos.gasSinUmbral
                        : '${Textos.lectura}${_num(s.gasAdc)} · ${Textos.umbral}${_num(umbral)}')
                    : null,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _Celda(
                tamanoValor: 19,
                rotulo: Textos.movimiento,
                valor: movimiento,
                subtitulo: (!nodoConectado || sinReloj)
                    ? null
                    : '${Textos.ultimoMovimiento} $ultimo',
                pie: inact == null ? null : avisoInactividadAlos(inact.tiempoMin),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Una celda de la rejilla: rótulo, cifra grande y, si la hay, su letra chica.
class _Celda extends StatelessWidget {
  const _Celda({
    required this.rotulo,
    required this.valor,
    this.color,
    this.subtitulo,
    this.pie,
    this.tamanoValor = 20,
  });

  final String rotulo;
  final String valor;
  final Color? color;
  final String? subtitulo;
  final String? pie;
  final double tamanoValor;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Tema.tarjeta,
        borderRadius: BorderRadius.circular(Tema.radio),
        border: Border.all(color: Tema.borde),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(rotulo, style: Theme.of(contexto).textTheme.bodySmall),
          const SizedBox(height: 4),
          Text(valor,
              style: TextStyle(
                  fontSize: tamanoValor,
                  fontWeight: FontWeight.w700,
                  height: 1.15,
                  color: color ?? Tema.texto,
                  fontFamily: Tema.familia)),
          if (subtitulo != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(subtitulo!, style: Theme.of(contexto).textTheme.bodySmall),
          ],
          if (pie != null) ...<Widget>[
            const SizedBox(height: 6),
            Text(pie!,
                style: Theme.of(contexto)
                    .textTheme
                    .bodySmall
                    ?.copyWith(fontWeight: FontWeight.w700)),
            TextButton(
              onPressed: () => mostrarDialogoInactividad(contexto),
              style: TextButton.styleFrom(
                  foregroundColor: Tema.primario,
                  padding: EdgeInsets.zero,
                  minimumSize: const Size(0, 32),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap),
              child: Text(Textos.cambiar,
                  style: Theme.of(contexto)
                      .textTheme
                      .bodySmall
                      ?.copyWith(
                          color: Tema.primario,
                          fontWeight: FontWeight.w700,
                          decoration: TextDecoration.underline)),
            ),
          ],
        ],
      ),
    );
  }
}

/// Las alertas más recientes: cinco, con su estado y su hora, y cada fila
/// abriendo el detalle.
class _AlertasRecientes extends StatelessWidget {
  const _AlertasRecientes();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final List<Alerta> recientes =
        estado.alertas.length > 5 ? estado.alertas.sublist(0, 5) : estado.alertas;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(Textos.alertasRecientes,
                      style: const TextStyle(
                          fontSize: 21, fontWeight: FontWeight.w700,
                          color: Tema.texto, fontFamily: Tema.familia)),
                ),
                TextButton(
                  onPressed: () => estado.irA(Destino.alertas),
                  style: TextButton.styleFrom(
                      foregroundColor: Tema.primario, padding: EdgeInsets.zero),
                  child: const Text(Textos.verTodas),
                ),
              ],
            ),
            const SizedBox(height: 4),
            if (recientes.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  Textos.sinAlertas,
                  style: Theme.of(contexto).textTheme.bodySmall,
                ),
              )
            else
              for (final Alerta a in recientes)
                _FilaAlerta(alerta: a, estado: estado),
          ],
        ),
      ),
    );
  }
}

class _FilaAlerta extends StatelessWidget {
  const _FilaAlerta({required this.alerta, required this.estado});

  final Alerta alerta;
  final EstadoPanel estado;

  @override
  Widget build(BuildContext contexto) {
    final PresentacionAlerta pres = presentacionAlerta(
        alerta.tipo,
        origen: alerta.carga['origen']?.toString(),
        carga: alerta.carga);
    final IconData icono = pres.icono;
    final Color iconoColor =
        alerta.sinResolver ? pres.colorTexto : Tema.primario;
    final Color iconoFondo =
        alerta.sinResolver ? pres.colorFondo : Tema.suave;

    String subtitulo = fechaAlerta(alerta.creadoEn);
    final String? origen = _origenFila(alerta);
    if (origen != null && origen.isNotEmpty) {
      subtitulo = '$origen · $subtitulo';
    }

    return InkWell(
      borderRadius: BorderRadius.circular(10),
      onTap: () => estado.abrirAlerta(alerta.id),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
        child: Row(
          children: <Widget>[
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                  color: iconoFondo, borderRadius: BorderRadius.circular(12)),
              child: Icon(icono, color: iconoColor, size: 22),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(tituloAlerta(alerta.tipo),
                      style: Theme.of(contexto)
                          .textTheme
                          .bodyMedium
                          ?.copyWith(fontWeight: FontWeight.w700)),
                  Text(subtitulo, style: Theme.of(contexto).textTheme.bodySmall),
                ],
              ),
            ),
            PastillaEstado(estado: alerta.estado),
          ],
        ),
      ),
    );
  }

  IconData _iconoAlerta(String tipo) {
    switch (tipo) {
      case 'gas':
        return Icons.local_gas_station;
      case 'inactividad':
        return Icons.hourglass_empty;
      case 'miedo':
        return Icons.favorite_border;
      case 'ayuda':
      default:
        return Icons.warning_amber_rounded;
    }
  }

  String? _origenFila(Alerta alerta) {
    final Map<String, dynamic> c = alerta.carga;
    if (alerta.tipo == 'inactividad') {
      final Object? m = c['minutos'];
      if (m is num) return '${m.toInt()} min sin movimiento';
    }
    final Object? origen = c['origen'];
    if (origen == null) return null;
    final String o = origen.toString();
    if (o == 'voz') return 'Por voz';
    if (o == 'alexa') return 'Por voz con Alexa';
    if (o == 'boton') return 'Con el botón';
    if (o == 'panel') return 'Desde el panel';
    return null;
  }
}

/// Los próximos recordatorios: tres, con su texto y su hora.
class _RecordatoriosProximos extends StatelessWidget {
  const _RecordatoriosProximos();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final List<Recordatorio> proximos = estado.recordatorios.length > 3
        ? estado.recordatorios.sublist(0, 3)
        : estado.recordatorios;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(Textos.recordatorios,
                      style: const TextStyle(
                          fontSize: 21, fontWeight: FontWeight.w700,
                          color: Tema.texto, fontFamily: Tema.familia)),
                ),
                OutlinedButton(
                  onPressed: () => abrirNuevoRecordatorio(contexto, estado),
                  style: OutlinedButton.styleFrom(
                      minimumSize: const Size(48, 44), padding: const EdgeInsets.symmetric(horizontal: 14)),
                  child: const Text(Textos.nuevo),
                ),
              ],
            ),
            const SizedBox(height: 4),
            if (proximos.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  Textos.recordatoriosVacio,
                  style: Theme.of(contexto).textTheme.bodySmall,
                ),
              )
            else
              for (final Recordatorio r in proximos)
                InkWell(
                  borderRadius: BorderRadius.circular(10),
                  onTap: () => estado.irA(Destino.recordatorios),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
                    child: Row(
                      children: <Widget>[
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text(r.texto,
                                  style: Theme.of(contexto)
                                      .textTheme
                                      .bodyMedium
                                      ?.copyWith(fontWeight: FontWeight.w700)),
                              Text(Textos.proximaVez + fechaProxima(r.proximoEn),
                                  style:
                                      Theme.of(contexto).textTheme.bodySmall),
                            ],
                          ),
                        ),
                        if (!r.activo)
                          const Text(Textos.pausado,
                              style: TextStyle(
                                  color: Tema.secundario, fontSize: 13)),
                      ],
                    ),
                  ),
                ),
          ],
        ),
      ),
    );
  }
}

/// La tercera tarjeta de la fila: mensajes, que todavía no existe.
///
/// No hay datos de mensajes en la casa y aquí no se inventan: se dice que viene,
/// con el pastón «Próximamente» del prototipo, y nada más.
class _TarjetaMensajes extends StatefulWidget {
  const _TarjetaMensajes();

  @override
  State<_TarjetaMensajes> createState() => _TarjetaMensajesState();
}

class _TarjetaMensajesState extends State<_TarjetaMensajes> {
  final TextEditingController _controlador = TextEditingController();

  @override
  void dispose() {
    _controlador.dispose();
    super.dispose();
  }

  Future<void> _enviar() async {
    final EstadoPanel estado = PanelScope.de(context);
    final String texto = _controlador.text;
    if (texto.trim().isEmpty) return;
    if (estado.enviandoMensaje) return;
    final bool ok = await estado.enviarMensaje(texto);
    if (ok && mounted) {
      _controlador.clear();
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final double ancho = MediaQuery.sizeOf(contexto).width;
    final bool compacto = ancho < Tema.anchoTableta;
    final List<EntradaConversacion> ultimos = estado.conversacion.length <= 3
        ? estado.conversacion
        : estado.conversacion.sublist(estado.conversacion.length - 3);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Icon(Icons.chat_bubble_outline,
                    color: Tema.secundario, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(Textos.mensajeParaLaSala,
                      style: TextStyle(
                          fontSize: ancho >= Tema.anchoTableta ? 21 : 16,
                          fontWeight: FontWeight.w700,
                          color: Tema.secundario,
                          fontFamily: Tema.familia)),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (ultimos.isEmpty)
              Text(Textos.mensajesVacio,
                  style: Theme.of(contexto).textTheme.bodySmall)
            else
              ...ultimos.map((EntradaConversacion e) => _BurbujaCompacta(entrada: e)),
            const SizedBox(height: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                TextField(
                  controller: _controlador,
                  maxLength: 200,
                  onChanged: (_) => setState(() {}),
                  onSubmitted: (_) => _enviar(),
                  decoration: InputDecoration(
                    hintText: Textos.chatPlaceholder,
                    counterText: '',
                    border: const OutlineInputBorder(),
                    isDense: true,
                  ),
                ),
                const SizedBox(height: 8),
                FilledButton(
                  onPressed: (_controlador.text.trim().isEmpty ||
                          estado.enviandoMensaje)
                      ? null
                      : _enviar,
                  child: Text(compacto
                      ? Textos.enviar
                      : Textos.enviarYDecirEnVozAlta),
                ),
              ],
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: () => estado.irA(Destino.mensajes),
              child: Text(Textos.verLaConversacion),
            ),
          ],
        ),
      ),
    );
  }
}

String _num(num? valor) {
  if (valor == null) return '—';
  final double d = valor.toDouble();
  if (d == d.roundToDouble()) return d.toStringAsFixed(0);
  return d.toStringAsFixed(1);
}

class _BurbujaCompacta extends StatelessWidget {
  const _BurbujaCompacta({required this.entrada});
  final EntradaConversacion entrada;

  @override
  Widget build(BuildContext context) {
    if (entrada.esAlerta) {
      final PresentacionAlerta pres = presentacionAlerta(
        entrada.alerta?.tipo ?? 'aviso',
        origen: entrada.alerta?.carga['origen']?.toString(),
        carga: entrada.alerta?.carga,
      );
      return Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: pres.colorFondo,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Row(
            children: <Widget>[
              Icon(pres.icono, size: 14, color: pres.colorTexto),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  pres.titulo +
                      ' · ' +
                      (entrada.alerta != null && entrada.alerta!.sinResolver
                          ? Textos.pendiente
                          : Textos.atendida),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: pres.colorTexto, fontSize: 13),
                ),
              ),
            ],
          ),
        ),
      );
    }
    if (entrada.tipo == 'aviso') {
      final String texto = (entrada.texto ?? Textos.sinTexto).trim();
      return Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: Tema.caja,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(
            texto,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 13, color: Tema.secundario),
          ),
        ),
      );
    }
    final bool esRespuesta = entrada.esRespuesta;
    final Color fondo = esRespuesta ? Tema.suave : Tema.caja;
    final String autor = esRespuesta
        ? 'Sala'
        : (entrada.autor ?? Textos.sinAutor).trim();
    final String texto = (entrada.texto ?? Textos.sinTexto).trim();
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: fondo,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(
                  esRespuesta ? Icons.volume_up : Icons.person_outline,
                  size: 14,
                  color: Tema.secundario,
                ),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(
                    '$autor',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 12, fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
            Text(
              texto,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 13),
            ),
          ],
        ),
      ),
    );
  }
}

class PastillaEstado extends StatelessWidget {
  const PastillaEstado({super.key, required this.estado});

  final String estado;

  @override
  Widget build(BuildContext contexto) {
    final Color color;
    final String texto;

    if (estado == 'resuelta' || estado == 'atendida') {
      color = Tema.primario;
      texto = Textos.atendida;
    } else if (estado == 'pendiente' || estado == 'enviada') {
      color = Tema.peligro;
      texto = Textos.pendiente;
    } else if (estado == 'descartada') {
      color = Tema.secundario;
      texto = Textos.descartada;
    } else {
      color = Tema.secundario;
      texto = estado;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(999)),
      child: Text(texto,
          style: TextStyle(
              color: color, fontWeight: FontWeight.w700, fontSize: 14)),
    );
  }
}
