import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/status.dart' as estado_ws;
import 'package:web_socket_channel/web_socket_channel.dart';

import 'modelos.dart';
import 'textos.dart';

/// Errores que la interfaz sabe distinguir.
///
/// No se lanza `Exception` a secas: un cartel «no se pudo conectar» y un
/// «este enlace ya no sirve; pide uno nuevo con /panel» llevan a acciones
/// distintas, y si se funden en uno solo la persona se queda esperando un enlace
/// que no va a llegar.
class ErrorApi implements Exception {
  const ErrorApi(this.mensaje, {this.codigo, this.sinSesion = false});

  final String mensaje;
  final int? codigo;

  /// 401: la sesión ya no sirve y hay que volver a la pantalla de acceso.
  final bool sinSesion;

  @override
  String toString() => mensaje;
}

/// El cliente de `panel/api.py`.
///
/// `base` sale de `Uri.base`, que es de donde viene el origen del navegador. Por
/// eso el enlace de Telegram funciona en `villaca...` y en `192.168.1.169` sin que
/// nadie configure nada: lo que se pregunta siempre es a este mismo origen.
class ClienteApi {
  ClienteApi({http.Client? cliente, Uri? origen})
      : _cliente = cliente ?? http.Client(),
        origen = origen ?? Uri.base;

  final http.Client _cliente;
  final Uri origen;

  String _token = '';
  String get token => _token;
  bool get tieneToken => _token.isNotEmpty;

  void ponerToken(String valor) => _token = valor;
  void quitarToken() => _token = '';

  Uri _ruta(String camino, [Map<String, String>? consulta]) =>
      origen.replace(path: camino, queryParameters: consulta);

  Map<String, String> get _cabeceras => <String, String>{
        'Authorization': 'Bearer $_token',
        'Content-Type': 'application/json',
        if (_noCache) 'Cache-Control': 'no-store',
      };

  /// Sin caché: el estado de la casa no puede quedar congelado en la caché del
  /// navegador de un móvil que se ha dormido.
  static const bool _noCache = true;

  Future<dynamic> _json(http.Response respuesta) async {
    if (respuesta.statusCode >= 400) {
      String mensaje = Textos.noSePudoCompletar;
      try {
        final dynamic cuerpo = jsonDecode(utf8.decode(respuesta.bodyBytes));
        if (cuerpo is Map && cuerpo['detail'] is String) {
          mensaje = cuerpo['detail'] as String;
        }
      } catch (_) {
        // Un cuerpo que no es JSON no cambia nada: lo que importa es el código.
      }
      throw ErrorApi(mensaje,
          codigo: respuesta.statusCode,
          sinSesion: respuesta.statusCode == 401);
    }
    if (respuesta.body.isEmpty) return null;
    return jsonDecode(utf8.decode(respuesta.bodyBytes));
  }

  /// `POST /api/acceso`. Devuelve el token y el nombre.
  Future<Map<String, dynamic>> canjear(String codigo) async {
    final http.Response respuesta = await _cliente
        .post(_ruta('/api/acceso'),
            headers: _cabeceras, body: jsonEncode({'codigo': codigo}))
        .timeout(const Duration(seconds: 15));
    final dynamic datos = await _json(respuesta);
    _token = (datos as Map)['token'] as String;
    return datos.cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> yo() async {
    final http.Response respuesta = await _cliente
        .get(_ruta('/api/yo'), headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    return (await _json(respuesta) as Map).cast<String, dynamic>();
  }

  Future<void> salir() async {
    try {
      await _cliente
          .post(_ruta('/api/salir'), headers: _cabeceras)
          .timeout(const Duration(seconds: 10));
    } catch (_) {
      // Si no se pudo revocar en el servidor, la sesión local se borra igual: la
      // persona ha pedido salir y no se le puede dejar con un token en el móvil.
    }
    quitarToken();
  }

  Future<Estado> estado() async {
    final http.Response respuesta = await _cliente
        .get(_ruta('/api/estado'), headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    return Estado.desde((await _json(respuesta) as Map).cast<String, dynamic>());
  }

  /// `POST /api/inactividad`. Devuelve los minutos que quedaron guardados.
  ///
  /// El servidor revalida y responde 422 si el número se sale de la franja; ese
  /// mensaje llega como `ErrorApi` y se enseña al lado del campo.
  Future<int> guardarInactividad(int tiempoMin) async {
    final http.Response respuesta = await _cliente
        .post(_ruta('/api/inactividad'),
            headers: _cabeceras, body: jsonEncode({'tiempo_min': tiempoMin}))
        .timeout(const Duration(seconds: 15));
    final Map<String, dynamic> datos =
        (await _json(respuesta) as Map).cast<String, dynamic>();
    return (datos['tiempo_min'] as num?)?.toInt() ?? tiempoMin;
  }

  Future<List<Alerta>> alertas({int limite = 20, int desde = 0}) async {
    final http.Response respuesta = await _cliente
        .get(_ruta('/api/alertas', <String, String>{
              'limite': '$limite',
              'desde': '$desde',
            }),
            headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    final Map<String, dynamic> datos =
        (await _json(respuesta) as Map).cast<String, dynamic>();
    return (datos['alertas'] as List)
        .map((f) => Alerta.desde((f as Map).cast<String, dynamic>()))
        .toList();
  }

  Future<Alerta> atender(String id) async {
    final http.Response respuesta = await _cliente
        .post(_ruta('/api/alertas/$id/atender'), headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    return Alerta.desde((await _json(respuesta) as Map).cast<String, dynamic>());
  }

  /// `GET /api/alertas/{id}`: la alerta con su línea de tiempo «Qué pasó».
  ///
  /// 401 llega como `ErrorApi` con `sinSesion` (como todo lo demás) y 404 con el
  /// mensaje del servidor, que es el que se enseña: «No se encontró esa alerta».
  Future<AlertaDetalle> alertaDetalle(String id) async {
    final http.Response respuesta = await _cliente
        .get(_ruta('/api/alertas/$id'), headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    return AlertaDetalle.desde(
        (await _json(respuesta) as Map).cast<String, dynamic>());
  }

  /// `GET /api/familiares`: sólo los nombres de los activos, sin chat_id.
  ///
  /// Devuelve la lista de nombres tal cual; un nombre vacío (alguien sin apodo
  /// guardado) lo deja vacío aquí y la pantalla lo pinta «Familiar».
  Future<List<String>> familiares() async {
    final http.Response respuesta = await _cliente
        .get(_ruta('/api/familiares'), headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    final Map<String, dynamic> datos =
        (await _json(respuesta) as Map).cast<String, dynamic>();
    return (datos['familiares'] as List)
        .map((dynamic f) =>
            ((f as Map)['nombre'] as String? ?? '').trim())
        .toList();
  }

  Future<void> luz({required bool encender}) async {
    final http.Response respuesta = await _cliente
        .post(_ruta('/api/luz'),
            headers: _cabeceras, body: jsonEncode({'estado': encender ? 'on' : 'off'}))
        .timeout(const Duration(seconds: 15));
    await _json(respuesta);
  }

  Future<List<Recordatorio>> recordatorios() async {
    final http.Response respuesta = await _cliente
        .get(_ruta('/api/recordatorios'), headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    final Map<String, dynamic> datos =
        (await _json(respuesta) as Map).cast<String, dynamic>();
    return (datos['recordatorios'] as List)
        .map((f) => Recordatorio.desde((f as Map).cast<String, dynamic>()))
        .toList();
  }

  /// Crea un recordatorio. `fecha` sólo en los de una sola vez y `dias` sólo en
  /// los semanales: el servidor rechaza con 422 si se cuelan en los demás.
  Future<Recordatorio> crearRecordatorio({
    required String texto,
    required String hora,
    required String repeticion,
    String? fecha,
    List<int>? dias,
  }) async {
    final http.Response respuesta = await _cliente
        .post(_ruta('/api/recordatorios'),
            headers: _cabeceras,
            body: jsonEncode(<String, dynamic>{
              'texto': texto,
              'hora': hora,
              'repeticion': repeticion,
              'fecha': ?fecha,
              'dias': ?dias,
            }))
        .timeout(const Duration(seconds: 15));
    return Recordatorio.desde(
        (await _json(respuesta) as Map).cast<String, dynamic>());
  }

  Future<void> cancelarRecordatorio(String id) async {
    final http.Response respuesta = await _cliente
        .post(_ruta('/api/recordatorios/$id/cancelar'), headers: _cabeceras)
        .timeout(const Duration(seconds: 15));
    await _json(respuesta);
  }
}

/// Los segundos de espera entre reintentos (apartado 4.3 de la propuesta):
/// 1, 2, 4, 8, 15, 30 y a partir de ahí siempre 30. Es la base; el jitter de
/// ±20 % se aplica encima en `_reconectar`.
int esperaReintentos(int intento) =>
    intento > 6 ? 30 : const <int>[1, 2, 4, 8, 15, 30][intento - 1];

/// Los eventos del WebSocket.
///
/// `estado` es la instantánea inicial y los demás traen sólo su bloque. El
/// corazón late por su cuenta (`latido`) y el cliente contesta `pong`; además
/// el cliente manda su propio `ping` cada 25 s (apartado 4.3), y `saturado` no
/// llega nunca: se cierra con 1013 y aquí se trata como «reconecta».
class Vivo {
  Vivo({required this.url, this.retrasoReconexion});

  final Uri url;

  /// Tiempo entre intentos. Se inyecta para poder probarlo sin esperar.
  final Duration Function(int intento)? retrasoReconexion;

  WebSocketChannel? _canal;
  StreamSubscription<dynamic>? _suscripcion;
  Timer? _temporizador;
  Timer? _ping;
  int _intentos = 0;

  final Random _azar = Random();

  final StreamController<Estado> _estado = StreamController<Estado>.broadcast();
  final StreamController<Map<String, dynamic>> _bloques =
      StreamController<Map<String, dynamic>>.broadcast();
  final StreamController<bool> _conectado = StreamController<bool>.broadcast();

  Stream<Estado> get instantanea => _estado.stream;
  Stream<Map<String, dynamic>> get bloques => _bloques.stream;
  Stream<bool> get conectado => _conectado.stream;

  bool _abierto = false;
  bool get abierto => _abierto;

  void conectar() {
    _abierto = true;
    _intentos = 0;
    _abrir();
  }

  void _abrir() {
    if (!_abierto) return;
    final WebSocketChannel canal;
    try {
      canal = WebSocketChannel.connect(url);
    } catch (_) {
      _reconectar();
      return;
    }
    _canal = canal;
    _ping?.cancel();
    _ping = Timer.periodic(const Duration(seconds: 25), (_) {
      // El ping propio del cliente (apartado 4.3): el navegador no puede mandar
      // un ping de protocolo, así que va como mensaje de aplicación.
      _mandar(<String, dynamic>{'tipo': 'ping'});
    });
    _suscripcion = canal.stream.listen(_alRecibir,
        onDone: _alCerrar, onError: (Object _) => _alCerrar());
  }

  void _mandar(Map<String, dynamic> mensaje) {
    // `_canal` puede haberse caído mientras se construía la orden.
    _canal?.sink.add(jsonEncode(mensaje));
  }

  void _alRecibir(dynamic dato) {
    if (dato is! String) return;
    try {
      final dynamic mensaje = jsonDecode(dato);
      if (mensaje is! Map) return;
      final Map<String, dynamic> mapa = mensaje.cast<String, dynamic>();
      final String tipo = mapa['tipo'] as String? ?? '';
      if (tipo == 'estado') {
        _intentos = 0;
        if (!_conectado.isClosed) _conectado.add(true);
        _estado.add(Estado.desde(
            (mapa['datos'] as Map).cast<String, dynamic>()));
      } else if (tipo == 'latido') {
        _intentos = 0;
        if (!_conectado.isClosed) _conectado.add(true);
        // El servidor cierra a los 45 s sin `pong`; el `pong` de aplicación es
        // la respuesta al `latido` (el ping de protocolo WebSocket no cuenta).
        _mandar(<String, dynamic>{'tipo': 'pong'});
      } else {
        if (!_conectado.isClosed) _conectado.add(true);
        _bloques.add(mapa);
      }
    } catch (_) {
      // Un mensaje que no se puede leer no debe cortar la conexión: el siguiente
      // puede ser válido.
    }
  }

  void _alCerrar() {
    // `_abierto` NO se toca aquí a propósito: significa «queremos seguir
    // conectados», y así `_reconectar` sabe que hay que reintentar. El que corta
    // la intención es `desconectar()` (salir de la sesión). Sin esta decisión,
    // la reconexión automática no pasaba el `if (!_abierto)` y el WebSocket se
    // quedaba muerto hasta que alguien recargara la página.
    final bool estaba = _abierto;
    _suscripcion?.cancel();
    _suscripcion = null;
    _canal = null;
    _ping?.cancel();
    _ping = null;
    if (estaba) {
      if (!_conectado.isClosed) _conectado.add(false);
      _reconectar();
    }
  }

  void _reconectar() {
    if (!_abierto) return;
    _temporizador?.cancel();
    _intentos++;
    // 1, 2, 4, 8, 15, 30 y de ahí en adelante 30, con ±20 % de desorden para que
    // dos teléfonos no reintenten a la vez (apartado 4.3 de la propuesta).
    final int base = esperaReintentos(_intentos);
    final Duration demora = retrasoReconexion?.call(base) ??
        Duration(
            seconds: (base * (1.0 + _azar.nextDouble() * 0.4 - 0.2)).round());
    _temporizador = Timer(demora, () {
      _abierto = true;
      _abrir();
    });
  }

  void desconectar() {
    _abierto = false;
    _temporizador?.cancel();
    _temporizador = null;
    _ping?.cancel();
    _ping = null;
    _suscripcion?.cancel();
    _suscripcion = null;
    _canal?.sink.close(estado_ws.normalClosure);
    _canal = null;
    if (!_conectado.isClosed) _conectado.add(false);
  }

  void cerrar() {
    _temporizador?.cancel();
    _temporizador = null;
    _ping?.cancel();
    _ping = null;
    _estado.close();
    _bloques.close();
    _conectado.close();
  }
}
