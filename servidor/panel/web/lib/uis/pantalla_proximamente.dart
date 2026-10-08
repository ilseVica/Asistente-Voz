import 'package:flutter/material.dart';

import '../main.dart';
import '../tema.dart';

/// «Próximamente» para Recordatorios y Mensajes.
///
/// No lleva datos de ejemplo. Inventar un recordatorio o un mensaje en una
/// pantalla que todavía no existe es la forma más rápida de que alguien lo
/// pruebe en la demostración y se lleve la idea de que ya funciona.
class PantallaProximamente extends StatelessWidget {
  const PantallaProximamente(
      {super.key, required this.titulo, required this.texto});

  final String titulo;
  final String texto;

  @override
  Widget build(BuildContext contexto) {
    final bool escritorio =
        MediaQuery.sizeOf(contexto).width >= Tema.anchoEscritorio;

    final Widget contenido = Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(Icons.schedule, color: Tema.secundario, size: 44),
              const SizedBox(height: 16),
              Text(titulo,
                  style: Theme.of(contexto).textTheme.headlineMedium,
                  textAlign: TextAlign.center),
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                    color: Tema.suave,
                    borderRadius: BorderRadius.circular(999)),
                child: const Text('Próximamente',
                    style: TextStyle(
                        color: Tema.primario,
                        fontWeight: FontWeight.w700,
                        fontSize: 14)),
              ),
              const SizedBox(height: 16),
              Text(texto,
                  style: Theme.of(contexto)
                      .textTheme
                      .bodyMedium
                      ?.copyWith(color: Tema.secundario, height: 1.5),
                  textAlign: TextAlign.center),
              const SizedBox(height: 28),
              OutlinedButton(
                onPressed: () => PanelScope.de(contexto).irA(Destino.inicio),
                child: const Text('Volver al inicio'),
              ),
            ],
          ),
        ),
      ),
    );

    if (!escritorio) {
      return Scaffold(
        body: SafeArea(child: contenido),
        bottomNavigationBar: _BarraProximamente(titulo: titulo),
      );
    }

    return Scaffold(
      body: Row(
        children: <Widget>[
          _LateralProximamente(titulo: titulo),
          Expanded(child: Container(color: Tema.fondo, child: contenido)),
        ],
      ),
    );
  }
}

class _BarraProximamente extends StatelessWidget {
  const _BarraProximamente({required this.titulo});

  final String titulo;

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
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
              _Item(icono: Icons.home_outlined, activo: Icons.home,
                  rotulo: 'Inicio', sel: false,
                  alPulsar: () => estado.irA(Destino.inicio)),
              _Item(icono: Icons.notifications_outlined,
                  activo: Icons.notifications, rotulo: 'Alertas',
                  cuenta: estado.estado?.alertasSinResolver ?? 0, sel: false,
                  alPulsar: () => estado.irA(Destino.alertas)),
              _Item(
                  icono: Icons.schedule_outlined,
                  activo: Icons.schedule,
                  rotulo: 'Recordatorios',
                  sel: titulo == 'Recordatorios',
                  alPulsar: () => estado.irA(Destino.recordatorios)),
              _Item(icono: Icons.chat_bubble_outline,
                  activo: Icons.chat_bubble, rotulo: 'Mensajes',
                  sel: titulo == 'Mensajes',
                  alPulsar: () => estado.irA(Destino.mensajes)),
            ],
          ),
        ),
      ),
    );
  }
}

class _Item extends StatelessWidget {
  const _Item({
    required this.icono,
    required this.activo,
    required this.rotulo,
    required this.sel,
    required this.alPulsar,
    this.cuenta = 0,
  });

  final IconData icono;
  final IconData activo;
  final String rotulo;
  final bool sel;
  final VoidCallback alPulsar;
  final int cuenta;

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

class _LateralProximamente extends StatelessWidget {
  const _LateralProximamente({required this.titulo});

  final String titulo;

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
                  Text('Asistente de Voz',
                      style: TextStyle(fontSize: 14, color: Tema.secundario)),
                  SizedBox(height: 2),
                  Text('Sala principal',
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
              title: const Text('Inicio',
                  style: TextStyle(
                      color: Tema.secundario, fontWeight: FontWeight.w700)),
              onTap: () => estado.irA(Destino.inicio),
            ),
            ListTile(
              leading: const Icon(Icons.notifications_outlined,
                  color: Tema.secundario),
              title: const Text('Alertas',
                  style: TextStyle(
                      color: Tema.secundario, fontWeight: FontWeight.w700)),
              onTap: () => estado.irA(Destino.alertas),
            ),
            ListTile(
              selected: titulo == 'Recordatorios',
              selectedTileColor: Tema.suave,
              leading: const Icon(Icons.schedule, color: Tema.primario),
              title: const Text('Recordatorios',
                  style: TextStyle(
                      color: Tema.primario, fontWeight: FontWeight.w700)),
              onTap: () => estado.irA(Destino.recordatorios),
            ),
            ListTile(
              selected: titulo == 'Mensajes',
              selectedTileColor: Tema.suave,
              leading:
                  const Icon(Icons.chat_bubble, color: Tema.primario),
              title: const Text('Mensajes',
                  style: TextStyle(
                      color: Tema.primario, fontWeight: FontWeight.w700)),
              onTap: () => estado.irA(Destino.mensajes),
            ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                '${estado.estado?.familiares ?? 0} familiares registrados',
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
