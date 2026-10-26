import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../presentacion_alerta.dart';
import '../textos.dart';
import 'banner_alerta.dart';
import 'banner_sin_conexion.dart';

/// El molde de todas las pantallas con sesión: una cabecera y una barra, y
/// dentro, el contenido de cada pantalla.
///
/// Antes cada pantalla montaba su propia barra lateral (escritorio) o su propia
/// barra inferior (móvil), y las cinco eran casi el mismo código escrito cinco
/// veces: al añadir «Familiares» habría sido seis. Aquí la navegación existe una
/// sola vez y cada pantalla aporta sólo lo suyo. Las reglas (decisión A.1 y B):
///
///   - **900 px o más**: cabecera con el título, la píldora de conexión y las
///     cinco pestañas Inicio / Alertas / Recordatorios / Mensajes / Familiares.
///   - **Menos de 900 px**: arriba el título con la píldora y el ícono de
///     Familiares (que no cabe en la barra de abajo: allí sólo caben cuatro
///     íconos, Inicio, Alertas, Recordatorios y Mensajes), y abajo esa barra de
///     cuatro.
///
/// Los dos carteles (alerta activa y sin conexión) y el aviso de una sola vez
/// también viven aquí: aparecen en TODAS las pantallas, que es donde tiene
/// sentido verlos, y así ninguna pantalla se olvida de ellos.
class EstructuraPanel extends StatelessWidget {
  const EstructuraPanel({super.key, required this.contenido});

  /// El cuerpo de la pantalla, ya como `Column`: los carteles y el aviso van
  /// encima, y el desplazamiento es del molde y no de cada pantalla.
  final Widget contenido;

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final bool escritorio =
        MediaQuery.sizeOf(contexto).width >= Tema.anchoEscritorio;

    final Widget cuerpo = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        const BannerAlertaActiva(),
        const BannerSinConexion(),
        if (estado.aviso != null) ...<Widget>[
          AvisoPanel(texto: estado.aviso!, alCerrar: estado.limpiarAviso),
          const SizedBox(height: 16),
        ],
        contenido,
      ],
    );

    if (escritorio) {
      return Scaffold(
        body: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            const CabeceraEscritorio(),
            Expanded(
              child: Container(
                color: Tema.fondo,
                child: SingleChildScrollView(
                  padding: const EdgeInsets.fromLTRB(24, 32, 24, 48),
                  child: Center(
                    child: ConstrainedBox(
                      key: const ValueKey<String>('contenido-panel'),
                      constraints: const BoxConstraints(maxWidth: 1200),
                      child: cuerpo,
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      );
    }

    return Scaffold(
      body: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            const CabeceraMovil(),
            Expanded(
              child: SingleChildScrollView(
                padding: MediaQuery.sizeOf(contexto).width >= Tema.anchoTableta
                    ? const EdgeInsets.fromLTRB(32, 24, 32, 24)
                    : const EdgeInsets.fromLTRB(20, 16, 20, 24),
                child: cuerpo,
              ),
            ),
          ],
        ),
      ),
      bottomNavigationBar: const BarraInferior(),
    );
  }
}

/// La píldora de estado de la casa: conectada, sin conexión o alerta activa.
///
/// Es la misma en escritorio y en móvil.
class PastillaConexion extends StatelessWidget {
  const PastillaConexion({super.key, this.compacta = false});

  /// La de arriba del móvil, un poco más justa de espacio.
  final bool compacta;

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;
    final Alerta? alertaActiva = estado.alertaMasUrgente;

    final String texto;
    final Color color;
    final Color fondo;

    if (alertaActiva != null) {
      final PresentacionAlerta pres = presentacionAlerta(
          alertaActiva.tipo,
          origen: alertaActiva.carga['origen']?.toString(),
          carga: alertaActiva.carga);
      texto = pres.pildora;
      color = pres.colorTexto;
      fondo = pres.colorFondo;
    } else if (!estado.conectadoAlServidor) {
      texto = compacta ? Textos.sinConexion : Textos.sinConexionSala;
      color = Tema.ambarPildora;
      fondo = Tema.ambarFondo;
    } else {
      final bool viva = e != null && e.sitio.estado != desconectado;
      if (viva) {
        texto = compacta ? Textos.conectada : Textos.salaConectada;
        color = Tema.primario;
        fondo = Tema.suave;
      } else {
        texto = compacta ? Textos.sinConexion : Textos.sinConexionSala;
        color = Tema.ambarPildora;
        fondo = Tema.ambarFondo;
      }
    }

    return Container(
      padding: compacta
          ? const EdgeInsets.symmetric(horizontal: 10, vertical: 6)
          : const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration:
          BoxDecoration(color: fondo, borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Container(
            width: compacta ? 8 : 10,
            height: compacta ? 8 : 10,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          SizedBox(width: compacta ? 6 : 8),
          Text(texto,
              style: TextStyle(
                  color: color,
                  fontWeight: FontWeight.w700,
                  fontSize: compacta ? 14 : 16)),
        ],
      ),
    );
  }
}

/// La cabecera de escritorio: quién es, cómo está la casa y las cuatro pestañas.
class CabeceraEscritorio extends StatelessWidget {
  const CabeceraEscritorio({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final TextTheme tipografia = Theme.of(contexto).textTheme;
    final int ahora = estado.ahoraLocal();
    final String actualizado =
        estado.ultimoMensajeVivo == null ? '' : haceCorto(estado.ultimoMensajeVivo, ahora: ahora);

    return Container(
      color: Tema.fondo,
      padding: const EdgeInsets.fromLTRB(24, 32, 24, 0),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1200),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: <Widget>[
                  Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                        color: Tema.primario,
                        borderRadius: BorderRadius.circular(14)),
                    child: const Icon(Icons.graphic_eq,
                        color: Tema.tarjeta, size: 26),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(Textos.asistenteDeVoz, style: tipografia.bodyMedium),
                        Text(Textos.salaPrincipal,
                            style: const TextStyle(
                                fontSize: 30, fontWeight: FontWeight.w700,
                                color: Tema.texto, height: 1.15,
                                fontFamily: Tema.familia)),
                      ],
                    ),
                  ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: <Widget>[
                  const PastillaConexion(),
                    if (actualizado.isNotEmpty) ...<Widget>[
                      const SizedBox(height: 4),
                      Text('Actualizado $actualizado',
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                              fontSize: 15, color: Tema.secundario,
                              fontFamily: Tema.familia)),
                    ],
                  ],
                ),
                ],
              ),
              const SizedBox(height: 20),
              Container(
                padding: const EdgeInsets.only(bottom: 12),
                decoration: const BoxDecoration(
                    border: Border(bottom: BorderSide(color: Tema.borde))),
                child: Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: <Widget>[
                    _Pestana(
                      destino: Destino.inicio,
                      rotulo: Textos.panel,
                      cuenta: 0,
                    ),
                    _Pestana(
                      destino: Destino.alertas,
                      rotulo: Textos.historialDeAlertas,
                      cuenta: estado.estado?.alertasSinResolver ?? 0,
                    ),
                    _Pestana(
                      destino: Destino.recordatorios,
                      rotulo: Textos.recordatorios,
                      cuenta: 0,
                    ),
                    _Pestana(
                      destino: Destino.familiares,
                      rotulo: Textos.familiares,
                      cuenta: 0,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 4),
            ],
          ),
        ),
      ),
    );
  }
}

/// Una pestaña de la cabecera. La activa es el negro con texto blanco del
/// prototipo; las demás son texto llano. El contador de alertas sin resolver va
/// en un punto rojo junto a «Alertas», que es lo único que merece ruido.
class _Pestana extends StatelessWidget {
  const _Pestana({
    required this.destino,
    required this.rotulo,
    required this.cuenta,
  });

  final Destino destino;
  final String rotulo;
  final int cuenta;

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final bool sel = estado.destino == destino;

    return InkWell(
      onTap: () => estado.irA(destino),
      borderRadius: BorderRadius.circular(10),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        decoration: BoxDecoration(
          color: sel ? Tema.texto : Colors.transparent,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(rotulo,
                style: TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                    color: sel ? Tema.tarjeta : Tema.texto)),
            if (cuenta > 0) ...<Widget>[
              const SizedBox(width: 6),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: const BoxDecoration(
                    color: Tema.peligro, shape: BoxShape.circle),
                child: Text('$cuenta',
                    style: const TextStyle(
                        color: Tema.tarjeta,
                        fontSize: 11,
                        fontWeight: FontWeight.w700)),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// La cabecera de móvil: título y píldora.
class CabeceraMovil extends StatelessWidget {
  const CabeceraMovil({super.key});

  @override
  Widget build(BuildContext contexto) {
    final TextTheme tipografia = Theme.of(contexto).textTheme;

    final bool tableta = MediaQuery.sizeOf(contexto).width >= Tema.anchoTableta;
    return Container(
      color: Tema.fondo,
      padding: tableta
          ? const EdgeInsets.fromLTRB(32, 24, 24, 16)
          : const EdgeInsets.fromLTRB(20, 14, 12, 12),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(Textos.asistenteDeVoz,
                    style: tableta ? tipografia.bodyMedium : tipografia.bodySmall),
                Text(Textos.salaPrincipal,
                    style: tableta
                        ? const TextStyle(
                            fontSize: 30, fontWeight: FontWeight.w700,
                            color: Tema.texto, height: 1.15,
                            fontFamily: Tema.familia)
                        : tipografia.headlineMedium),
              ],
            ),
          ),
          PastillaConexion(compacta: !tableta),
        ],
      ),
    );
  }
}

/// La barra inferior de móvil: Inicio, Alertas, Recordatorios, Familiares.
class BarraInferior extends StatelessWidget {
  const BarraInferior({super.key});

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
                cuenta: estado.estado?.alertasSinResolver ?? 0,
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
                icono: Icons.people_outline,
                activo: Icons.people,
                rotulo: Textos.familiares,
                seleccionado: estado.destino == Destino.familiares,
                cuenta: 0,
                alPulsar: () => estado.irA(Destino.familiares),
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
              // «Recordatorios» no cabe siempre en una celda a 390 px: con
              // FittedBox se achica la letra en vez de partirse en dos líneas,
              // que es lo que hacía el contenedor saltar por 13 px.
              FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(rotulo,
                    maxLines: 1,
                    style: TextStyle(
                        color: color,
                        fontSize: 13,
                        fontWeight: FontWeight.w700)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// El aviso de una sola vez (un error, un «luz enviada»): informativo y con su
/// botón de cerrar. Vive en el molde para que se vea desde cualquier pantalla.
class AvisoPanel extends StatelessWidget {
  const AvisoPanel({super.key, required this.texto, required this.alCerrar});

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
              icon: const Icon(Icons.close, color: Tema.primario, size: 20),
              tooltip: Textos.cancelar),
        ],
      ),
    );
  }
}
