import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';

/// Los dos «sin conexión», que no son lo mismo (apartado 4.5).
class BannerSinConexion extends StatelessWidget {
  const BannerSinConexion({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;

    if (!estado.conectadoAlServidor) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: _Cartel(
          icono: Icons.warning_amber_rounded,
          titulo: Textos.sinConexionServidor,
          texto: Textos.sinConexionServidorTexto,
        ),
      );
    }

    if (e == null) return const SizedBox.shrink();

    if (e.sitio.estado == noInstalado) return const SizedBox.shrink();

    if (e.sitio.estado == desconectado) {
      final int ahora = estado.ahoraLocal();
      final int? recibidoEn = e.nodo.recibidoEn ?? e.sitio.recibidoEn;
      final String titulo = recibidoEn == null
          ? Textos.sinConexionSala
          : '${Textos.sinConexionSala} desde ${hace(recibidoEn, ahora: ahora)}';
      return Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: _Cartel(
          icono: Icons.warning_amber_rounded,
          titulo: titulo,
          texto: Textos.sinConexionSalaTexto,
        ),
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

/// Un cartel ámbar: fondo `#FBEFD9`, borde `#E8C98F`, título y texto en ámbar
/// oscuro, igual que el del prototipo de «sin conexión».
class _Cartel extends StatelessWidget {
  const _Cartel({
    required this.icono,
    required this.titulo,
    required this.texto,
  });

  final IconData icono;
  final String titulo;
  final String texto;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Tema.ambarFondo,
        borderRadius: BorderRadius.circular(Tema.radio),
        border: Border.all(color: Tema.ambarBorde),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icono, color: Tema.ambarPildora, size: 24),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(titulo,
                    style: const TextStyle(
                        color: Tema.ambarPildora,
                        fontWeight: FontWeight.w700,
                        fontSize: 16)),
                const SizedBox(height: 4),
                Text(texto,
                    style: const TextStyle(
                        color: Tema.ambarTexto, fontSize: 14, height: 1.4)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
