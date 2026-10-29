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
    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;

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
    } else if (estado.familiaresLista.isEmpty) {
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
          for (final Map<String, dynamic> f in estado.familiaresLista)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(
                children: <Widget>[
                  Container(
                    width: movil ? 46 : 52,
                    height: movil ? 46 : 52,
                    decoration: const BoxDecoration(
                        color: Tema.suave, shape: BoxShape.circle),
                    alignment: Alignment.center,
                    child: Text(
                      ((f['nombre'] as String?) ?? '').isEmpty
                          ? '?'
                          : (f['nombre'] as String)[0].toUpperCase(),
                      style: TextStyle(
                          fontSize: movil ? 20 : 22,
                          fontWeight: FontWeight.w700,
                          color: Tema.primario,
                          fontFamily: Tema.familia),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      (f['nombre'] as String?)?.trim().isEmpty ?? true
                          ? Textos.nombreSinDefinir
                          : '${(f['nombre'] as String).trim()}${(f['tiene_telegram'] == true) ? '' : ' (sin Telegram)'}',
                      style: TextStyle(
                          fontSize: movil ? 18 : 19,
                          fontWeight: FontWeight.w700,
                          fontFamily: Tema.familia),
                    ),
                  ),
                  if (estado.esAdmin)
                    TextButton(
                      onPressed: () async {
                        final bool? seguro = await showDialog<bool>(
                          context: contexto,
                          builder: (BuildContext c) => AlertDialog(
                            title: Text(Textos.desactivarFamiliar),
                            content: Text(Textos.confirmacionDesactivar),
                            actions: <Widget>[
                              TextButton(onPressed: () => Navigator.of(c).pop(false), child: const Text(Textos.cancelar)),
                              FilledButton(onPressed: () => Navigator.of(c).pop(true), child: Text(Textos.desactivarFamiliar)),
                            ],
                          ),
                        );
                        if (seguro == true) {
                          await estado.desactivarFamiliar((f['nombre'] as String).trim());
                        }
                      },
                      child: Text(Textos.desactivarFamiliar),
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
          Text(Textos.familiares,
              style: TextStyle(
                  fontSize: movil ? 24 : 28,
                  fontWeight: FontWeight.w700,
                  color: Tema.texto,
                  fontFamily: Tema.familia)),
          const SizedBox(height: 6),
          Text(Textos.familiaresSubtitulo,
              style: TextStyle(
                  fontSize: movil ? 15 : 17,
                  color: Tema.secundario,
                  fontFamily: Tema.familia)),
          const SizedBox(height: 16),
          if (estado.esAdmin) ...<Widget>[
            FilledButton.icon(
              onPressed: () async {
                final TextEditingController nombreCtrl = TextEditingController();
                bool conTelegram = true;
                final bool? ok = await showDialog<bool>(
                  context: contexto,
                  builder: (BuildContext c) => StatefulBuilder(
                    builder: (BuildContext c, void Function(void Function()) setSt) => AlertDialog(
                      title: Text(Textos.agregarFamiliar),
                      content: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: <Widget>[
                          TextField(controller: nombreCtrl, decoration: const InputDecoration(labelText: 'Nombre')),
                          const SizedBox(height: 8),
                          SwitchListTile(
                            title: const Text('Con Telegram'),
                            value: conTelegram,
                            onChanged: (bool v) => setSt(() => conTelegram = v),
                          ),
                        ],
                      ),
                      actions: <Widget>[
                        TextButton(onPressed: () => Navigator.of(c).pop(false), child: const Text(Textos.cancelar)),
                        FilledButton(onPressed: () => Navigator.of(c).pop(true), child: const Text('Agregar')),
                      ],
                    ),
                  ),
                );
                if (ok == true) {
                  final String nombre = nombreCtrl.text.trim();
                  if (nombre.isNotEmpty) {
                    final Map<String, dynamic>? r = await estado.agregarFamiliar(nombre, conTelegram);
                    if (r != null && r['enlace'] != null) {
                      // mostrar enlace una vez
                      await showDialog<void>(
                        context: contexto,
                        builder: (BuildContext c) => AlertDialog(
                          title: Text(Textos.enlaceInvitacion),
                          content: SelectableText(r['enlace'] as String),
                          actions: <Widget>[
                            TextButton(onPressed: () => Navigator.of(c).pop(), child: const Text('Cerrar')),
                          ],
                        ),
                      );
                    } else if (r != null && r['codigo'] != null) {
                      await showDialog<void>(
                        context: contexto,
                        builder: (BuildContext c) => AlertDialog(
                          title: Text(Textos.codigoRegistro),
                          content: Text('Envíe al bot: /registro ${r['codigo']}'),
                          actions: <Widget>[
                            TextButton(onPressed: () => Navigator.of(c).pop(), child: const Text('Cerrar')),
                          ],
                        ),
                      );
                    }
                  }
                }
              },
              icon: const Icon(Icons.person_add),
              label: Text(Textos.agregarFamiliar),
            ),
            const SizedBox(height: 12),
          ],
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
              '${familiares(estado.familiaresLista.length)} · ${Textos.avisoFamiliares}',
              style: TextStyle(
                  fontSize: movil ? 14 : 16,
                  color: Tema.secundario,
                  fontFamily: Tema.familia),
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
    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;
    final double fuente = movil ? 14 : 15;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: movil
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                TextButton(
                  onPressed: estado.aceptarAviso,
                  style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      minimumSize: const Size(0, 32),
                      foregroundColor: Tema.primario),
                  child: Text(Textos.verAviso,
                      style: TextStyle(
                          fontSize: fuente,
                          color: Tema.primario,
                          decoration: TextDecoration.underline,
                          fontFamily: Tema.familia)),
                ),
                TextButton(
                  onPressed: () => _salir(contexto, estado),
                  style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      minimumSize: const Size(0, 32)),
                  child: Text(Textos.cerrarSesion,
                      style: TextStyle(
                          fontSize: fuente,
                          color: Tema.secundario,
                          fontFamily: Tema.familia)),
                ),
              ],
            )
          : Row(
              children: <Widget>[
                TextButton(
                  onPressed: estado.aceptarAviso,
                  style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      minimumSize: const Size(0, 32),
                      foregroundColor: Tema.primario),
                  child: Text(Textos.verAviso,
                      style: TextStyle(
                          fontSize: fuente,
                          color: Tema.primario,
                          decoration: TextDecoration.underline,
                          fontFamily: Tema.familia)),
                ),
                const SizedBox(width: 24),
                TextButton(
                  onPressed: () => _salir(contexto, estado),
                  style: TextButton.styleFrom(
                      padding: EdgeInsets.zero,
                      minimumSize: const Size(0, 32)),
                  child: Text(Textos.cerrarSesion,
                      style: TextStyle(
                          fontSize: fuente,
                          color: Tema.secundario,
                          fontFamily: Tema.familia)),
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
