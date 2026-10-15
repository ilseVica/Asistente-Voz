import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';

/// Los dos «sin conexión», que no son lo mismo (apartado 4.5).
///
///  - **Sin conexión con el servidor**: se cayó el WebSocket. Es el móvil sin
///    Tailscale o sin Internet. Lo que se muestra es lo último que se sabía,
///    marcado como viejo.
///  - **Sitio sin conexión**: el panel SÍ habla con el servidor, pero el servidor
///    no ha oído al sitio en más de 90 s. Sólo tiene sentido si hay sitio que
///    escuchar.
///
/// Confundirlas es hacer que un sitio sin Internet parezca un móvil sin cobertura,
/// y por eso hay dos textos y dos colores. Un tercero, el gris de «no instalado»,
/// no lleva cartel: es una línea que dice que ese componente no está en esta casa.
class BannerSinConexion extends StatelessWidget {
  const BannerSinConexion({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;

    if (!estado.conectadoAlServidor) {
      return const _Cartel(
        color: Color(0xFF9F1D1D),
        fondo: Color(0xFFF6E9E9),
        icono: Icons.wifi_off,
        titulo: Textos.sinConexionServidor,
        texto: Textos.sinConexionServidorTexto,
      );
    }

    if (e == null) return const SizedBox.shrink();

    // El sitio sólo se puede echar de menos si está instalado. `no_instalado`
    // es la normalidad y no un fallo, así que no lleva cartel.
    if (e.sitio.estado == noInstalado) return const SizedBox.shrink();

    if (e.sitio.estado == desconectado) {
      return const _Cartel(
        color: Color(0xFF8A6D1D),
        fondo: Color(0xFFFBF3DE),
        icono: Icons.cell_tower,
        titulo: Textos.casaNoResponde,
        texto: Textos.casaNoRespondeTexto,
      );
    }

    return const SizedBox.shrink();
  }
}

/// El gris de «no instalado». No es un cartel: una línea y nada más.
///
/// El texto es fijo (la luz) y por eso no recibe un sujeto: así no hay que
/// concordar el género de quien lo use.
class LineaLuzNoInstalada extends StatelessWidget {
  const LineaLuzNoInstalada({super.key});

  @override
  Widget build(BuildContext contexto) {
    return Text(
      Textos.luzNoInstalada,
      style: Theme.of(contexto).textTheme.bodySmall,
    );
  }
}

class _Cartel extends StatelessWidget {
  const _Cartel({
    required this.color,
    required this.fondo,
    required this.icono,
    required this.titulo,
    required this.texto,
  });

  final Color color;
  final Color fondo;
  final IconData icono;
  final String titulo;
  final String texto;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: fondo,
        borderRadius: BorderRadius.circular(Tema.radio),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icono, color: color, size: 24),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(titulo,
                    style: TextStyle(
                        color: color,
                        fontWeight: FontWeight.w700,
                        fontSize: 16)),
                const SizedBox(height: 4),
                Text(texto,
                    style: const TextStyle(
                        color: Tema.secundario, fontSize: 14, height: 1.4)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
