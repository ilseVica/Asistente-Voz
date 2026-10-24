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
    this.resueltoPor,
    this.enviados,
    this.sinMandar,
  });

  final String id;
  final String tipo;
  final String estado;
  final int creadoEn;
  final Map<String, dynamic> carga;
  final int? resueltaEn;

  /// El NOMBRE de quien la atendió («Ana»), nunca el chat_id. Las alertas
  /// anteriores al campo 8 del esquema lo traen `null` y entonces en pantalla
  /// sólo se pinta la hora, sin darle nombre a nadie.
  final String? resueltoPor;
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
        resueltoPor: json['resuelto_por'] as String?,
        enviados: json['enviados'] as int?,
        sinMandar: json['sin_mandar'] as int?,
      );
}

/// Un evento de la línea de tiempo «Qué pasó» (`GET /api/alertas/{id}`).
///
/// El servidor manda SOLO hechos reales de la base: `creada`, `enviada`,
/// `pendiente`, `atendida` y `descartada`, con su hora (`en`) y, cuando el
/// envío o la atención los traen, el NOMBRE de la persona. Aquí no se calcula
/// nada: si un evento no viene, no se pinta.
class EventoAlerta {
  const EventoAlerta({required this.tipo, required this.en, this.nombre});

  final String tipo;
  final int en;
  final String? nombre;

  factory EventoAlerta.desde(Map<String, dynamic> json) => EventoAlerta(
        tipo: json['tipo'] as String? ?? '',
        en: (json['en'] as num?)?.toInt() ?? 0,
        nombre: json['nombre'] as String?,
      );
}

/// Una alerta con su línea de tiempo, tal como devuelve `GET /api/alertas/{id}`.
class AlertaDetalle {
  const AlertaDetalle({
    required this.alerta,
    required this.eventos,
    required this.servidorHora,
  });

  final Alerta alerta;

  /// En el orden en que pasaron las cosas, ya ordenado por el servidor.
  final List<EventoAlerta> eventos;
  final int servidorHora;

  factory AlertaDetalle.desde(Map<String, dynamic> json) => AlertaDetalle(
        alerta: Alerta.desde(
            (json['alerta'] as Map).cast<String, dynamic>()),
        eventos: ((json['eventos'] as List?) ?? const <dynamic>[])
            .map((dynamic e) =>
                EventoAlerta.desde((e as Map).cast<String, dynamic>()))
            .toList(),
        servidorHora: json['servidor_hora'] as int? ?? 0,
      );
}

/// Un recordatorio de `GET /api/recordatorios`, apartado 3.
///
/// `dias` sólo tiene sentido en los semanales y `fecha` sólo en los de una sola
/// vez; el servidor manda lo que corresponda y `[]`/`null` en el resto.
class Recordatorio {
  const Recordatorio({
    required this.id,
    required this.texto,
    required this.hora,
    required this.repeticion,
    required this.fecha,
    required this.dias,
    required this.proximoEn,
    required this.activo,
    required this.creadoEn,
    required this.creadoPor,
    required this.canceladoEn,
    required this.hechoEn,
    required this.ultimaEntrega,
  });

  final String id;
  final String texto;

  /// La hora en 'HH:MM', tal como la manda la API.
  final String hora;
  final String repeticion;
  final String? fecha;
  final List<int> dias;
  final int proximoEn;
  final bool activo;
  final int creadoEn;
  final String creadoPor;
  final int? canceladoEn;
  final int? hechoEn;
  final EntregaRecordatorio? ultimaEntrega;

  bool get deUnaSolaVez => repeticion == 'una_vez';

  factory Recordatorio.desde(Map<String, dynamic> json) => Recordatorio(
        id: json['id'] as String,
        texto: json['texto'] as String,
        hora: json['hora'] as String? ?? '00:00',
        repeticion: json['repeticion'] as String? ?? 'una_vez',
        fecha: json['fecha'] as String?,
        dias: ((json['dias'] as List?) ?? const <dynamic>[])
            .map((dynamic d) => (d as num).toInt())
            .toList(),
        proximoEn: json['proximo_en'] as int? ?? 0,
        activo: json['activo'] as bool? ?? false,
        creadoEn: json['creado_en'] as int? ?? 0,
        creadoPor: json['creado_por'] as String? ?? '',
        canceladoEn: json['cancelado_en'] as int?,
        hechoEn: json['hecho_en'] as int?,
        ultimaEntrega: json['ultima_entrega'] == null
            ? null
            : EntregaRecordatorio.desde(
                (json['ultima_entrega'] as Map).cast<String, dynamic>()),
      );
}

/// La última vez que un recordatorio se dijo (o no se pudo decir).
class EntregaRecordatorio {
  const EntregaRecordatorio({required this.estado, required this.cuando});

  final String estado;
  final int? cuando;

  bool get seDijo => estado == 'entregado';

  factory EntregaRecordatorio.desde(Map<String, dynamic> json) =>
      EntregaRecordatorio(
        estado: json['estado'] as String? ?? '',
        cuando: json['cuando'] as int?,
      );
}

/// El reloj de la inactividad, del bloque `inactividad` de `GET /api/estado`.
///
/// `ultimoMovimiento` es el epoch del último movimiento que vio el orquestador, o
/// `null` si todavía no ha visto ninguno (y entonces se dice «sin datos», no se
/// pinta una hora inventada). `tiempoMin` es el ajuste vigente y `minimo`/`maximo`
/// su franja: el formulario del panel usa esa franja y no números escritos a mano,
/// para que no pueda mandar un valor que el servidor vaya a rechazar.
class Inactividad {
  const Inactividad({
    required this.ultimoMovimiento,
    required this.tiempoMin,
    required this.minimo,
    required this.maximo,
  });

  final int? ultimoMovimiento;
  final int tiempoMin;
  final int minimo;
  final int maximo;

  factory Inactividad.desde(Map<String, dynamic> json) => Inactividad(
        ultimoMovimiento: json['ultimo_movimiento'] as int?,
        tiempoMin: (json['tiempo_min'] as num?)?.toInt() ?? 2,
        minimo: (json['minimo'] as num?)?.toInt() ?? 2,
        maximo: (json['maximo'] as num?)?.toInt() ?? 720,
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
    required this.inactividad,
    required this.modo,
    required this.alertas,
    required this.familiares,
    required this.conexiones,
    this.gasOnAdc,
  });

  final int servidorHora;
  final Luz luz;
  final Sensores sensores;
  final Componente nodo;
  final Componente satelite;
  final Componente sitio;
  final Inactividad inactividad;
  final String modo;
  final Map<String, int> alertas;
  final int familiares;
  final int conexiones;

  /// El umbral de encendido del gas (`GAS_ON_ADC`), en lecturas del ADC. Lo
  /// manda `instantanea()` desde la configuración por entorno y es el número
  /// que la tarjeta de gas enseña y contra el que compara: sin él no hay forma
  /// de decir en el panel cuándo es «demasiado».
  final num? gasOnAdc;

  /// Cuántas alertas no están resueltas ni descartadas. El servidor manda los
  /// cuatro conteos y el total se saca aquí, porque es una cuenta y no una regla.
  int get alertasSinResolver {
    int total = 0;
    alertas.forEach((clave, valor) {
      if (clave == 'pendiente' || clave == 'enviada') total += valor;
    });
    return total;
  }

  /// Una copia con otro bloque de inactividad.
  ///
  /// Existe sólo por el ajuste: al guardar los minutos, el servidor devuelve el
  /// valor que quedó y se pinta sin esperar a la siguiente instantánea. No se hace
  /// un `copyWith` general porque el único bloque que cambia la interfaz por su
  /// cuenta es éste; los demás llegan por el WebSocket.
  Estado conInactividad(Inactividad nueva) => Estado(
        servidorHora: servidorHora,
        luz: luz,
        sensores: sensores,
        nodo: nodo,
        satelite: satelite,
        sitio: sitio,
        inactividad: nueva,
        modo: modo,
        alertas: alertas,
        familiares: familiares,
        conexiones: conexiones,
        gasOnAdc: gasOnAdc,
      );

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
        inactividad: Inactividad.desde(
            (json['inactividad'] as Map?)?.cast<String, dynamic>() ??
                const <String, dynamic>{}),
        modo: json['modo'] as String? ?? 'instalacion',
        alertas: ((json['alertas'] as Map?) ?? const {})
            .map((k, v) => MapEntry(k.toString(), (v as num?)?.toInt() ?? 0)),
        familiares: (json['familiares'] as num?)?.toInt() ?? 0,
        conexiones: (json['conexiones'] as num?)?.toInt() ?? 0,
        gasOnAdc: json['gas_on_adc'] as num?,
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

/// Cuándo toca un recordatorio, mirando hacia adelante.
///
/// A diferencia de `fechaAlerta`, aquí «mañana» y el día de la semana sí importan:
/// la pantalla de recordatorios enseña lo que va a pasar, no lo que pasó.
String fechaProxima(int epochSegundos, {DateTime? hoy}) {
  final DateTime fecha =
      DateTime.fromMillisecondsSinceEpoch(epochSegundos * 1000);
  final DateTime ref = hoy ?? DateTime.now();
  final int dias = DateTime(fecha.year, fecha.month, fecha.day)
      .difference(DateTime(ref.year, ref.month, ref.day))
      .inDays;
  final String hora = horaDelDia(fecha);
  if (dias == 0) return 'hoy, $hora';
  if (dias == 1) return 'mañana, $hora';
  if (dias > 1 && dias < 7) {
    return 'el ${Textos.diasSemana[fecha.weekday - 1].toLowerCase()}, $hora';
  }
  return '${fecha.day}/${fecha.month}/${fecha.year} · $hora';
}

/// Cuándo se dijo (o tocaba decir) un recordatorio, mirando hacia atrás.
String cuandoSeDijo(int epochSegundos, {DateTime? hoy}) {
  final DateTime fecha =
      DateTime.fromMillisecondsSinceEpoch(epochSegundos * 1000);
  final DateTime ref = hoy ?? DateTime.now();
  final int dias = DateTime(ref.year, ref.month, ref.day)
      .difference(DateTime(fecha.year, fecha.month, fecha.day))
      .inDays;
  final String hora = horaDelDia(fecha);
  if (dias == 0) return 'hoy a las $hora';
  if (dias == 1) return 'ayer a las $hora';
  return 'el ${fecha.day}/${fecha.month}/${fecha.year} a las $hora';
}
