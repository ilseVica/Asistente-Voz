import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';
import 'banner_sin_conexion.dart';
import 'tarjetas.dart';

/// Inicio: las tarjetas, con la barra de abajo en móvil y lateral en escritorio.
///
/// El corte son 900 px (apartado 5.4): por encima, tres columnas con barra
/// lateral; por debajo, una columna con barra inferior. No hay tres pantallas,
/// hay una que se ordena distinto, y eso es lo que evita que el panel de
/// escritorio diga una cosa y el del móvil otra.
class PantallaPanel extends StatelessWidget {
  const PantallaPanel({super.key});

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
            _AvisoPanel(texto: estado.aviso!, alCerrar: estado.limpiarAviso),
            const SizedBox(height: 16),
          ],
          const Cabecera(),
          const SizedBox(height: 20),
          if (estado.estado?.luz.estado == noInstalado)
            const LineaLuzNoInstalada()
          else
            const TarjetaLuz(),
          const SizedBox(height: 16),
          const TarjetaAmbiente(),
          const SizedBox(height: 16),
          const _AlertasRecientes(),
          const SizedBox(height: 16),
          const TarjetaEnlaces(),
          const SizedBox(height: 8),
          const PiePanel(),
        ],
      ),
    );

    if (!escritorio) {
      return Scaffold(
        body: SafeArea(child: contenido),
        bottomNavigationBar: const _BarraInferior(),
      );
    }

    return Scaffold(
      body: Row(
        children: <Widget>[
          const _BarraLateral(),
          Expanded(
            child: Container(
              color: Tema.fondo,
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(32),
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1040),
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

/// Las cuatro últimas alertas, con su estado y su hora.
class _AlertasRecientes extends StatelessWidget {
  const _AlertasRecientes();

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final List<Alerta> recientes =
        estado.alertas.length > 4 ? estado.alertas.sublist(0, 4) : estado.alertas;

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
                      style: Theme.of(contexto).textTheme.titleLarge),
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
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(tituloAlerta(alerta.tipo),
                    style: Theme.of(contexto)
                        .textTheme
                        .bodyMedium
                        ?.copyWith(fontWeight: FontWeight.w700)),
                Text(fechaAlerta(alerta.creadoEn),
                    style: Theme.of(contexto).textTheme.bodySmall),
              ],
            ),
          ),
          PastillaEstado(estado: alerta.estado),
        ],
      ),
    );
  }
}

/// El nombre de la alerta en pantalla. El servidor manda `gas`, `ayuda`,
/// `inactividad` y `miedo`; se traducen aquí y no en cada pantalla.
String tituloAlerta(String tipo) {
  switch (tipo) {
    case 'gas':
      return Textos.alertaDeGas;
    case 'ayuda':
      return Textos.peticionDeAyuda;
    case 'inactividad':
      return Textos.sinMovimientoEnLaCasa;
    case 'miedo':
      return Textos.haDichoTenerMiedo;
    default:
      return tipo;
  }
}

class PastillaEstado extends StatelessWidget {
  const PastillaEstado({super.key, required this.estado});

  final String estado;

  @override
  Widget build(BuildContext contexto) {
    final Color color;
    final String texto;
    switch (estado) {
      case 'resuelta':
        color = Tema.primario;
        texto = Textos.atendida;
      case 'descartada':
        color = Tema.secundario;
        texto = Textos.descartada;
      case 'enviada':
        color = Tema.primario;
        texto = Textos.enviada;
      default:
        color = const Color(0xFF8A6D1D);
        texto = Textos.pendiente;
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(999)),
      child: Text(texto,
          style: TextStyle(
              color: color, fontWeight: FontWeight.w700, fontSize: 13)),
    );
  }
}

class _AvisoPanel extends StatelessWidget {
  const _AvisoPanel({required this.texto, required this.alCerrar});

  final String texto;
  final VoidCallback alCerrar;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
      decoration: BoxDecoration(
        color: Tema.suave,
        borderRadius: BorderRadius.circular(Tema.radio),
        border: Border.all(color: Tema.primario.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: <Widget>[
          const Icon(Icons.info_outline, color: Tema.primario, size: 22),
          const SizedBox(width: 10),
          Expanded(
              child: Text(texto,
                  style: const TextStyle(color: Tema.primario, fontSize: 15))),
          IconButton(
              onPressed: alCerrar,
              icon: const Icon(Icons.close,
                  color: Tema.primario, size: 20),
              tooltip: 'Cerrar'),
        ],
      ),
    );
  }
}

class _BarraInferior extends StatelessWidget {
  const _BarraInferior();

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
              _DestinoBarra(
                icono: Icons.home_outlined,
                activo: Icons.home,
                rotulo: Textos.inicio,
                seleccionado: estado.destino == Destino.inicio,
                cuenta: 0,
                alPulsar: () => estado.irA(Destino.inicio),
              ),
              _DestinoBarra(
                icono: Icons.notifications_outlined,
                activo: Icons.notifications,
                rotulo: Textos.alertas,
                seleccionado: estado.destino == Destino.alertas,
                cuenta: pendientes,
                alPulsar: () => estado.irA(Destino.alertas),
              ),
              _DestinoBarra(
                icono: Icons.schedule_outlined,
                activo: Icons.schedule,
                rotulo: Textos.recordatorios,
                seleccionado: estado.destino == Destino.recordatorios,
                cuenta: 0,
                alPulsar: () => estado.irA(Destino.recordatorios),
              ),
              _DestinoBarra(
                icono: Icons.chat_bubble_outline,
                activo: Icons.chat_bubble,
                rotulo: Textos.mensajes,
                seleccionado: estado.destino == Destino.mensajes,
                cuenta: 0,
                alPulsar: () => estado.irA(Destino.mensajes),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DestinoBarra extends StatelessWidget {
  const _DestinoBarra({
    required this.icono,
    required this.activo,
    required this.rotulo,
    required this.seleccionado,
    required this.cuenta,
    required this.alPulsar,
  });

  final IconData icono;
  final IconData activo;
  final String rotulo;
  final bool seleccionado;
  final int cuenta;
  final VoidCallback alPulsar;

  @override
  Widget build(BuildContext contexto) {
    final Color color = seleccionado ? Tema.primario : Tema.secundario;
    return Expanded(
      child: InkWell(
        onTap: alPulsar,
        child: Container(
          height: Tema.altoNavegacion + 16,
          padding: const EdgeInsets.only(top: 8, bottom: 12),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              Stack(
                clipBehavior: Clip.none,
                children: <Widget>[
                  Icon(seleccionado ? activo : icono, color: color, size: 26),
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
                ],
              ),
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

class _BarraLateral extends StatelessWidget {
  const _BarraLateral();

  Future<void> _salir(BuildContext contexto, EstadoPanel estado) async {
    final bool? seguro = await showDialog<bool>(
      context: contexto,
      builder: (BuildContext contexto) => AlertDialog(
        title: const Text(Textos.cerrarSesionTitulo),
        content: const Text(Textos.cerrarSesionTexto),
        actions: <Widget>[
          TextButton(
              onPressed: () => Navigator.of(contexto).pop(false),
              child: const Text(Textos.cancelar)),
          FilledButton(
              onPressed: () => Navigator.of(contexto).pop(true),
              child: const Text(Textos.cerrarSesion)),
        ],
      ),
    );
    if (seguro == true) await estado.cerrarSesion();
  }

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final int pendientes = estado.estado?.alertasSinResolver ?? 0;

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
                      style: TextStyle(
                          fontSize: 14, color: Tema.secundario)),
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
            _DestinoLateral(
              icono: Icons.home_outlined,
              activo: Icons.home,
              rotulo: Textos.inicio,
              seleccionado: estado.destino == Destino.inicio,
              cuenta: 0,
              alPulsar: () => estado.irA(Destino.inicio),
            ),
            _DestinoLateral(
              icono: Icons.notifications_outlined,
              activo: Icons.notifications,
              rotulo: Textos.alertas,
              seleccionado: estado.destino == Destino.alertas,
              cuenta: pendientes,
              alPulsar: () => estado.irA(Destino.alertas),
            ),
            _DestinoLateral(
              icono: Icons.schedule_outlined,
              activo: Icons.schedule,
              rotulo: Textos.recordatorios,
              seleccionado: estado.destino == Destino.recordatorios,
              cuenta: 0,
              alPulsar: () => estado.irA(Destino.recordatorios),
            ),
            _DestinoLateral(
              icono: Icons.chat_bubble_outline,
              activo: Icons.chat_bubble,
              rotulo: Textos.mensajes,
              seleccionado: estado.destino == Destino.mensajes,
              cuenta: 0,
              alPulsar: () => estado.irA(Destino.mensajes),
            ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                familiares(estado.estado?.familiares ?? 0),
                style: Theme.of(contexto).textTheme.bodySmall,
              ),
            ),
            const Divider(),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: TextButton(
                onPressed: () => _salir(contexto, estado),
                style: TextButton.styleFrom(
                    alignment: Alignment.centerLeft,
                    foregroundColor: Tema.secundario),
                child: const Text(Textos.cerrarSesion),
              ),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
}

class _DestinoLateral extends StatelessWidget {
  const _DestinoLateral({
    required this.icono,
    required this.activo,
    required this.rotulo,
    required this.seleccionado,
    required this.cuenta,
    required this.alPulsar,
  });

  final IconData icono;
  final IconData activo;
  final String rotulo;
  final bool seleccionado;
  final int cuenta;
  final VoidCallback alPulsar;

  @override
  Widget build(BuildContext contexto) {
    final Color color = seleccionado ? Tema.primario : Tema.secundario;
    return InkWell(
      onTap: alPulsar,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 13),
        decoration: BoxDecoration(
          color: seleccionado ? Tema.suave : Colors.transparent,
          border: Border(
              left: BorderSide(
                  color: seleccionado ? Tema.primario : Colors.transparent,
                  width: 3)),
        ),
        child: Row(
          children: <Widget>[
            Icon(seleccionado ? activo : icono, color: color, size: 22),
            const SizedBox(width: 12),
            Expanded(
              child: Text(rotulo,
                  style: TextStyle(
                      color: color,
                      fontSize: 15,
                      fontWeight: FontWeight.w700)),
            ),
            if (cuenta > 0)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: const BoxDecoration(
                    color: Tema.peligro, shape: BoxShape.circle),
                child: Text('$cuenta',
                    style: const TextStyle(
                        color: Tema.tarjeta,
                        fontSize: 11,
                        fontWeight: FontWeight.w700)),
              ),
          ],
        ),
      ),
    );
  }
}
