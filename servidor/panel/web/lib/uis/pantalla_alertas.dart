import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';
import 'navegacion.dart';
import 'pantalla_panel.dart' show PastillaEstado;
import 'tarjetas.dart';

/// El historial de alertas y «Ya lo atendí».
///
/// Toda la navegación (cabecera, barra, carteles) viene del molde
/// `EstructuraPanel`: aquí sólo está la lista. Cada fila abre el detalle de esa
/// alerta («Qué pasó»), y el botón de atender sigue estando en la propia lista
/// para poder contestar sin entrar.
class PantallaAlertas extends StatelessWidget {
  const PantallaAlertas({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);

    return EstructuraPanel(
      contenido: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(Textos.alertas,
                    style: Theme.of(contexto).textTheme.headlineMedium),
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
            style: Theme.of(contexto).textTheme.bodySmall,
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
            for (final Alerta a in estado.alertas)
              _TarjetaAlerta(alerta: a, estado: estado),
          const SizedBox(height: 8),
          const PiePanel(),
        ],
      ),
    );
  }
}

class _TarjetaAlerta extends StatelessWidget {
  const _TarjetaAlerta({required this.alerta, required this.estado});

  final Alerta alerta;
  final EstadoPanel estado;

  @override
  Widget build(BuildContext contexto) {
    final bool sePuedeAtender =
        alerta.sinResolver && !estado.cargandoAlerta;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        borderRadius: BorderRadius.circular(Tema.radio + 2),
        onTap: () => estado.abrirAlerta(alerta.id),
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(tituloAlerta(alerta.tipo),
                            style: Theme.of(contexto).textTheme.titleMedium),
                        const SizedBox(height: 4),
                        Text(fechaAlerta(alerta.creadoEn),
                            style: Theme.of(contexto).textTheme.bodySmall),
                      ],
                    ),
                  ),
                  PastillaEstado(estado: alerta.estado),
                ],
              ),
              if (quePasoDe(alerta.tipo, alerta.carga) != null) ...<Widget>[
                const SizedBox(height: 10),
                Text(quePasoDe(alerta.tipo, alerta.carga)!,
                    style: Theme.of(contexto).textTheme.bodyMedium),
              ],
              if (sePuedeAtender) ...<Widget>[
                const SizedBox(height: 14),
                OutlinedButton(
                  onPressed: () => estado.atenderAlerta(alerta.id),
                  style: OutlinedButton.styleFrom(
                      minimumSize: const Size(48, 48)),
                  child: const Text(Textos.yaLoAtendi),
                ),
              ],
              if (alerta.resueltaEn != null) ...<Widget>[
                const SizedBox(height: 10),
                Text(
                  atendidaCuando(fechaAlerta(alerta.resueltaEn!),
                      alerta.resueltoPor),
                  style: Theme.of(contexto)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: Tema.primario),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
