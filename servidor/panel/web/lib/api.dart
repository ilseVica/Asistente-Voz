import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/status.dart' as estado_ws;
import 'package:web_socket_channel/web_socket_channel.dart';

import 'modelos.dart';

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
      String mensaje = 'no se pudo completar la petición';
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

  /// Comprueba un token pegado SIN cambiar el de la sesión.
  ///
  /// Para pegar en el campo un token copiado de otro navegador: si el token es
  /// válido devuelve los datos de `/api/yo`, y si no responde 401 (un 401 de
  /// `/api/yo` no toca el límite de accesos, que sólo mira `/api/acceso`).
  Future<Map<String, dynamic>> comprobarToken(String token) async {
    final http.Response respuesta = await _cliente
        .get(_ruta('/api/yo'),
            headers: <String, String>{'Authorization': 'Bearer $token'})
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

  Future<void> luz({required bool encender}) async {
    final http.Response respuesta = await _cliente
        .post(_ruta('/api/luz'),
            headers: _cabeceras, body: jsonEncode({'estado': encender ? 'on' : 'off'}))
        .timeout(const Duration(seconds: 15));
    await _json(respuesta);
  }
}

/// Los eventos del WebSocket.
///
/// `estado` es la instantánea inicial y los demás traen sólo su bloque. El
/// corazón late por su cuenta (`latido`), y `saturado` no llega nunca: se cierra
/// con 1013 y aquí se trata como «reconecta».
class Vivo {
  Vivo({required this.url, this.retrasoReconexion});

  final Uri url;

  /// Tiempo entre intentos. Se inyecta para poder probarlo sin esperar.
  final Duration Function(int intento)? retrasoReconexion;

  WebSocketChannel? _canal;
  StreamSubscription<dynamic>? _suscripcion;
  Timer? _temporizador;
  int _intentos = 0;

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
    _suscripcion = canal.stream.listen(_alRecibir,
        onDone: _alCerrar, onError: (Object _) => _alCerrar());
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
    final bool estaba = _abierto;
    _abierto = false;
    _suscripcion?.cancel();
    _suscripcion = null;
    _canal = null;
    if (estaba) {
      if (!_conectado.isClosed) _conectado.add(false);
      _reconectar();
    }
  }

  void _reconectar() {
    if (!_abierto) return;
    _temporizador?.cancel();
    _intentos++;
    // Espera creciente con tope: 1, 2, 4, 8, 16 y ya nunca más de 16 s. Si el
    // panel está caído no se lanza una tormenta de reconexiones, y si vuelve en
    // cuanto se puede se ve en el primer segundo.
    final int espera = _intentos > 4 ? 16 : 1 << (_intentos - 1);
    final Duration demora = retrasoReconexion?.call(espera) ??
        Duration(seconds: espera);
    _temporizador = Timer(demora, () {
      _abierto = true;
      _abrir();
    });
  }

  void desconectar() {
    _abierto = false;
    _temporizador?.cancel();
    _temporizador = null;
    _suscripcion?.cancel();
    _suscripcion = null;
    _canal?.sink.close(estado_ws.normalClosure);
    _canal = null;
    if (!_conectado.isClosed) _conectado.add(false);
  }

  void cerrar() {
    _estado.close();
    _bloques.close();
    _conectado.close();
  }
}
