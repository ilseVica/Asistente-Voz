import 'package:flutter/material.dart';

import 'modelos.dart';
import 'tema.dart';
import 'textos.dart';

/// La presentación de una alerta en pantalla: la única fuente de verdad para
/// texto, ícono y color según el tipo. Ningún widget decide por su cuenta.
class PresentacionAlerta {
  const PresentacionAlerta({
    required this.pildora,
    required this.titulo,
    required this.cuerpo,
    required this.icono,
    required this.colorTexto,
    required this.colorFondo,
    required this.peligro,
  });

  final String pildora;
  final String titulo;
  final String cuerpo;
  final IconData icono;
  final Color colorTexto;
  final Color colorFondo;
  final bool peligro;
}

PresentacionAlerta presentacionAlerta(
  String tipo, {
  String? origen,
  Map<String, dynamic>? carga,
}) {
  final Map<String, dynamic> c = carga ?? const <String, dynamic>{};
  final String o = origen ?? c['origen']?.toString() ?? '';

  switch (tipo) {
    case 'gas':
      return const PresentacionAlerta(
        pildora: Textos.alertaActivaPildora,
        titulo: Textos.alertaActiva,
        cuerpo: Textos.gasDetectado,
        icono: Icons.local_gas_station,
        colorTexto: Tema.tarjeta,
        colorFondo: Tema.peligro,
        peligro: true,
      );
    case 'ayuda':
      String cuerpo = Textos.ayudaGenerica;
      if (o == 'voz') cuerpo = Textos.ayudaPorVoz;
      if (o == 'alexa') cuerpo = Textos.ayudaPorAlexa;
      return PresentacionAlerta(
        pildora: Textos.pidenAyuda,
        titulo: Textos.pidenAyudaEnLaSala,
        cuerpo: cuerpo,
        icono: Icons.support_agent,
        colorTexto: Tema.tarjeta,
        colorFondo: Tema.peligro,
        peligro: true,
      );
    case 'inactividad':
      final int min = (c['minutos'] as num?)?.toInt() ?? 0;
      return PresentacionAlerta(
        pildora: Textos.sinMovimiento,
        titulo: Textos.sinMovimientoEnLaSala,
        cuerpo: Textos.sinMovimientoMinutos(min),
        icono: Icons.schedule,
        colorTexto: Tema.ambarPildora,
        colorFondo: Tema.ambarFondo,
        peligro: false,
      );
    default:
      return const PresentacionAlerta(
        pildora: Textos.aviso,
        titulo: Textos.avisoEnLaSala,
        cuerpo: Textos.seRecibioUnAviso,
        icono: Icons.info_outline,
        colorTexto: Tema.ambarPildora,
        colorFondo: Tema.ambarFondo,
        peligro: false,
      );
  }
}
