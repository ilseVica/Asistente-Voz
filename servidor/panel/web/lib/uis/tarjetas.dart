import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';

/// El encabezado: quién es y si la casa está viva.
class Cabecera extends StatelessWidget {
  const Cabecera({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;
    final TextTheme tipografia = Theme.of(contexto).textTheme;

    final bool viva = e != null && estado.conectadoAlServidor &&
        e.sitio.estado != desconectado;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: <Widget>[
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(Textos.asistenteDeVoz, style: tipografia.bodySmall),
              Text(Textos.salaPrincipal, style: tipografia.headlineMedium),
            ],
          ),
        ),
        _Pastilla(
          texto: !estado.conectadoAlServidor
              ? Textos.sinConexion
              : viva
                  ? Textos.conectada
                  : Textos.casaNoResponde,
          color: !estado.conectadoAlServidor
              ? Tema.peligro
              : viva
                  ? Tema.primario
                  : Color(0xFF8A6D1D),
          fondo: !estado.conectadoAlServidor
              ? const Color(0xFFF6E9E9)
              : viva
                  ? Tema.suave
                  : const Color(0xFFFBF3DE),
          vivo: estado.conectadoAlServidor && viva,
        ),
      ],
    );
  }
}

class _Pastilla extends StatelessWidget {
  const _Pastilla({
    required this.texto,
    required this.color,
    required this.fondo,
    required this.vivo,
  });

  final String texto;
  final Color color;
  final Color fondo;
  final bool vivo;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
          color: fondo, borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Container(
            width: 8,
            height: 8,
            decoration:
                BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 7),
          Text(texto,
              style: TextStyle(
                  color: color, fontWeight: FontWeight.w700, fontSize: 14)),
        ],
      ),
    );
  }
}

/// La tarjeta de la luz, con su interruptor.
///
/// Es la única tarjeta que manda cosas. Un `FilledButton` que refleja lo que se
/// sabe y no lo que se mando: mientras la orden está en vuelo, el botón sigue
/// diciendo «Apagar la luz» con el estado actual de al lado, y el cambio llega por
/// el WebSocket.
class TarjetaLuz extends StatelessWidget {
  const TarjetaLuz({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Luz luz = estado.estado?.luz ??
        const Luz(estado: sinDatos, encendida: null, origen: null,
            recibidoEn: null);
    final bool? encendida = luz.encendida;
    final String titulo = encendida == null
        ? Textos.sinDatos
        : encendida
            ? Textos.encendida
            : Textos.apagada;

    final String detalle = luz.estado == sinDatos
        ? Textos.todaviaNoHayLuz
        : '${origenDeLuz(luz.origen)} · '
            '${hace(luz.recibidoEn, ahora: estado.ahoraServidor())}';

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Icon(Icons.lightbulb_outline,
                    color: Tema.secundario, size: 20),
                const SizedBox(width: 8),
                Text(Textos.luzDeLaSala,
                    style: Theme.of(contexto)
                        .textTheme
                        .bodyMedium
                        ?.copyWith(
                            color: Tema.secundario,
                            fontWeight: FontWeight.w700)),
              ],
            ),
            const SizedBox(height: 12),
            Text(titulo,
                style: Theme.of(contexto).textTheme.headlineMedium),
            const SizedBox(height: 4),
            Text(detalle, style: Theme.of(contexto).textTheme.bodySmall),
            const SizedBox(height: 16),
            FilledButton(
              // Sin datos no se manda nada: apagar una luz de la que no se sabe ni
              // si existe es una orden a ciegas.
              onPressed: (encendida == null || estado.cargandoLuz)
                  ? null
                  : () =>
                      estado.encenderLuz(encender: !encendida),
              child: estado.cargandoLuz
                  ? const SizedBox(
                      height: 20,
                      width: 20,
                      child: CircularProgressIndicator(
                          strokeWidth: 2.5, color: Tema.tarjeta))
                  : Text(encendida == null
                      ? Textos.sinDatosDeLaLuz
                      : encendida
                          ? Textos.apagarLaLuz
                          : Textos.encenderLaLuz),
            ),
          ],
        ),
      ),
    );
  }
}

/// Temperatura, humedad y movimiento, cada uno en su recuadro.
class TarjetaAmbiente extends StatelessWidget {
  const TarjetaAmbiente({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Sensores s = estado.estado?.sensores ??
        const Sensores(estado: sinDatos, temperaturaC: null,
            humedadPct: null, movimiento: null, gasAdc: null,
            recibidoEn: null);
    final int ahora = estado.ahoraServidor();
    final String antiguedad = hace(s.recibidoEn, ahora: ahora);

    final List<_Dato> datos = <_Dato>[
      _Dato(
        rotulo: Textos.temperatura,
        valor: s.temperaturaC == null
            ? Textos.sinDatos
            : '${_num(s.temperaturaC)} °C',
        tamano: 26,
      ),
      _Dato(
        rotulo: Textos.humedad,
        valor: s.humedadPct == null ? Textos.sinDatos : '${_num(s.humedadPct)} %',
        tamano: 26,
      ),
      _Dato(
        rotulo: Textos.gas,
        valor: s.gasAdc == null ? Textos.sinDatos : Textos.noSeDetectaGas,
        tamano: 26,
        color: s.gasAdc == null ? Tema.secundario : Tema.primario,
      ),
      _Dato(
        rotulo: Textos.movimiento,
        valor: s.movimiento == null
            ? Textos.sinDatos
            : s.movimiento!
                ? Textos.ahoraMismo
                : Textos.noSeDetectaNadie,
        tamano: 18,
        subtitulo: s.recibidoEn == null ? null : antiguedad,
      ),
    ];

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(Textos.ambiente,
                style: Theme.of(contexto)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(
                        color: Tema.secundario,
                        fontWeight: FontWeight.w700)),
            const SizedBox(height: 12),
            LayoutBuilder(builder: (BuildContext contexto, BoxConstraints c) {
              final int columnas = c.maxWidth > 420 ? 4 : 2;
              final double ancho = (c.maxWidth - 12 * (columnas - 1)) / columnas;
              return Wrap(
                spacing: 12,
                runSpacing: 12,
                children: <Widget>[
                  for (final _Dato d in datos) SizedBox(width: ancho, child: d),
                ],
              );
            }),
            const SizedBox(height: 12),
            Text('${Textos.ultimaLectura}$antiguedad',
                style: Theme.of(contexto).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

class _Dato extends StatelessWidget {
  const _Dato({
    required this.rotulo,
    required this.valor,
    required this.tamano,
    this.color,
    this.subtitulo,
  });

  final String rotulo;
  final String valor;
  final double tamano;
  final Color? color;
  final String? subtitulo;

  @override
  Widget build(BuildContext contexto) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFFFAFBF9),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Tema.borde),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(rotulo, style: Theme.of(contexto).textTheme.bodySmall),
          const SizedBox(height: 4),
          Text(valor,
              style: TextStyle(
                  fontSize: tamano,
                  fontWeight: FontWeight.w700,
                  color: color ?? Tema.texto,
                  height: 1.15)),
          if (subtitulo != null) ...<Widget>[
            const SizedBox(height: 2),
            Text(subtitulo!,
                style: Theme.of(contexto).textTheme.bodySmall),
          ],
        ],
      ),
    );
  }
}

/// Los enlaces: nodo, sitio y satélite, cada uno con su última lectura.
class TarjetaEnlaces extends StatelessWidget {
  const TarjetaEnlaces({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;
    if (e == null) return const SizedBox.shrink();

    final int ahora = estado.ahoraServidor();

    final List<_Enlace> enlaces = <_Enlace>[
      _Enlace(Textos.sensoresYLuz, e.nodo),
      _Enlace(Textos.conexionConLaCasa, e.sitio),
      _Enlace(Textos.asistenteDeVoz, e.satelite),
    ];

    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        child: Column(
          children: <Widget>[
            for (final _Enlace enlace in enlaces)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Row(
                  children: <Widget>[
                    SizedBox(
                        width: 84,
                        child: Text(enlace.nombre,
                            style: Theme.of(contexto)
                                .textTheme
                                .bodyMedium
                                ?.copyWith(fontWeight: FontWeight.w700))),
                    Expanded(child: Text(enlace.texto(ahora),
                        style: Theme.of(contexto).textTheme.bodyMedium)),
                    _EstadoPunto(estado: enlace.componente.estado),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _Enlace {
  const _Enlace(this.nombre, this.componente);

  final String nombre;
  final Componente componente;

  /// `ahoraRef` se pasa de fuera y no se guarda: es el reloj del servidor, que se
  /// actualiza cada 30 s, y una copia dentro de esta clase se quedaría vieja.
  String texto(int ahoraRef) {
    if (componente.estado == noInstalado) return Textos.noSeUsaAqui;
    if (componente.estado == sinDatos) return Textos.sinDatos;
    if (componente.estado == desconectado) {
      return 'Sin conexión · ${hace(componente.recibidoEn, ahora: ahoraRef)}';
    }
    return 'Conectado · ${hace(componente.recibidoEn, ahora: ahoraRef)}';
  }
}

class _EstadoPunto extends StatelessWidget {
  const _EstadoPunto({required this.estado});

  final String estado;

  @override
  Widget build(BuildContext contexto) {
    final Color color;
    if (estado == conectado) {
      color = Tema.primario;
    } else if (estado == noInstalado) {
      color = const Color(0xFFB7BCB8);
    } else if (estado == desconectado) {
      color = Tema.peligro;
    } else {
      color = const Color(0xFF8A6D1D);
    }
    return Container(
      width: 10,
      height: 10,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }
}

/// El pie, con el enlace permanente al aviso de privacidad.
class PiePanel extends StatelessWidget {
  const PiePanel({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final TextTheme tipografia = Theme.of(contexto).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            Textos.piePrivacidad,
            style: tipografia.bodySmall,
          ),
          const SizedBox(height: 4),
          Row(
            children: <Widget>[
              TextButton(
                onPressed: estado.aceptarAviso,
                style: TextButton.styleFrom(
                    padding: EdgeInsets.zero,
                    minimumSize: const Size(0, 32),
                    foregroundColor: Tema.primario),
                child: Text(Textos.verAviso,
                    style: tipografia.bodySmall?.copyWith(
                        color: Tema.primario,
                        decoration: TextDecoration.underline)),
              ),
              const Spacer(),
              TextButton(
                onPressed: () => _salir(contexto, estado),
                style: TextButton.styleFrom(
                    padding: EdgeInsets.zero,
                    minimumSize: const Size(0, 32)),
                child: Text(Textos.cerrarSesion,
                    style: tipografia.bodySmall
                        ?.copyWith(color: Tema.secundario)),
              ),
            ],
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

String _num(num? valor) {
  if (valor == null) return '—';
  final double d = valor.toDouble();
  if (d == d.roundToDouble()) return d.toStringAsFixed(0);
  return d.toStringAsFixed(1);
}
