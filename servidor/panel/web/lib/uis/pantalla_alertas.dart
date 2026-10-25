import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';
import 'navegacion.dart';
import 'tarjetas.dart';

class PantallaAlertas extends StatelessWidget {
  const PantallaAlertas({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;

    return EstructuraPanel(
      contenido: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(Textos.alertas,
                    style: TextStyle(
                        fontSize: movil ? 24 : 28,
                        fontWeight: FontWeight.w700,
                        color: Tema.texto,
                        fontFamily: Tema.familia)),
              ),
              if (estado.cargandoAlerta)
                const SizedBox(
                    height: 20,
                    width: 20,
                    child: CircularProgressIndicator(strokeWidth: 2)),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            Textos.alertasSubtitulo,
            style: TextStyle(
                fontSize: movil ? 15 : 17,
                color: Tema.secundario,
                fontFamily: Tema.familia),
          ),
          const SizedBox(height: 20),
          if (estado.alertas.isEmpty)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  Textos.sinAlertas,
                  style: Theme.of(contexto).textTheme.bodyMedium,
                ),
              ),
            )
          else
            Card(
              child: Padding(
                padding: EdgeInsets.symmetric(
                    horizontal: movil ? 16 : 24, vertical: 4),
                child: Column(
                  children: <Widget>[
                    for (int i = 0; i < estado.alertas.length; i++)
                      _FilaHistorial(
                        alerta: estado.alertas[i],
                        estado: estado,
                        ultimo: i == estado.alertas.length - 1,
                        movil: movil,
                      ),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 8),
          Text(
            Textos.alertasBorradoAuto,
            style: TextStyle(
                fontSize: movil ? 14 : 15,
                color: Tema.secundario,
                fontFamily: Tema.familia),
          ),
          const SizedBox(height: 8),
          const PiePanel(),
        ],
      ),
    );
  }
}

class _FilaHistorial extends StatelessWidget {
  const _FilaHistorial({
    required this.alerta,
    required this.estado,
    required this.ultimo,
    required this.movil,
  });

  final Alerta alerta;
  final EstadoPanel estado;
  final bool ultimo;
  final bool movil;

  @override
  Widget build(BuildContext contexto) {
    final double icono = movil ? 38 : 44;
    final double titulo = movil ? 16 : 18;
    final double sub = movil ? 14 : 15;
    final double pillFont = movil ? 13 : 14;
    final IconData iconoD = _iconoAlerta(alerta.tipo);
    final Color iconoColor = (alerta.sinResolver && alerta.tipo == 'gas')
        ? Tema.peligro
        : Tema.primario;
    final Color iconoFondo = (alerta.sinResolver && alerta.tipo == 'gas')
        ? Tema.peligro.withValues(alpha: 0.12)
        : Tema.suave;

    String subtitulo = fechaAlerta(alerta.creadoEn);
    final String? origen = _origenFila(alerta);
    if (origen != null && origen.isNotEmpty) {
      subtitulo = '$origen · $subtitulo';
    }

    return InkWell(
      onTap: () => estado.abrirAlerta(alerta.id),
      child: Container(
        padding: EdgeInsets.symmetric(vertical: movil ? 14 : 16),
        decoration: BoxDecoration(
          border: ultimo
              ? null
              : const Border(bottom: BorderSide(color: Tema.caja)),
        ),
        child: Row(
          children: <Widget>[
            Container(
              width: icono,
              height: icono,
              decoration: BoxDecoration(
                  color: iconoFondo, borderRadius: BorderRadius.circular(12)),
              child: Icon(iconoD, color: iconoColor, size: movil ? 20 : 22),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(tituloAlerta(alerta.tipo),
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          fontSize: titulo,
                          fontWeight: FontWeight.w700,
                          fontFamily: Tema.familia)),
                  Text(subtitulo,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          fontSize: sub,
                          color: Tema.secundario,
                          fontFamily: Tema.familia)),
                ],
              ),
            ),
            if (!movil) ...<Widget>[
              SizedBox(
                width: 160,
                child: Text(fechaAlerta(alerta.creadoEn),
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                        fontSize: 15,
                        color: Tema.secundario,
                        fontFamily: Tema.familia)),
              ),
            ],
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: (alerta.estado == 'pendiente' || alerta.estado == 'enviada')
                    ? Tema.peligro
                    : (alerta.estado == 'descartada')
                        ? Tema.caja
                        : Tema.suave,
                borderRadius: BorderRadius.circular(999),
              ),
              child: Text(
                pastillaEstadoTexto(alerta.estado),
                style: TextStyle(
                    color: (alerta.estado == 'pendiente' ||
                            alerta.estado == 'enviada')
                        ? Tema.tarjeta
                        : Tema.primario,
                    fontSize: pillFont,
                    fontWeight: FontWeight.w700),
              ),
            ),
            if (!movil) ...<Widget>[
              const SizedBox(width: 8),
              const Icon(Icons.chevron_right, color: Tema.secundario, size: 20),
            ],
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

String pastillaEstadoTexto(String estado) {
  if (estado == 'resuelta' || estado == 'atendida') return Textos.atendida;
  if (estado == 'pendiente' || estado == 'enviada') return Textos.pendiente;
  if (estado == 'descartada') return Textos.descartada;
  return estado;
}
