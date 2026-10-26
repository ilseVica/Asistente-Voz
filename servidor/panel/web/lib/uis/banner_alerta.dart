import 'package:flutter/material.dart';

import '../main.dart';
import '../tema.dart';
import '../textos.dart';
import '../presentacion_alerta.dart';
import '../modelos.dart';

/// El aviso de que hay gas en la sala, fijo arriba en TODAS las pantallas
/// (decisión A.1 y el diseño de `alerta-gas-celular.html`).
///
/// **Es lo único rojo del panel**: una alerta activa sí es una emergencia y
/// merece el color. El resto de carteles (sin conexión, sin datos) usan el
/// ámbar del prototipo, que es un aviso y no un grito.
///
/// Sólo se muestra con `alertasSinResolver > 0`: en cuanto alguien la atiende,
/// se retira de la pantalla sin que nadie tenga que recargar nada, porque
/// llegan los cambios del WebSocket.
class BannerAlertaActiva extends StatelessWidget {
  const BannerAlertaActiva({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Alerta? alerta = estado.alertaMasUrgente;

    if (alerta == null) return const SizedBox.shrink();

    final PresentacionAlerta pres = presentacionAlerta(
        alerta.tipo, origen: alerta.carga['origen']?.toString(),
        carga: alerta.carga);
    final String subtitulo =
        '${pres.cuerpo} · ${fechaAlerta(alerta.creadoEn)}';

    final bool estrecho = MediaQuery.sizeOf(contexto).width < 700;

    return Container(
      color: pres.colorFondo,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
      child: SafeArea(
        bottom: false,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: pres.colorFondo,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                        color: pres.colorTexto.withValues(alpha: 0.6), width: 2),
                  ),
                  child: Icon(pres.icono,
                      color: pres.colorTexto, size: 24),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(pres.titulo,
                          style: TextStyle(
                              color: pres.colorTexto,
                              fontSize: 14,
                              fontWeight: FontWeight.w700)),
                      const SizedBox(height: 4),
                      Text(pres.cuerpo,
                          style: TextStyle(
                              color: pres.colorTexto,
                              fontSize: 20,
                              height: 1.2,
                              fontWeight: FontWeight.w800)),
                      const SizedBox(height: 6),
                      Text(subtitulo,
                          style: TextStyle(
                              color: pres.colorTexto.withValues(alpha: 0.9),
                              fontSize: 14,
                              height: 1.3)),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
            // Los botones van apilados a ancho completo y con 48 px de alto:
            // en el móvil son una de las pocas cosas que se tocan con prisa
            // (decisión F.2).
            if (estrecho) ...<Widget>[
              _BotonAlerta(
                texto: Textos.ver,
                relleno: false,
                alPulsar: () => estado.abrirAlerta(alerta.id),
              ),
              const SizedBox(height: 8),
              _BotonAlerta(
                texto: Textos.yaLoAtendi,
                relleno: true,
                alPulsar: () => estado.atenderAlerta(alerta.id),
              ),
              const SizedBox(height: 4),
            ] else
              Row(
                children: <Widget>[
                  Expanded(
                    child: _BotonAlerta(
                      texto: Textos.ver,
                      relleno: false,
                      alPulsar: () => estado.abrirAlerta(alerta.id),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: _BotonAlerta(
                      texto: Textos.yaLoAtendi,
                      relleno: true,
                      alPulsar: () => estado.atenderAlerta(alerta.id),
                    ),
                  ),
                ],
              ),
            const SizedBox(height: 14),
          ],
        ),
      ),
    );
  }
}

/// «Ya lo atendí» (relleno blanco) o «Ver» (borde) sobre el rojo.
class _BotonAlerta extends StatelessWidget {
  const _BotonAlerta({
    required this.texto,
    required this.relleno,
    required this.alPulsar,
  });

  final String texto;
  final bool relleno;
  final VoidCallback alPulsar;

  @override
  Widget build(BuildContext contexto) {
    final Color textoColor = relleno ? Tema.peligro : Tema.tarjeta;
    return SizedBox(
      height: 48,
      child: ElevatedButton(
        onPressed: alPulsar,
        style: ElevatedButton.styleFrom(
          backgroundColor: relleno ? Tema.tarjeta : Colors.transparent,
          foregroundColor: textoColor,
          elevation: 0,
          shadowColor: Colors.transparent,
          side: relleno
              ? null
              : const BorderSide(color: Tema.tarjeta, width: 1.5),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(Tema.radioBoton)),
          padding: const EdgeInsets.symmetric(horizontal: 20),
        ),
        child: Text(texto,
            style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w800,
                color: textoColor)),
      ),
    );
  }
}
