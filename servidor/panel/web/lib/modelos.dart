/// Lo que la API devuelve, tal cual.
///
/// Nada de estas clases se serializa con `json_serializable`: son un `fromJson` a
/// mano y nada más. Un generador añadiría una fase de build, un paquete de más y
/// código generado que hay que volver a mirar, y todo el contrato que aquí se
/// necesita cabe en lo que se ve.
library;

import 'textos.dart';

/// Los estados que devuelve `estado.py`.
const String conectado = 'conectado';
const String desconectado = 'desconectado';
const String noInstalado = 'no_instalado';
const String sinDatos = 'sin_datos';

/// Un `null` en cualquier campo numérico significa «no lo sé», y no «cero».
///
/// Es la distinción que sostiene el panel entero: un sensor sin datos pone
/// «sin datos» y no `0.0 °C`, porque pintar cero grados es decir una mentira con
/// un número pequeño.

class Luz {
  const Luz({
    required this.estado,
    required this.encendida,
    required this.origen,
    required this.recibidoEn,
  });

  final String estado;
  final bool? encendida;
  final String? origen;
  final int? recibidoEn;

  factory Luz.desde(Map<String, dynamic> json) => Luz(
        estado: json['estado'] as String? ?? sinDatos,
        encendida: json['encendida'] as bool?,
        origen: json['origen'] as String?,
        recibidoEn: json['recibido_en'] as int?,
      );
}

class Sensores {
  const Sensores({
    required this.estado,
    required this.temperaturaC,
    required this.humedadPct,
    required this.movimiento,
    required this.gasAdc,
    required this.recibidoEn,
  });

  final String estado;
  final num? temperaturaC;
  final num? humedadPct;
  final bool? movimiento;
  final num? gasAdc;
  final int? recibidoEn;

  factory Sensores.desde(Map<String, dynamic> json) => Sensores(
        estado: json['estado'] as String? ?? sinDatos,
        temperaturaC: json['temperatura_c'] as num?,
        humedadPct: json['humedad_pct'] as num?,
        movimiento: json['movimiento'] as bool?,
        gasAdc: json['gas_adc'] as num?,
        recibidoEn: json['recibido_en'] as int?,
      );
}

/// Un componente que sólo dice si está vivo y cuándo se le vio: nodo, satélite y
/// sitio. Los tres tienen la misma forma en la API y por eso comparten clase.
class Componente {
  const Componente({required this.estado, this.recibidoEn, this.fuente});

  final String estado;
  final int? recibidoEn;

  /// Sólo lo lleva el bloque del sitio (`nodo` o `puente`). Dice de dónde sale la
  /// respuesta, y no es lo mismo que la ruta del propio sitio, que siempre existe.
  final String? fuente;

  factory Componente.desde(Map<String, dynamic> json) => Componente(
        estado: json['estado'] as String? ?? sinDatos,
        recibidoEn: json['recibido_en'] as int?,
        fuente: json['fuente'] as String?,
      );
}

/// Las cuatro estados de una alerta, tal como los calcula `db.CASE_ESTADO`.
///
/// El estado lo decide el servidor y no la interfaz: si el botón de Telegram y el
/// del panel calcularan cada uno el suyo, los dos podrían mostrar cosas distintas
/// de la misma alerta.
class Alerta {
  const Alerta({
    required this.id,
    required this.tipo,
    required this.estado,
    required this.creadoEn,
    required this.carga,
    required this.resueltaEn,
    this.enviados,
    this.sinMandar,
  });

  final String id;
  final String tipo;
  final String estado;
  final int creadoEn;
  final Map<String, dynamic> carga;
  final int? resueltaEn;
  final int? enviados;
  final int? sinMandar;

  bool get sinResolver => estado == 'pendiente' || estado == 'enviada';

  factory Alerta.desde(Map<String, dynamic> json) => Alerta(
        id: json['id'] as String,
        tipo: json['tipo'] as String,
        estado: json['estado'] as String,
        creadoEn: json['creado_en'] as int,
        carga: (json['carga'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{},
        resueltaEn: json['resuelta_en'] as int?,
        enviados: json['enviados'] as int?,
        sinMandar: json['sin_mandar'] as int?,
      );
}

/// La instantánea completa de `GET /api/estado`, apartado 4.2.
class Estado {
  const Estado({
    required this.servidorHora,
    required this.luz,
    required this.sensores,
    required this.nodo,
    required this.satelite,
    required this.sitio,
    required this.modo,
    required this.alertas,
    required this.familiares,
    required this.conexiones,
  });

  final int servidorHora;
  final Luz luz;
  final Sensores sensores;
  final Componente nodo;
  final Componente satelite;
  final Componente sitio;
  final String modo;
  final Map<String, int> alertas;
  final int familiares;
  final int conexiones;

  /// Cuántas alertas no están resueltas ni descartadas. El servidor manda los
  /// cuatro conteos y el total se saca aquí, porque es una cuenta y no una regla.
  int get alertasSinResolver {
    int total = 0;
    alertas.forEach((clave, valor) {
      if (clave == 'pendiente' || clave == 'enviada') total += valor;
    });
    return total;
  }

  factory Estado.desde(Map<String, dynamic> json) => Estado(
        servidorHora: json['servidor_hora'] as int? ?? 0,
        luz: Luz.desde((json['luz'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{}),
        sensores: Sensores.desde(
            (json['sensores'] as Map?)?.cast<String, dynamic>() ??
                const <String, dynamic>{}),
        nodo: Componente.desde((json['nodo'] as Map?)
                ?.cast<String, dynamic>() ??
            const <String, dynamic>{}),
        satelite: Componente.desde(
            (json['satelite'] as Map?)?.cast<String, dynamic>() ??
                const <String, dynamic>{}),
        sitio: Componente.desde((json['sitio'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{}),
        modo: json['modo'] as String? ?? 'instalacion',
        alertas: ((json['alertas'] as Map?) ?? const {})
            .map((k, v) => MapEntry(k.toString(), (v as num?)?.toInt() ?? 0)),
        familiares: (json['familiares'] as num?)?.toInt() ?? 0,
        conexiones: (json['conexiones'] as num?)?.toInt() ?? 0,
      );
}

/// El «cuánto tiempo hace» de la casa, en texto.
///
/// Se formatea a mano y no con `intl`: el paquete pesa, trae locales enteros y
/// sólo hace falta «hace 4 min». Para lo demás («ayer, 18:42») basta con saber
/// qué es ayer.
String hace(int? epochSegundos, {int? ahora}) {
  if (epochSegundos == null) return 'sin datos';
  final int ahoraS = ahora ?? DateTime.now().millisecondsSinceEpoch ~/ 1000;
  final int segundos = ahoraS - epochSegundos;
  if (segundos < 0) return 'ahora mismo';
  if (segundos < 60) return 'hace un momento';
  if (segundos < 3600) {
    return 'hace ${minutos(segundos ~/ 60)}';
  }
  if (segundos < 86400) {
    return 'hace ${horas(segundos ~/ 3600)}';
  }
  final DateTime fecha =
      DateTime.fromMillisecondsSinceEpoch(epochSegundos * 1000);
  final int dias = (ahoraS - epochSegundos) ~/ 86400;
  if (dias == 1) return 'ayer, ${horaDelDia(fecha)}';
  if (dias < 7) return 'hace $dias días';
  return '${fecha.day}/${fecha.month}/${fecha.year}';
}

String horaDelDia(DateTime fecha) =>
    '${fecha.hour.toString().padLeft(2, '0')}:${fecha.minute.toString().padLeft(2, '0')}';

/// La fecha larga de una alerta, que sí quiere día y hora.
String fechaAlerta(int epochSegundos, {DateTime? hoy}) {
  final DateTime fecha =
      DateTime.fromMillisecondsSinceEpoch(epochSegundos * 1000);
  final DateTime ref = hoy ?? DateTime.now();
  final DateTime soloDia = DateTime(fecha.year, fecha.month, fecha.day);
  final DateTime hoySolo = DateTime(ref.year, ref.month, ref.day);
  final int dias = hoySolo.difference(soloDia).inDays;
  final String hora = horaDelDia(fecha);
  if (dias == 0) return 'hoy, $hora';
  if (dias == 1) return 'ayer, $hora';
  return '${fecha.day}/${fecha.month}/${fecha.year} · $hora';
}
