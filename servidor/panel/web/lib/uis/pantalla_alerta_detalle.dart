import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../presentacion_alerta.dart';
import '../textos.dart';
import 'navegacion.dart';

/// El detalle de una alerta: qué pasó, cuándo pasó y qué se hizo con ella.
///
/// Es la pantalla que el rojo de arriba y la lista de alertas abren (decisión
/// A.3). Sigue tres reglas del diseño:
///
///   - **El hero es rojo SOLO si la alerta sigue activa**: es una emergencia en
///     curso. Una alerta ya atendida o descartada lleva cabecera neutra con su
///     hora y, si la hay, el nombre de quien la atendió (decisión C.3).
///   - **«Qué pasó» enseña únicamente hechos reales** de `GET /api/alertas/{id}`
///     (decisión B.3): nada de líneas de tiempo inventadas.
///   - **Volver** es `cerrarAlerta()`, que vuelve a la pantalla de `destino` de
///     donde se vino sin perder el sitio en la navegación.
class PantallaAlertaDetalle extends StatelessWidget {
  const PantallaAlertaDetalle({super.key, required this.id});

  final String id;

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final AlertaDetalle? detalle = estado.detalleAlerta;

    if (detalle == null) {
      return EstructuraPanel(
        contenido: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            _BarraAtras(alVolver: estado.cerrarAlerta),
            const SizedBox(height: 24),
            if (estado.errorDetalle != null)
              _FalloDetalle(
                mensaje: estado.errorDetalle!,
                alReintentar: () => estado.abrirAlerta(id),
              )
            else
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 60),
                child: Center(
                  child: SizedBox(
                    height: 28,
                    width: 28,
                    child: CircularProgressIndicator(
                        strokeWidth: 3, color: Tema.primario),
                  ),
                ),
              ),
          ],
        ),
      );
    }

    final Alerta alerta = detalle.alerta;
    final bool activa = alerta.sinResolver;
    final Estado? e = estado.estado;

    // La frase de «qué pasó» con el umbral de gas tal como está hoy en el
    // servidor (el umbral no cambia en caliente, pero si la configuración
    // cambiara entre arranques, es el número que hoy significa «demasiado»).
    // Con la alerta activa la frase ya va dentro del hero rojo, así que la
    // tarjeta sólo se pinta cuando la alerta ya está resuelta.
    final String? quePaso = quePasoDe(alerta.tipo, alerta.carga,
        umbralGas: e?.gasOnAdc);

    return EstructuraPanel(
      contenido: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          _BarraAtras(alVolver: estado.cerrarAlerta),
          const SizedBox(height: 8),
          _Hero(alerta: alerta, activa: activa, detalle: detalle),
          const SizedBox(height: 16),
          if (activa) ...<Widget>[
            FilledButton(
              onPressed: estado.cargandoAlerta
                  ? null
                  : () => estado.atenderAlerta(alerta.id),
              style: FilledButton.styleFrom(
                backgroundColor: Tema.peligro,
                foregroundColor: Tema.tarjeta,
                minimumSize: const Size.fromHeight(56),
              ),
              child: estado.cargandoAlerta
                  ? const SizedBox(
                      height: 20,
                      width: 20,
                      child: CircularProgressIndicator(
                          strokeWidth: 2.5, color: Tema.tarjeta))
                  : const Text(Textos.yaLoAtendi,
                      style: TextStyle(fontWeight: FontWeight.w800)),
            ),
            const SizedBox(height: 10),
          ],
          OutlinedButton(
            onPressed: () {
              // «Ver el estado de la sala»: se cierra el detalle y se va a
              // Inicio, que es donde está el estado.
              estado.cerrarAlerta();
              estado.irA(Destino.inicio);
            },
            style: OutlinedButton.styleFrom(
              foregroundColor: Tema.primario,
              side: const BorderSide(color: Tema.primario),
              minimumSize: const Size.fromHeight(Tema.altoMinimo),
            ),
            child: const Text(Textos.verEstadoDeLaSala,
                style: TextStyle(fontWeight: FontWeight.w800)),
          ),
          if (activa) ...<Widget>[
            const SizedBox(height: 16),
            const _AvisoEmergencia(),
          ],
          if (!activa && quePaso != null) ...<Widget>[
            const SizedBox(height: 16),
            _QuePaso(frase: quePaso),
          ],
          const SizedBox(height: 16),
          _LineaTiempo(eventos: detalle.eventos),
        ],
      ),
    );
  }
}

/// La flecha de volver. Sólo eso: la navegación de abajo y la de arriba ya están
/// en el molde, así que el detalle no inventa una barra propia.
class _BarraAtras extends StatelessWidget {
  const _BarraAtras({required this.alVolver});

  final VoidCallback alVolver;

  @override
  Widget build(BuildContext contexto) {
    return Row(
      children: <Widget>[
        IconButton(
          onPressed: alVolver,
          tooltip: Textos.volver,
          icon: const Icon(Icons.arrow_back),
          color: Tema.texto,
          iconSize: 26,
          constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
        ),
        Text(Textos.volver,
            style: Theme.of(contexto)
                .textTheme
                .bodyMedium
                ?.copyWith(fontWeight: FontWeight.w700)),
      ],
    );
  }
}

/// La cabecera: roja mientras la alerta está activa, neutra cuando ya no.
class _Hero extends StatelessWidget {
  const _Hero({
    required this.alerta,
    required this.activa,
    required this.detalle,
  });

  final Alerta alerta;
  final bool activa;
  final AlertaDetalle detalle;

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final PresentacionAlerta pres = presentacionAlerta(
        alerta.tipo,
        origen: alerta.carga['origen']?.toString(),
        carga: alerta.carga);
    final String fecha =
        '${hace(alerta.creadoEn, ahora: estado.ahoraServidor())} · '
        '${fechaAlerta(alerta.creadoEn)}';

    if (!activa) {
      final String cuando = fechaAlerta(alerta.resueltaEn ?? alerta.creadoEn);
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Container(
                    width: 56,
                    height: 56,
                    decoration: BoxDecoration(
                        color: Tema.suave,
                        borderRadius: BorderRadius.circular(16)),
                    child: Icon(
                        alerta.estado == 'descartada'
                            ? Icons.block
                            : Icons.check,
                        color: Tema.primario,
                        size: 30),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(Textos.alertaEnLaSala,
                            style:
                                Theme.of(contexto).textTheme.bodySmall),
                        const SizedBox(height: 2),
                        Text(tituloAlerta(alerta.tipo),
                            style: Theme.of(contexto)
                                .textTheme
                                .headlineMedium),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Text(
                alerta.estado == 'descartada'
                    ? '${etiquetaEstadoAlerta(alerta.estado)} $cuando'
                    : atendidaCuando(cuando, alerta.resueltoPor),
                style: Theme.of(contexto)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(
                        color: Tema.primario, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              Text(fecha, style: Theme.of(contexto).textTheme.bodySmall),
            ],
          ),
        ),
      );
    }

    final Estado? e = estado.estado;
    final String? quePaso =
        quePasoDe(alerta.tipo, alerta.carga, umbralGas: e?.gasOnAdc);

    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;
    return Container(
      padding: EdgeInsets.all(movil ? 20 : 32),
      decoration: BoxDecoration(
        color: pres.colorFondo,
        borderRadius: BorderRadius.circular(movil ? Tema.radio : 18),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Container(
                width: movil ? 56 : 64,
                height: movil ? 56 : 64,
                decoration: BoxDecoration(
                  color: pres.colorFondo,
                  borderRadius: BorderRadius.circular(movil ? 16 : 18),
                  border: Border.all(
                      color: Tema.tarjeta.withValues(alpha: 0.6), width: 2),
                ),
                child: Icon(
                    pres.icono,
                    color: pres.colorTexto,
                    size: 30),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(Textos.alertaEnLaSala,
                        style: TextStyle(
                            color: pres.colorTexto.withValues(alpha: 0.85),
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                            fontFamily: Tema.familia)),
                    const SizedBox(height: 4),
                    Text(tituloAlerta(alerta.tipo),
                        style: TextStyle(
                            color: pres.colorTexto,
                            fontSize: movil ? 32 : 36,
                            height: 1.1,
                            fontWeight: FontWeight.w800,
                            fontFamily: Tema.familia)),
                  ],
                ),
              ),
            ],
          ),
          if (quePaso != null) ...<Widget>[
            const SizedBox(height: 14),
            Text(quePaso,
                style: TextStyle(
                    color: Tema.tarjeta.withValues(alpha: 0.95),
                    fontSize: movil ? 17 : 18,
                    height: 1.45,
                    fontFamily: Tema.familia)),
          ],
          const SizedBox(height: 10),
          Text(fecha,
              style: TextStyle(
                  color: Tema.tarjeta.withValues(alpha: 0.8),
                  fontSize: movil ? 15 : 16,
                  fontFamily: Tema.familia)),
        ],
      ),
    );
  }
}

/// El aviso ámbar de que, si nadie contesta, conviene llamar: es un consejo y
/// no un dato de la casa, y por eso el ámbar del panel y no el rojo.
class _AvisoEmergencia extends StatelessWidget {
  const _AvisoEmergencia();

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
          const Icon(Icons.phone_in_talk, color: Tema.ambarPildora, size: 22),
          const SizedBox(width: 12),
          Expanded(
            child: Text(Textos.avisoEmergencia,
                style: const TextStyle(
                    color: Tema.ambarTexto, fontSize: 15, height: 1.4)),
          ),
        ],
      ),
    );
  }
}

/// «Qué pasó»: la frase de la alerta, con su fondo claro, como en el prototipo.
class _QuePaso extends StatelessWidget {
  const _QuePaso({required this.frase});

  final String frase;

  @override
  Widget build(BuildContext contexto) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(Textos.quePaso,
                style: Theme.of(contexto)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(
                        color: Tema.secundario,
                        fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Text(frase, style: Theme.of(contexto).textTheme.bodyMedium),
          ],
        ),
      ),
    );
  }
}

/// La línea de tiempo, con los HECHOS que manda el servidor: creación, envíos,
/// atención y descarte, en su hora. Si no hay eventos no se pinta nada en vez
/// de enseñar una línea vacía.
class _LineaTiempo extends StatelessWidget {
  const _LineaTiempo({required this.eventos});

  final List<EventoAlerta> eventos;

  @override
  Widget build(BuildContext contexto) {
    if (eventos.isEmpty) return const SizedBox.shrink();
    final bool movil = MediaQuery.sizeOf(contexto).width < Tema.anchoTableta;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Text(Textos.quePaso,
                style: Theme.of(contexto)
                    .textTheme
                    .titleMedium),
            const SizedBox(height: 10),
            for (final EventoAlerta evento in eventos)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 7),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    SizedBox(
                      width: movil ? 72 : 90,
                      child: Text(
                        horaDelDia(DateTime.fromMillisecondsSinceEpoch(
                            evento.en * 1000)),
                        style: TextStyle(
                            fontSize: movil ? 15 : 16,
                            color: Tema.secundario,
                            fontWeight: FontWeight.w700,
                            fontFamily: Tema.familia),
                      ),
                    ),
                    Expanded(
                      child: Text(eventoAlerta(evento.tipo, evento.nombre),
                          style: TextStyle(
                              fontSize: movil ? 16 : 17,
                              fontFamily: Tema.familia)),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _FalloDetalle extends StatelessWidget {
  const _FalloDetalle({required this.mensaje, required this.alReintentar});

  final String mensaje;
  final VoidCallback alReintentar;

  @override
  Widget build(BuildContext contexto) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
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
            const SizedBox(height: 8),
            Text(Textos.noSePudoCargarLaAlerta,
                style: Theme.of(contexto).textTheme.bodySmall),
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
        ),
      ),
    );
  }
}
