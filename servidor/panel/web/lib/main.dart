import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'modelos.dart';
import 'sesion.dart';
import 'tema.dart';
import 'textos.dart';
import 'uis/pantalla_acceso.dart';
import 'uis/pantalla_alertas.dart';
import 'uis/pantalla_aviso.dart';
import 'uis/pantalla_panel.dart';
import 'uis/pantalla_proximamente.dart';

void main() {
  runApp(const AsistentePanel());
}

/// A dónde se está mirando. Los cuatro destinos son navegación de verdad y los dos
/// últimos dicen «próximamente» con texto propio: no se inventan datos de ejemplo
/// en una pantalla que todavía no existe.
enum Destino { inicio, alertas, recordatorios, mensajes }

/// El estado compartido del panel.
///
/// `ChangeNotifier` a secas y `InheritedNotifier` para llegar a él, que es lo que
/// trae el SDK. Un paquete de estado añadiría dependencia, código generado y una
/// capa que hay que entender antes de entender el panel; aquí lo que hay que
/// entender es esto, que es corto.
class EstadoPanel extends ChangeNotifier {
  EstadoPanel({ClienteApi? api, Sesion? sesion})
      : api = api ?? ClienteApi(),
        sesion = sesion ?? Sesion();

  final ClienteApi api;
  final Sesion sesion;

  // --- Arranque --------------------------------------------------------------

  bool preparando = true;
  bool avisoPendiente = false;
  String? errorArranque;

  Estado? estado;
  List<Alerta> alertas = const <Alerta>[];
  String nombre = '';

  bool conectadoAlServidor = false;
  bool conectadoAlSitio = false;
  bool cargandoLuz = false;
  bool cargandoAlerta = false;
  String? aviso; // Un mensaje que sólo se ve una vez (un error, sobre todo).

  /// Justo después de entrar: el token en claro, para que se pueda copiar.
  ///
  /// Sirve para entrar desde otro navegador sin pedir `/panel` otra vez. Se
  /// muestra en el campo de la pantalla de acceso, y se borra al arrancar el
  /// panel: la petición explícita de la familia fue que el token se vea a la
  /// hora de entrar para poder copiarlo. Mientras está aquí, el panel no
  /// arranca (le toca el turno a la pantalla de acceso).
  String? sesionNueva;

  Destino destino = Destino.inicio;

  Vivo? _vivo;
  Timer? _reloj;
  Timer? _refrescoAlertas;

  /// La hora del servidor, que es la que se usa para el «hace 4 min».
  ///
  /// No se usa la del dispositivo: un móvil con la hora mal atrasada pintaría
  /// «hace 3 h» para algo que llegó hace un segundo. `servidor_hora` existe justo
  /// para esto.
  int ahoraServidor() {
    final Estado? e = estado;
    if (e == null) return DateTime.now().millisecondsSinceEpoch ~/ 1000;
    final int base = e.servidorHora;
    final int transcurrido =
        DateTime.now().millisecondsSinceEpoch ~/ 1000 - _momentoLocal!;
    return base + transcurrido;
  }

  int? _momentoLocal;

  Future<void> arrancar() async {
    preparando = true;
    errorArranque = null;
    notifyListeners();

    final String? guardado = await sesion.leerToken();
    if (guardado == null || guardado.isEmpty) {
      preparando = false;
      avisoPendiente = await sesion.avisoVisto();
      notifyListeners();
      return;
    }

    api.ponerToken(guardado);
    avisoPendiente = await sesion.avisoVisto();

    try {
      final Map<String, dynamic> yo = await api.yo();
      nombre = yo['nombre'] as String? ?? '';
      preparando = false;
      notifyListeners();
      _empezar();
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        // El token ya no sirve (caducado o revocado). No se avisa: se vuelve a la
        // pantalla de acceso, que explica cómo pedir uno nuevo.
        await _olvidar();
        preparando = false;
        notifyListeners();
      } else {
        preparando = false;
        errorArranque = error.mensaje;
        notifyListeners();
      }
    } catch (_) {
      preparando = false;
      errorArranque = Textos.errorDeRed;
      notifyListeners();
    }
  }

  Future<void> _olvidar() async {
    api.quitarToken();
    await sesion.borrarToken();
    nombre = '';
    sesionNueva = null;
    estado = null;
    alertas = const <Alerta>[];
    _vivo?.desconectar();
    _vivo = null;
    _reloj?.cancel();
    _refrescoAlertas?.cancel();
  }

  void _empezar() {
    _vivo?.desconectar();
    _vivo = Vivo(url: _urlVivo());
    _vivo!.conectado.listen((bool ok) {
      conectadoAlServidor = ok;
      notifyListeners();
    });
    _vivo!.instantanea.listen((Estado e) {
      estado = e;
      _momentoLocal = DateTime.now().millisecondsSinceEpoch ~/ 1000;
      notifyListeners();
    });
    _vivo!.bloques.listen(_aplicar);
    _vivo!.conectar();

    _reloj = Timer.periodic(const Duration(seconds: 30), (_) => notifyListeners());
    _refrescoAlertas =
        Timer.periodic(const Duration(seconds: 5), (_) => cargarAlertas());

    cargarEstado();
    cargarAlertas();
  }

  Uri _urlVivo() {
    final Uri base = api.origen;
    return base.replace(
      scheme: base.scheme == 'https' ? 'wss' : 'ws',
      path: '/api/vivo',
      queryParameters: <String, String>{'token': api.token},
    );
  }

  /// Aplica un evento del WebSocket a la instantánea.
  ///
  /// Cada evento trae un sólo bloque y no la instantánea entera, así que se copia
  /// la de antes cambiando lo que toca. Se hace con una lista de «qué bloque
  /// cambio» y no con cinco casi-iguales: con cinco, el próximo bloque que añada
  /// la API se olvida de una y la tarjeta se queda congelada sin que nada falle.
  ///
  /// El único caso que no es «sustituye el bloque por el que viene» es `nodo` en
  /// modo instalación, donde el bloque del sitio sale del nodo.
  void _aplicar(Map<String, dynamic> mensaje) {
    final Estado? actual = estado;
    if (actual == null) return;
    final String tipo = mensaje['tipo'] as String? ?? '';
    final Map<String, dynamic> datos =
        (mensaje['datos'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{};

    Luz luz = actual.luz;
    Sensores sensores = actual.sensores;
    Componente nodo = actual.nodo;
    Componente satelite = actual.satelite;
    Componente sitio = actual.sitio;

    switch (tipo) {
      case 'luz':
        luz = Luz.desde(datos);
      case 'sensores':
        sensores = Sensores.desde(datos);
      case 'nodo':
        nodo = Componente.desde(datos);
        if (actual.modo == 'instalacion') {
          // En instalación el sitio ES el nodo. Sin recalcularlo aquí, la tarjeta
          // del sitio se quedaría con lo viejo hasta la próxima instantánea, que no
          // llega hasta que alguien recargue.
          sitio = Componente(estado: nodo.estado,
              recibidoEn: nodo.recibidoEn, fuente: 'nodo');
        }
      case 'satelite':
        satelite = Componente.desde(datos);
      case 'sitio':
        sitio = Componente.desde(datos);
      default:
        return;
    }

    estado = Estado(
      servidorHora: actual.servidorHora,
      luz: luz,
      sensores: sensores,
      nodo: nodo,
      satelite: satelite,
      sitio: sitio,
      modo: actual.modo,
      alertas: actual.alertas,
      familiares: actual.familiares,
      conexiones: actual.conexiones,
    );
    notifyListeners();
  }

  // --- Peticiones -------------------------------------------------------------

  Future<void> cargarEstado() async {
    try {
      final Estado e = await api.estado();
      estado = e;
      _momentoLocal = DateTime.now().millisecondsSinceEpoch ~/ 1000;
      conectadoAlServidor = true;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        await cerrarSesion();
        return;
      }
      conectadoAlServidor = false;
      aviso = error.mensaje;
    } catch (_) {
      conectadoAlServidor = false;
    }
    notifyListeners();
  }

  Future<void> cargarAlertas() async {
    try {
      alertas = await api.alertas(limite: 20);
      conectadoAlServidor = true;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        await cerrarSesion();
        return;
      }
      // Un fallo al refrescar las alertas cada 5 s no puede levantar un cartel en
      // pantalla: parpadearía con cada red móvil que dudara. Sólo se marca la
      // conexión.
      conectadoAlServidor = false;
    } catch (_) {
      conectadoAlServidor = false;
    }
    notifyListeners();
  }

  Future<void> encenderLuz({required bool encender}) async {
    if (cargandoLuz) return;
    cargandoLuz = true;
    aviso = null;
    notifyListeners();
    try {
      await api.luz(encender: encender);
      // No se pone la luz aquí. La orden es 202 y el estado real llega por el
      // WebSocket: pintar «encendida» porque se mandó la orden sería mentir.
      aviso = encender
          ? Textos.luzEnviadaEncender
          : Textos.luzEnviadaApagar;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        cargandoLuz = false;
        await cerrarSesion();
        return;
      }
      aviso = error.mensaje;
    } catch (_) {
      aviso = Textos.noSePudoEnviarOrden;
    }
    cargandoLuz = false;
    notifyListeners();
  }

  Future<void> atenderAlerta(String id) async {
    if (cargandoAlerta) return;
    cargandoAlerta = true;
    notifyListeners();
    try {
      final Alerta resuelta = await api.atender(id);
      final int indice = alertas.indexWhere((Alerta a) => a.id == id);
      if (indice >= 0) {
        final List<Alerta> copia = List<Alerta>.from(alertas);
        copia[indice] = resuelta;
        alertas = copia;
      } else {
        alertas = <Alerta>[resuelta, ...alertas];
      }
      if (!resuelta.estado.startsWith('resuelta')) {
        aviso = Textos.otraPersonaAtendio;
      }
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        cargandoAlerta = false;
        await cerrarSesion();
        return;
      }
      aviso = error.mensaje;
    } catch (_) {
      aviso = Textos.noSePudoAtender;
    }
    cargandoAlerta = false;
    notifyListeners();
  }

  Future<void> aceptarAviso() async {
    avisoPendiente = false;
    await sesion.marcarAvisoVisto();
    notifyListeners();
  }

  Future<void> entrar(String enlaceOcodigo) async {
    final String entrada = enlaceOcodigo.trim();
    if (entrada.isEmpty) {
      aviso = Textos.faltaEnlace;
      notifyListeners();
      return;
    }
    final String? codigo = codigoDeEnlace(entrada);
    aviso = null;
    preparando = true;
    notifyListeners();
    try {
      final Map<String, dynamic> datos =
          await _sesionConTokenOCodigo(entrada, codigo);
      sesionNueva = api.token;
      await sesion.guardarToken(api.token);
      final String nombreRecibido = datos['nombre'] as String? ?? '';
      await sesion.guardarNombre(nombreRecibido);
      nombre = nombreRecibido;
      aviso = null;
      preparando = false;
      notifyListeners();
      // El panel NO arranca aquí a propósito: la pantalla de acceso se queda con
      // el token delante para que se pueda copiar (petición de la familia).
    } on ErrorApi catch (error) {
      preparando = false;
      aviso = error.mensaje;
      notifyListeners();
    } catch (_) {
      preparando = false;
      aviso = Textos.errorDeRed;
      notifyListeners();
    }
  }

  /// Entrada por un token pegado (otro navegador) o por código/enlace del bot.
  ///
  /// Un token y un código son indistinguibles por la forma (los dos son letras y
  /// números), así que se prueba el token primero: si `/api/yo` lo acepta, es de
  /// esta sesión y no se canjea nada; si responde 401, cae al canje normal con el
  /// código. El 401 de `/api/yo` no descuenta nada del límite de accesos, que sólo
  /// mira `/api/acceso`, así que no hay riesgo de bloquear a nadie por pegar mal.
  Future<Map<String, dynamic>> _sesionConTokenOCodigo(
      String entrada, String? codigo) async {
    try {
      final Map<String, dynamic> datos = await api.comprobarToken(entrada);
      api.ponerToken(entrada);
      return datos;
    } on ErrorApi catch (error) {
      if (!error.sinSesion) rethrow;
      if (codigo == null) {
        throw const ErrorApi(Textos.enlaceNoReconocido);
      }
      return await api.canjear(codigo);
    }
  }

  /// Termina la entrada: arranca el panel y quita el token de la pantalla.
  void entrarAlPanel() {
    sesionNueva = null;
    _empezar();
  }

  Future<void> cerrarSesion() async {
    await api.salir();
    await _olvidar();
    aviso = null;
    preparando = false;
    avisoPendiente = await sesion.avisoVisto();
    notifyListeners();
  }

  void irA(Destino nuevo) {
    if (destino == nuevo) return;
    destino = nuevo;
    notifyListeners();
  }

  void limpiarAviso() {
    aviso = null;
    notifyListeners();
  }

  /// Pone un mensaje que sólo se ve hasta la próxima petición.
  ///
  /// Está aparte de tocar `aviso` desde fuera porque `notifyListeners` es de
  /// `ChangeNotifier` y se puede llamar, pero un estado que se edita desde fuera
  /// no se puede ni encontrar con una búsqueda: esto deja una sola puerta de
  /// entrada.
  void avisar(String texto) {
    aviso = texto;
    notifyListeners();
  }

  @override
  void dispose() {
    _reloj?.cancel();
    _refrescoAlertas?.cancel();
    _vivo?.desconectar();
    _vivo?.cerrar();
    super.dispose();
  }
}

/// Cómo llegan los widgets al estado. Un `InheritedNotifier` y no un
/// `Provider`: se hereda de un `ChangeNotifier`, que es exactamente lo que hace
/// falta, sin paquete de por medio.
class PanelScope extends InheritedNotifier<EstadoPanel> {
  const PanelScope({super.key, required EstadoPanel estado, required super.child})
      : super(notifier: estado);

  static EstadoPanel de(BuildContext contexto) {
    final PanelScope? alcance =
        contexto.dependOnInheritedWidgetOfExactType<PanelScope>();
    assert(alcance != null, 'Falta PanelScope por encima del widget');
    return alcance!.notifier!;
  }
}

class AsistentePanel extends StatefulWidget {
  const AsistentePanel({super.key});

  @override
  State<AsistentePanel> createState() => _AsistentePanelState();
}

class _AsistentePanelState extends State<AsistentePanel> {
  final EstadoPanel _estado = EstadoPanel();

  @override
  void initState() {
    super.initState();
    _estado.arrancar();
  }

  @override
  void dispose() {
    _estado.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return PanelScope(
      estado: _estado,
      child: ListenableBuilder(
        listenable: _estado,
        builder: (BuildContext contexto, Widget? _) {
          return MaterialApp(
            title: Textos.panelDeLaSala,
            debugShowCheckedModeBanner: false,
            theme: Tema.construir(),
            home: _raiz(contexto),
          );
        },
      ),
    );
  }

  Widget _raiz(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);

    if (estado.preparando) return const Cargando();

    // El aviso de privacidad NO bloquea: se entra, se ve, y se continúa con un
    // botón. Una pantalla que se interpone justo cuando un familiar mira si hay
    // gas en casa es un fallo de la casa, no una protección (decisión 8.1).
    if (estado.avisoPendiente) return const PantallaAviso();

    if (!estado.api.tieneToken) return const PantallaAcceso();

    // Recién entrado: la pantalla de acceso se queda con el token a la vista para
    // que se pueda copiar a otro navegador (petición de la familia).
    if (estado.sesionNueva != null) return const PantallaAcceso();

    if (estado.errorArranque != null) {
      return PantallaErrorArranque(mensaje: estado.errorArranque!);
    }

    switch (estado.destino) {
      case Destino.inicio:
        return const PantallaPanel();
      case Destino.alertas:
        return const PantallaAlertas();
      case Destino.recordatorios:
        return const PantallaProximamente(
            titulo: Textos.recordatorios,
            texto: Textos.recordatoriosVacio);
      case Destino.mensajes:
        return const PantallaProximamente(
            titulo: Textos.mensajes,
            texto: Textos.mensajesVacio);
    }
  }
}

class Cargando extends StatelessWidget {
  const Cargando({super.key});

  @override
  Widget build(BuildContext contexto) {
    return const Scaffold(
      body: Center(child: CircularProgressIndicator()),
    );
  }
}

class PantallaErrorArranque extends StatelessWidget {
  const PantallaErrorArranque({super.key, required this.mensaje});

  final String mensaje;

  @override
  Widget build(BuildContext contexto) {
    final EstadoPanel estado = PanelScope.de(contexto);
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(Textos.noSePudoAbrir,
                  style: Theme.of(contexto).textTheme.headlineMedium,
                  textAlign: TextAlign.center),
              const SizedBox(height: 12),
              Text(mensaje,
                  style: Theme.of(contexto).textTheme.bodyMedium,
                  textAlign: TextAlign.center),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: estado.cargarEstado,
                child: const Text(Textos.volverAIntentar),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
