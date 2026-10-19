import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';
import 'banner_sin_conexion.dart';
import 'pantalla_panel.dart' show tituloAlerta, PastillaEstado;
import 'tarjetas.dart';

/// El historial de alertas y «Ya lo atendí».
class PantallaAlertas extends StatelessWidget {
  const PantallaAlertas({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final bool escritorio =
        MediaQuery.sizeOf(contexto).width >= Tema.anchoEscritorio;

    final Widget contenido = SingleChildScrollView(
      padding: EdgeInsets.only(
          left: escritorio ? 0 : 20,
          right: escritorio ? 0 : 20,
          top: 24,
          bottom: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          const BannerSinConexion(),
          if (estado.aviso != null) ...<Widget>[
            const SizedBox(height: 16),
            Text(estado.aviso!,
                style: const TextStyle(color: Tema.primario, fontSize: 15)),
          ],
          const SizedBox(height: 16),
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

    if (!escritorio) {
      return Scaffold(
        body: SafeArea(child: contenido),
        bottomNavigationBar: const _BarraAlertas(),
      );
    }

    return Scaffold(
      body: Row(
        children: <Widget>[
          const _LateralAlertas(),
          Expanded(
            child: Container(
              color: Tema.fondo,
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(32),
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1120),
                    child: contenido,
                  ),
                ),
              ),
            ),
          ),
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
            if (_detalle(alerta) != null) ...<Widget>[
              const SizedBox(height: 10),
              Text(_detalle(alerta)!,
                  style: Theme.of(contexto).textTheme.bodyMedium),
            ],
            if (sePuedeAtender) ...<Widget>[
              const SizedBox(height: 14),
              OutlinedButton(
                onPressed: () => estado.atenderAlerta(alerta.id),
                child: const Text(Textos.yaLoAtendi),
              ),
            ],
            if (alerta.resueltaEn != null) ...<Widget>[
              const SizedBox(height: 10),
              Text(
                '${Textos.atendida} ${fechaAlerta(alerta.resueltaEn!)}',
                style: Theme.of(contexto)
                    .textTheme
                    .bodySmall
                    ?.copyWith(color: Tema.primario),
              ),
            ],
          ],
        ),
      ),
    );
  }

  String? _detalle(Alerta a) {
    if (a.tipo == 'gas' && a.carga['gas_adc'] != null) {
      return 'Sensor de gas: ${a.carga['gas_adc']}';
    }
    if (a.tipo == 'inactividad' && a.carga['minutos'] != null) {
      return 'Sin movimiento durante ${a.carga['minutos']} minutos';
    }
    final Object? origen = a.carga['origen'];
    if (origen == null) return null;
    return detalleOrigen(origen.toString());
  }
}

class _LateralAlertas extends StatelessWidget {
  const _LateralAlertas();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    return Container(
      width: 230,
      decoration: const BoxDecoration(color: Tema.tarjeta),
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            const Padding(
              padding: EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(Textos.asistenteDeVoz,
                      style: TextStyle(fontSize: 14, color: Tema.secundario)),
                  SizedBox(height: 2),
                  Text(Textos.salaPrincipal,
                      style: TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.w700,
                          color: Tema.texto)),
                ],
              ),
            ),
            const Divider(),
            const SizedBox(height: 8),
            ListTile(
              leading: const Icon(Icons.home_outlined, color: Tema.secundario),
              title: const Text(Textos.inicio,
                  style: TextStyle(
                      color: Tema.secundario, fontWeight: FontWeight.w700)),
              onTap: () => estado.irA(Destino.inicio),
            ),
            ListTile(
              selected: true,
              selectedTileColor: Tema.suave,
              leading: const Icon(Icons.notifications,
                  color: Tema.primario),
              title: const Text(Textos.alertas,
                  style: TextStyle(
                      color: Tema.primario, fontWeight: FontWeight.w700)),
              onTap: () => estado.irA(Destino.alertas),
            ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                familiares(estado.estado?.familiares ?? 0),
                style: Theme.of(contexto).textTheme.bodySmall,
              ),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
}

class _BarraAlertas extends StatelessWidget {
  const _BarraAlertas();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final int pendientes = estado.estado?.alertasSinResolver ?? 0;
    return Container(
      decoration: const BoxDecoration(
        color: Tema.tarjeta,
        border: Border(top: BorderSide(color: Tema.borde)),
      ),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: Tema.altoNavegacion + 16,
          child: Row(
            children: <Widget>[
              _Boton(icono: Icons.home_outlined, activo: Icons.home,
                  rotulo: Textos.inicio, cuenta: 0, sel: false,
                  alPulsar: () => estado.irA(Destino.inicio)),
              _Boton(icono: Icons.notifications_outlined,
                  activo: Icons.notifications, rotulo: Textos.alertas,
                  cuenta: pendientes, sel: true,
                  alPulsar: () => estado.irA(Destino.alertas)),
              _Boton(icono: Icons.schedule_outlined, activo: Icons.schedule,
                  rotulo: Textos.recordatorios, cuenta: 0, sel: false,
                  alPulsar: () => estado.irA(Destino.recordatorios)),
              _Boton(icono: Icons.chat_bubble_outline,
                  activo: Icons.chat_bubble, rotulo: Textos.mensajes,
                  cuenta: 0, sel: false,
                  alPulsar: () => estado.irA(Destino.mensajes)),
            ],
          ),
        ),
      ),
    );
  }
}

class _Boton extends StatelessWidget {
  const _Boton({
    required this.icono,
    required this.activo,
    required this.rotulo,
    required this.cuenta,
    required this.sel,
    required this.alPulsar,
  });

  final IconData icono;
  final IconData activo;
  final String rotulo;
  final int cuenta;
  final bool sel;
  final VoidCallback alPulsar;

  @override
  Widget build(BuildContext contexto) {
    final Color color = sel ? Tema.primario : Tema.secundario;
    return Expanded(
      child: InkWell(
        onTap: alPulsar,
        child: Container(
          height: Tema.altoNavegacion + 16,
          padding: const EdgeInsets.only(top: 8, bottom: 12),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              Stack(clipBehavior: Clip.none, children: <Widget>[
                Icon(sel ? activo : icono, color: color, size: 26),
                if (cuenta > 0)
                  Positioned(
                    right: -8,
                    top: -4,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 5, vertical: 1),
                      decoration: const BoxDecoration(
                          color: Tema.peligro, shape: BoxShape.circle),
                      child: Text('$cuenta',
                          style: const TextStyle(
                              color: Tema.tarjeta,
                              fontSize: 11,
                              fontWeight: FontWeight.w700)),
                    ),
                  ),
              ]),
              const SizedBox(height: 3),
              Text(rotulo,
                  style: TextStyle(
                      color: color,
                      fontSize: 13,
                      fontWeight: FontWeight.w700)),
            ],
          ),
        ),
      ),
    );
  }
}
