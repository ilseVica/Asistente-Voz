import 'package:flutter/material.dart';

import '../main.dart';
import '../tema.dart';
import '../textos.dart';
import 'navegacion.dart';

/// «Familiares»: quiénes reciben los avisos de esta casa (decisión C).
///
/// Es una pantalla de sólo lectura y sólo eso: la lista con la que se pinta sale
/// de `GET /api/familiares`, que devuelve SÓLO los nombres de los familiares
/// activos y nada más (ni chat_id, ni los dados de baja). Si no hay nadie, se
/// enseña el texto de cómo se añade a alguien; si la petición falla, un aviso
/// con su botón de reintentar. No se inventan nombres de ejemplo: un apodo que
/// no existe en Telegram no se escribe aquí ni aunque quede más bonito.
class PantallaFamiliares extends StatelessWidget {
  const PantallaFamiliares({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final TextTheme tipografia = Theme.of(contexto).textTheme;

    final Widget cuerpo;
    if (estado.cargandoFamiliares) {
      cuerpo = const Padding(
        padding: EdgeInsets.symmetric(vertical: 48),
        child: Center(
          child: SizedBox(
            height: 28,
            width: 28,
            child: CircularProgressIndicator(
                strokeWidth: 3, color: Tema.primario),
          ),
        ),
      );
    } else if (estado.errorFamiliares != null) {
      cuerpo = _ErrorFamiliares(
        mensaje: estado.errorFamiliares!,
        alReintentar: estado.cargarFamiliares,
      );
    } else if (estado.nombresFamiliares.isEmpty) {
      cuerpo = Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(Textos.sinFamiliares, style: tipografia.bodyMedium),
          const SizedBox(height: 8),
          Text(Textos.sinFamiliaresAyuda, style: tipografia.bodySmall),
        ],
      );
    } else {
      cuerpo = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          for (final String nombre in estado.nombresFamiliares)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(
                children: <Widget>[
                  Container(
                    width: 40,
                    height: 40,
                    decoration: const BoxDecoration(
                        color: Tema.suave, shape: BoxShape.circle),
                    child: const Icon(Icons.person,
                        color: Tema.primario, size: 22),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      nombre.isEmpty
                          ? Textos.nombreSinDefinir
                          : nombre,
                      style: tipografia.bodyMedium
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                  ),
                ],
              ),
            ),
        ],
      );
    }

    return EstructuraPanel(
      contenido: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text(Textos.familiares, style: tipografia.headlineLarge),
          const SizedBox(height: 6),
          Text(Textos.familiaresSubtitulo, style: tipografia.bodySmall),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: cuerpo,
            ),
          ),
          if (!estado.cargandoFamiliares &&
              estado.errorFamiliares == null) ...<Widget>[
            const SizedBox(height: 12),
            Text(
              '${familiares(estado.nombresFamiliares.length)} · ${Textos.avisoFamiliares}',
              style: tipografia.bodySmall,
            ),
          ],
          const SizedBox(height: 8),
          const PieFamiliares(),
        ],
      ),
    );
  }
}

/// El fallo de la petición, con su reintento (es la única pantalla que puede
/// mostrar un error de carga: las otras refrescan sin avisar cada pocos segundos).
class _ErrorFamiliares extends StatelessWidget {
  const _ErrorFamiliares({required this.mensaje, required this.alReintentar});

  final String mensaje;
  final VoidCallback alReintentar;

  @override
  Widget build(BuildContext contexto) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Row(
          children: <Widget>[
            const Icon(Icons.error_outline, color: Tema.peligro, size: 22),
            const SizedBox(width: 10),
            Expanded(
              child: Text(mensaje,
                  style: const TextStyle(color: Tema.peligro, fontSize: 15)),
            ),
          ],
        ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: alReintentar,
          icon: const Icon(Icons.refresh, size: 18),
          label: const Text(Textos.reintentar),
          style: OutlinedButton.styleFrom(
              foregroundColor: Tema.primario,
              minimumSize: const Size(48, 48)),
        ),
      ],
    );
  }
}

/// El pie con «Cerrar sesión» y el aviso de privacidad, que es el mismo de
/// Inicio: otra tarjeta con él costaría un viaje a la raíz.
class PieFamiliares extends StatelessWidget {
  const PieFamiliares({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: <Widget>[
          TextButton(
            onPressed: estado.aceptarAviso,
            style: TextButton.styleFrom(
                padding: EdgeInsets.zero,
                minimumSize: const Size(0, 48),
                foregroundColor: Tema.primario),
            child: Text(Textos.verAviso,
                style: Theme.of(contexto)
                    .textTheme
                    .bodySmall
                    ?.copyWith(
                        color: Tema.primario,
                        decoration: TextDecoration.underline)),
          ),
          const Spacer(),
          TextButton(
            onPressed: () => _salir(contexto, estado),
            style: TextButton.styleFrom(
                padding: EdgeInsets.zero,
                minimumSize: const Size(0, 48)),
            child: Text(Textos.cerrarSesion,
                style: Theme.of(contexto)
                    .textTheme
                    .bodySmall
                    ?.copyWith(color: Tema.secundario)),
          ),
        ],
      ),
    );
  }

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
}
