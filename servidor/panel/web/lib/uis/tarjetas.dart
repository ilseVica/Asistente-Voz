import 'package:flutter/material.dart';

import '../main.dart';
import '../modelos.dart';
import '../tema.dart';
import '../textos.dart';

/// La tarjeta de la luz, con su interruptor.
class TarjetaLuz extends StatelessWidget {
  const TarjetaLuz({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Luz luz = estado.estado?.luz ??
        const Luz(estado: sinDatos, encendida: null, origen: null, recibidoEn: null);
    final bool? encendida = luz.encendida;
    final String titulo = encendida == null
        ? Textos.sinDatos
        : encendida
            ? Textos.encendida
            : Textos.apagada;

    String detalle;
    if (luz.estado == sinDatos && encendida == null) {
      detalle = Textos.todaviaNoHayLuz;
    } else if (luz.recibidoEn == null && luz.origen != null) {
      detalle = '${titulo.toLowerCase()} ${origenDeLuz(luz.origen).toLowerCase()}';
    } else if (luz.origen != null) {
      detalle =
          '${origenDeLuz(luz.origen)} · ${hace(luz.recibidoEn, ahora: estado.ahoraServidor())}';
    } else {
      detalle = titulo;
    }

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
                            color: Tema.secundario, fontWeight: FontWeight.w700)),
              ],
            ),
            const SizedBox(height: 12),
            Text(titulo, style: Theme.of(contexto).textTheme.headlineMedium),
            const SizedBox(height: 4),
            Text(detalle, style: Theme.of(contexto).textTheme.bodySmall),
            const SizedBox(height: 16),
            FilledButton(
              style: FilledButton.styleFrom(
                  minimumSize: const Size.fromHeight(Tema.altoMinimo)),
              onPressed: (encendida == null || estado.cargandoLuz)
                  ? null
                  : () => estado.encenderLuz(encender: !encendida),
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

/// Temperatura y humedad, como en el prototipo.
class TarjetaAmbiente extends StatelessWidget {
  const TarjetaAmbiente({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Sensores s = estado.estado?.sensores ??
        const Sensores(
            estado: sinDatos,
            temperaturaC: null,
            humedadPct: null,
            movimiento: null,
            gasAdc: null,
            recibidoEn: null);
    final int ahora = estado.ahoraServidor();
    final bool nodoConectado = estado.estado?.nodo.estado == conectado;

    final String temp = datoDeSensor(
        nodoConectado: nodoConectado,
        valor: s.temperaturaC,
        conValor: () => '${_num(s.temperaturaC)} °C');
    final String hum = datoDeSensor(
        nodoConectado: nodoConectado,
        valor: s.humedadPct,
        conValor: () => '${_num(s.humedadPct)} %');

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Icon(Icons.thermostat, color: Tema.secundario, size: 20),
                const SizedBox(width: 8),
                Text(Textos.ambiente,
                    style: Theme.of(contexto)
                        .textTheme
                        .bodyMedium
                        ?.copyWith(
                            color: Tema.secundario, fontWeight: FontWeight.w700)),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(temp,
                          style: const TextStyle(
                              fontSize: 34, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 4),
                      Text(Textos.temperatura,
                          style: Theme.of(contexto).textTheme.bodySmall),
                    ],
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(hum,
                          style: const TextStyle(
                              fontSize: 34, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 4),
                      Text(Textos.humedad,
                          style: Theme.of(contexto).textTheme.bodySmall),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (s.recibidoEn != null)
              Text('Actualizado ${haceCorto(s.recibidoEn, ahora: ahora)}',
                  style: Theme.of(contexto).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

/// El nivel del gas en palabras, con su color.
///
/// Tres escalones contra `gas_on_adc` (el umbral que manda el servidor por
/// entorno): por debajo de tres cuartas partes, «Normal»; de ahí al umbral,
/// «Gas elevado» (el ámbar del aviso); en o por encima del umbral, «Gas
/// detectado» (el rojo de la alerta). Con un umbral no se puede juzgar nada y
/// se enseña sólo la lectura.
///
/// Funciones puras para poder probar la regla sin montar la tarjeta.
String estadoDelGas({required num? gasAdc, required num? umbral}) {
  if (gasAdc == null) return Textos.sinDatos;
  if (umbral == null) return Textos.gasSinUmbral;
  if (gasAdc >= umbral) return Textos.gasDetectado;
  if (gasAdc >= umbral * 0.75) return Textos.gasElevado;
  return Textos.gasNormal;
}

Color colorDelGas({required num? gasAdc, required num? umbral}) {
  final String estado =
      estadoDelGas(gasAdc: gasAdc, umbral: umbral);
  if (estado == Textos.gasDetectado) return Tema.peligro;
  if (estado == Textos.gasElevado) return Tema.ambarPildora;
  if (estado == Textos.sinDatos || estado == Textos.gasSinUmbral) {
    return Tema.secundario;
  }
  return Tema.primario;
}

/// La barra de gas: la lectura contra la escala completa del ADC de 12 bits,
/// con el umbral marcado en su sitio (decisión A.3: el panel expone el umbral
/// y se ve dónde está, no sólo un «Normal» sin explicación).
class BarraGas extends StatelessWidget {
  const BarraGas({super.key, required this.gasAdc, required this.umbral});

  final num? gasAdc;
  final num? umbral;

  @override
  Widget build(BuildContext contexto) {
    final double lectura =
        gasAdc == null ? 0 : gasAdc!.toDouble() / Tema.escalaAdc;
    final double anchoUmbral =
        umbral == null ? 0 : umbral!.toDouble() / Tema.escalaAdc;

    return SizedBox(
      height: 10,
      child: LayoutBuilder(
        builder: (BuildContext contexto, BoxConstraints c) {
          final double lleno = c.maxWidth * lectura.clamp(0.0, 1.0);
          final double marca = c.maxWidth * anchoUmbral.clamp(0.0, 1.0);
          return Stack(
            clipBehavior: Clip.none,
            children: <Widget>[
              Container(
                decoration: BoxDecoration(
                    color: Tema.caja,
                    borderRadius: BorderRadius.circular(6)),
              ),
              if (gasAdc != null)
                Align(
                  alignment: Alignment.centerLeft,
                  child: Container(
                    width: lleno < 10 ? 10 : lleno,
                    decoration: BoxDecoration(
                        color: colorDelGas(gasAdc: gasAdc, umbral: umbral),
                        borderRadius: BorderRadius.circular(6)),
                  ),
                ),
              if (umbral != null && anchoUmbral > 0 && anchoUmbral < 1)
                Positioned(
                  left: marca - 1,
                  top: -3,
                  bottom: -3,
                  child: Container(
                    width: 2,
                    color: Tema.ambarTexto,
                  ),
                ),
            ],
          );
        },
      ),
    );
  }
}

/// La tarjeta de gas, que es la pieza con más cuidado del panel: dice el estado
/// con color, enseña la lectura contra el umbral del servidor y la barra con la
/// marca donde está ese umbral.
class TarjetaGas extends StatelessWidget {
  const TarjetaGas({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Sensores s = estado.estado?.sensores ??
        const Sensores(
            estado: sinDatos,
            temperaturaC: null,
            humedadPct: null,
            movimiento: null,
            gasAdc: null,
            recibidoEn: null);
    final bool nodoConectado = estado.estado?.nodo.estado == conectado;
    final num? umbral = estado.estado?.gasOnAdc;
    final num? gasAdc = s.gasAdc;

    String valor;
    if (!nodoConectado) {
      valor = Textos.sinDatos;
    } else if (gasAdc == null) {
      valor = Textos.sinSensor;
    } else {
      valor = estadoDelGas(gasAdc: gasAdc, umbral: umbral);
    }
    final bool hayDatoNum = nodoConectado && gasAdc != null;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Icon(Icons.local_gas_station,
                    color: Tema.secundario, size: 20),
                const SizedBox(width: 8),
                Text(Textos.gas,
                    style: Theme.of(contexto)
                        .textTheme
                        .bodyMedium
                        ?.copyWith(
                            color: Tema.secundario, fontWeight: FontWeight.w700)),
              ],
            ),
            const SizedBox(height: 12),
            Text(valor,
                style: Theme.of(contexto).textTheme.headlineMedium?.copyWith(
                    color: hayDatoNum
                        ? colorDelGas(gasAdc: gasAdc, umbral: umbral)
                        : Tema.secundario)),
            if (hayDatoNum) ...<Widget>[
              const SizedBox(height: 12),
              BarraGas(gasAdc: gasAdc, umbral: umbral),
              const SizedBox(height: 12),
              Text(
                  '${Textos.lectura}${_num(gasAdc)}'
                  '${umbral == null ? '' : ' · ${Textos.umbral}${_num(umbral)}'}',
                  style: Theme.of(contexto).textTheme.bodySmall),
            ],
          ],
        ),
      ),
    );
  }
}

/// La tarjeta de movimiento: si hay alguien ahora, cuándo fue la última vez que
/// lo hubo, y el aviso por inactividad con su acceso al diálogo.
class TarjetaMovimiento extends StatelessWidget {
  const TarjetaMovimiento({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;
    final Sensores s = e?.sensores ??
        const Sensores(
            estado: sinDatos,
            temperaturaC: null,
            humedadPct: null,
            movimiento: null,
            gasAdc: null,
            recibidoEn: null);
    final bool nodoConectado = e?.nodo.estado == conectado;

    String valor;
    if (!nodoConectado) {
      valor = Textos.sinDatos;
    } else if (s.movimiento == null) {
      valor = Textos.sinSensor;
    } else if (s.movimiento!) {
      valor = Textos.movimientoDetectado;
    } else {
      valor = Textos.sinMovimiento;
    }

    final Inactividad? inact = e?.inactividad;
    final bool sinReloj = inact == null ||
        inact.ultimoMovimiento == null ||
        !nodoConectado ||
        e?.nodo.estado == desconectado;
    final String? ultimoLinea = sinReloj
        ? null
        : '${Textos.ultimoMovimiento} ${hace(inact.ultimoMovimiento!, ahora: estado.ahoraServidor())}';

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Icon(Icons.directions_walk,
                    color: Tema.secundario, size: 20),
                const SizedBox(width: 8),
                Text(Textos.movimiento,
                    style: Theme.of(contexto)
                        .textTheme
                        .bodyMedium
                        ?.copyWith(
                            color: Tema.secundario, fontWeight: FontWeight.w700)),
              ],
            ),
            const SizedBox(height: 12),
            Text(valor, style: Theme.of(contexto).textTheme.headlineMedium),
            if (ultimoLinea != null) ...<Widget>[
              const SizedBox(height: 4),
              Text(ultimoLinea, style: Theme.of(contexto).textTheme.bodySmall),
            ],
            const SizedBox(height: 16),
            if (inact != null)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton(
                  onPressed: () => mostrarDialogoInactividad(contexto),
                  style: TextButton.styleFrom(
                      foregroundColor: Tema.primario,
                      padding: EdgeInsets.zero,
                      minimumSize: const Size(0, 48)),
                  child: Text(avisoInactividadAlos(inact.tiempoMin),
                      style: Theme.of(contexto)
                          .textTheme
                          .bodySmall
                          ?.copyWith(fontWeight: FontWeight.w700)),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Abre el diálogo del aviso por inactividad desde cualquier pantalla.
Future<void> mostrarDialogoInactividad(BuildContext contexto) {
  return showDialog<void>(
    context: contexto,
    builder: (BuildContext contexto) => const DialogoInactividad(),
  );
}

/// El aviso por inactividad: cuándo se movió alguien por última vez y el ajuste.
///
/// Es el único número que guarda el panel, y lo guarda con un campo de texto y no
/// con un deslizador: la franja va de 2 a 720 y 719 pasos no se afinan con el
/// dedo. El error se enseña debajo del campo, en la misma pantalla, y la franja
/// la manda el servidor, así que aquí no hay números escritos a mano. Antes era
/// una tarjeta de la fila de escritorio; ahora se abre con «Cambiar», que es
/// donde de verdad se toca (decisión B y E).
class DialogoInactividad extends StatefulWidget {
  const DialogoInactividad({super.key});

  @override
  State<DialogoInactividad> createState() => _DialogoInactividadState();
}

class _DialogoInactividadState extends State<DialogoInactividad> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  int? _editado;

  String? _validar(String? valor, Inactividad inact) {
    final int? minutos = int.tryParse((valor ?? '').trim());
    if (minutos == null) return Textos.soloNumerosDeMinutos;
    if (minutos < inact.minimo || minutos > inact.maximo) {
      return minutosFueraDeRango(inact.minimo, inact.maximo);
    }
    return null;
  }

  Future<void> _guardar(EstadoPanel estado) async {
    // `validate()` corre los validadores y, si no hay error, `save()` deja el número
    // en `_editado`. Nada de esto toca la red hasta que el número es bueno.
    if (!(_formKey.currentState?.validate() ?? false)) return;
    _formKey.currentState!.save();
    if (_editado == null) return;
    await estado.guardarInactividad(_editado!);
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;
    if (e == null) return const SizedBox.shrink();
    final Inactividad inact = e.inactividad;

    return AlertDialog(
      title: const Text(Textos.dialogoInactividad),
      content: SizedBox(
        width: 420,
        child: Form(
          key: _formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                  '${avisoInactividadAlos(inact.tiempoMin)} '
                  '(${inact.minimo}–${inact.maximo} ${Textos.minutosUnidad})',
                  style: Theme.of(contexto).textTheme.bodySmall),
              const SizedBox(height: 16),
              TextFormField(
                // La clave lleva el valor del servidor: cuando se guarda (o
                // cambia en otro dispositivo), el campo se reinicia con el
                // número que confirmó el servidor en vez de quedarse con lo
                // que se había teclado.
                key: ValueKey<int>(inact.tiempoMin),
                initialValue: '${inact.tiempoMin}',
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: Textos.avisarSiNoHayMovimiento,
                  suffixText: Textos.minutosUnidad,
                ),
                validator: (String? valor) => _validar(valor, inact),
                onSaved: (String? valor) =>
                    _editado = int.tryParse((valor ?? '').trim()),
                onFieldSubmitted: (_) => _guardar(estado),
              ),
            ],
          ),
        ),
      ),
      actions: <Widget>[
        TextButton(
            onPressed: () => Navigator.of(contexto).pop(),
            child: const Text(Textos.cancelar)),
        FilledButton(
          onPressed:
              estado.cargandoInactividad ? null : () => _guardar(estado),
          child: estado.cargandoInactividad
              ? const SizedBox(
                  height: 20,
                  width: 20,
                  child: CircularProgressIndicator(
                      strokeWidth: 2.5, color: Tema.tarjeta))
              : const Text(Textos.guardarInactividad),
        ),
      ],
    );
  }
}

/// Los equipos de la casa: nodo, asistente y servidor, cada uno con su estado y
/// su última lectura. Antes se llamaba «Enlaces» y se le había caído el servidor
/// de la lista, que es justo lo que un familiar quiere ver cuando nada responde.
class TarjetaEquipos extends StatelessWidget {
  const TarjetaEquipos({super.key});

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    final Estado? e = estado.estado;
    if (e == null) return const SizedBox.shrink();

    final int ahora = estado.ahoraServidor();

    final List<_Enlace> enlaces = <_Enlace>[
      _Enlace(Textos.nodoDeLaSala, e.nodo),
      _Enlace(Textos.asistenteDeVoz, e.satelite),
    ];

    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
        child: Column(
          children: <Widget>[
            for (final _Enlace enlace in enlaces)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Row(
                  children: <Widget>[
                    SizedBox(
                        width: 110,
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
            // El servidor no es un `Componente` del sitio: se sabe con el
            // WebSocket. Igual que en la cabecera, su aviso es ámbar y no rojo.
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 10),
              child: Row(
                children: <Widget>[
                  SizedBox(
                      width: 110,
                      child: Text(Textos.servidor,
                          style: Theme.of(contexto)
                              .textTheme
                              .bodyMedium
                              ?.copyWith(fontWeight: FontWeight.w700))),
                  Expanded(
                      child: Text(
                          estado.conectadoAlServidor
                              ? Textos.enLinea
                              : Textos.sinConexion,
                          style:
                              Theme.of(contexto).textTheme.bodyMedium)),
                  _EstadoPunto(
                      estado: estado.conectadoAlServidor
                          ? conectado
                          : desconectado),
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
      color = Tema.ambarPildora;
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
          LayoutBuilder(builder: (BuildContext context, BoxConstraints c) {
            return Row(
              children: <Widget>[
                Flexible(
                  child: TextButton(
                    onPressed: estado.aceptarAviso,
                    style: TextButton.styleFrom(
                        padding: EdgeInsets.zero,
                        minimumSize: const Size(0, 32),
                        foregroundColor: Tema.primario),
                    child: Text(Textos.verAviso,
                        overflow: TextOverflow.ellipsis,
                        style: tipografia.bodySmall?.copyWith(
                            color: Tema.primario,
                            decoration: TextDecoration.underline)),
                  ),
                ),
                Flexible(
                  child: TextButton(
                    onPressed: () => _salir(contexto, estado),
                    style: TextButton.styleFrom(
                        padding: EdgeInsets.zero,
                        minimumSize: const Size(0, 32),
                        foregroundColor: Tema.secundario),
                    child: Text(Textos.cerrarSesion,
                        overflow: TextOverflow.ellipsis,
                        style: tipografia.bodySmall
                            ?.copyWith(color: Tema.secundario)),
                  ),
                ),
              ],
            );
          }),
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

/// El texto de un dato de ambiente cuando el valor puede faltar.
///
/// Distingue dos cosas que no son lo mismo:
///
///   - «Sin datos»: no se sabe nada del nodo (desconectado, o todavía no ha
///     llegado ningún mensaje). Que el campo venga `null` aquí no dice nada del
///     sensor, dice que no hay con quién hablar.
///   - «Sin sensor»: el nodo SÍ está conectado y ha mandado el campo como `null`.
///     Es lo que manda el firmware cuando el DHT22 falla tres veces, el MQ-2
///     calienta o la bandera del sensor está en false: el nodo está vivo y dice
///     que ese sensor no tiene dato, que es una información distinta y más útil.
///
/// Con el nodo conectado y el valor presente, devuelve el valor ya formateado con
/// [conValor]. Es una función pura para poder probarla sin montar la interfaz.
String datoDeSensor({
  required bool nodoConectado,
  required num? valor,
  required String Function() conValor,
}) {
  if (valor != null) return conValor();
  return nodoConectado ? Textos.sinSensor : Textos.sinDatos;
}
