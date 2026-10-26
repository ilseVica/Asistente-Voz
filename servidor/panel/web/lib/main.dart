import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'modelos.dart';
import 'sesion.dart';
import 'tema.dart';
import 'textos.dart';
import 'uis/pantalla_acceso.dart';
import 'uis/pantalla_alerta_detalle.dart';
import 'uis/pantalla_alertas.dart';
import 'uis/pantalla_aviso.dart';
import 'uis/pantalla_familiares.dart';
import 'uis/pantalla_panel.dart';
import 'uis/pantalla_proximamente.dart';
import 'uis/pantalla_recordatorios.dart';

void main() {
  runApp(const AsistentePanel());
}

/// A dónde se está mirando. Los cinco destinos son navegación de verdad; el de
/// «Mensajes» todavía no tiene pantalla propia y dice «próximamente» con texto
/// propio, sin inventar datos de ejemplo en una pantalla que no existe.
enum Destino { inicio, alertas, recordatorios, mensajes, familiares }

/// El estado compartido del panel.
///
/// `ChangeNotifier` a secas y `InheritedNotifier` para llegar a él, que es lo que
/// trae el SDK. Un paquete de estado añadiría dependencia, código generado y una
/// capa que hay que entender antes de entender el panel; aquí lo que hay que
/// entender es esto, que es corto.
class EstadoPanel extends ChangeNotifier {
  EstadoPanel({ClienteApi? api, Sesion? sesion, this._ultimoMensajeVivo})
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
  List<Recordatorio> recordatorios = const <Recordatorio>[];
  String nombre = '';

  /// Los nombres de los familiares activos, para la pantalla «Familiares».
  List<String> nombresFamiliares = const <String>[];
  bool cargandoFamiliares = false;
  String? errorFamiliares;

  /// El detalle de una alerta abierta. Mientras `alertaAbierta` tenga valor, la
  /// raíz enseña el detalle en vez de la pantalla de `destino`: «volver» es
  /// borrarlo y se vuelve a donde se estaba, sin perder el sitio.
  String? alertaAbierta;
  AlertaDetalle? detalleAlerta;
  bool cargandoDetalle = false;
  String? errorDetalle;

  /// La alerta activa más reciente, o `null` si no queda ninguna sin atender.
  ///
  /// Es lo que decide el banner rojo de arriba de todo el panel. Si hay varias
  /// a la vez se queda con la más nueva, que es la que acaba de pasar y la que
  /// alguien que no estaba mirando todavía no habrá visto.
  Alerta? get alertaMasUrgente {
    final List<Alerta> vivas =
        alertas.where((Alerta a) => a.sinResolver).toList();
    if (vivas.isEmpty) return null;
    int prioridad(String t) {
      if (t == 'gas') return 0;
      if (t == 'ayuda') return 1;
      if (t == 'inactividad') return 2;
      return 3;
    }
    vivas.sort((Alerta a, Alerta b) {
      final int p = prioridad(a.tipo).compareTo(prioridad(b.tipo));
      if (p != 0) return p;
      return b.creadoEn.compareTo(a.creadoEn);
    });
    return vivas.first;
  }

  bool conectadoAlServidor = false;
  bool conectadoAlSitio = false;
  bool cargandoLuz = false;
  bool cargandoAlerta = false;
  bool cargandoRecordatorio = false;
  bool cargandoInactividad = false;
  String? aviso; // Un mensaje que sólo se ve una vez (un error, sobre todo).

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
    final int? momentoLocal = _momentoLocal;
    final int transcurrido = momentoLocal == null
        ? 0
        : DateTime.now().millisecondsSinceEpoch ~/ 1000 - momentoLocal;
    return base + transcurrido;
  }

  int ahoraLocal() => DateTime.now().millisecondsSinceEpoch ~/ 1000;

  int? _momentoLocal;
  int? _ultimoMensajeVivo;

  /// Último momento en el que llegó un mensaje vivo (WebSocket).
  int? get ultimoMensajeVivo => _ultimoMensajeVivo;

  /// Anota que llegó un mensaje vivo del servidor.
  void anotarMensajeVivo([int? cuando]) {
    _ultimoMensajeVivo = cuando ?? ahoraLocal();
    notifyListeners();
  }

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
    estado = null;
    alertas = const <Alerta>[];
    recordatorios = const <Recordatorio>[];
    nombresFamiliares = const <String>[];
    errorFamiliares = null;
    alertaAbierta = null;
    detalleAlerta = null;
    cargandoDetalle = false;
    errorDetalle = null;
    _vivo?.desconectar();
    _vivo = null;
    _reloj?.cancel();
    _refrescoAlertas?.cancel();
    _ultimoMensajeVivo = null;
  }

  /// Vuelve a pedir la instantánea sin hacer ruido, para el «último movimiento».
  ///
  /// El WebSocket empuja luz, sensores, nodo y sitio, pero NO la inactividad: ese
  /// reloj lo escribe el orquestador en la base que los dos comparten y no hay
  /// tópico que lo anuncie. Sin esta vuelta, el «hace X min» se quedaría con el
  /// valor de cuando se abrió el panel y subiría sin parar aunque alguien se
  /// moviera. Un fallo aquí no levanta cartel, igual que en las alertas: sólo se
  /// marca que no hay conexión.
  Future<void> _refrescarEstado() async {
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
    } catch (_) {
      conectadoAlServidor = false;
    }
    notifyListeners();
  }

  void _empezar() {
    _vivo?.desconectar();
    _vivo = Vivo(url: _urlVivo());
    _vivo!.conectado.listen((bool ok) {
      conectadoAlServidor = ok;
      if (ok) {
        _ultimoMensajeVivo = ahoraLocal();
      }
      notifyListeners();
    });
    _vivo!.instantanea.listen((Estado e) {
      estado = e;
      _momentoLocal = ahoraLocal();
      _ultimoMensajeVivo = ahoraLocal();
      notifyListeners();
    });
    _vivo!.bloques.listen((Map<String, dynamic> m) {
      _ultimoMensajeVivo = ahoraLocal();
      _aplicar(m);
    });
    _vivo!.conectar();

    _reloj = Timer.periodic(const Duration(seconds: 30), (_) {
      // El aviso inmediato es para el «hace X min», que se calcula con el reloj
      // del cliente y sube solo; la vuelta al servidor es para traer los datos
      // que no llegan por el WebSocket (el último movimiento de la inactividad).
      notifyListeners();
      _refrescarEstado();
    });
    _refrescoAlertas =
        Timer.periodic(const Duration(seconds: 5), (_) => cargarAlertas());

    cargarEstado();
    cargarAlertas();
    cargarRecordatorios();
    cargarFamiliares();
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
      // La inactividad no llega por el WebSocket: la trae la vuelta periódica a
      // `/api/estado`. Aquí sólo se conserva la que ya había.
      inactividad: actual.inactividad,
      modo: actual.modo,
      alertas: actual.alertas,
      familiares: actual.familiares,
      conexiones: actual.conexiones,
      // El umbral de gas no cambia nunca en caliente: se configura por
      // entorno y se publica al arrancar, así que aquí sólo se conserva el que
      // ya traía la instantánea.
      gasOnAdc: actual.gasOnAdc,
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

  /// Los nombres de los familiares activos para «Familiares».
  ///
  /// Un fallo aquí SÍ levanta cartel en su pantalla (no parpadea cada 5 s como
  /// las alertas): sólo se pide al entrar y al reintentar. El 401, como en todo,
  /// cierra la sesión y vuelve al código de acceso.
  Future<void> cargarFamiliares() async {
    cargandoFamiliares = true;
    errorFamiliares = null;
    notifyListeners();
    try {
      nombresFamiliares = await api.familiares();
      conectadoAlServidor = true;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        cargandoFamiliares = false;
        await cerrarSesion();
        return;
      }
      errorFamiliares = error.mensaje;
      conectadoAlServidor = false;
    } catch (_) {
      errorFamiliares = Textos.errorDeRed;
      conectadoAlServidor = false;
    }
    cargandoFamiliares = false;
    notifyListeners();
  }

  /// Abre el detalle de una alerta (la raíz cambia a `PantallaAlertaDetalle`).
  void abrirAlerta(String id) {
    alertaAbierta = id;
    detalleAlerta = null;
    errorDetalle = null;
    cargandoDetalle = true;
    notifyListeners();
    unawaited(_cargarDetalle(id));
  }

  /// Vuelve a la pantalla de `destino`, que es de donde se abrió el detalle.
  void cerrarAlerta() {
    alertaAbierta = null;
    detalleAlerta = null;
    errorDetalle = null;
    cargandoDetalle = false;
    notifyListeners();
  }

  Future<void> _cargarDetalle(String id) async {
    try {
      final AlertaDetalle detalle = await api.alertaDetalle(id);
      // Si mientras cargaba se cerró el detalle (o se abrió otro), esta
      // respuesta ya no le corresponde y se tira en vez de pisarla.
      if (alertaAbierta != id) return;
      detalleAlerta = detalle;
      cargandoDetalle = false;
      errorDetalle = null;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        cargandoDetalle = false;
        await cerrarSesion();
        return;
      }
      if (alertaAbierta != id) return;
      errorDetalle = error.mensaje;
      cargandoDetalle = false;
    } catch (_) {
      if (alertaAbierta != id) return;
      errorDetalle = Textos.errorDeRed;
      cargandoDetalle = false;
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
      // Si el detalle está abierto, su «Qué pasó» tiene que reflejar la
      // atención: se recarga sin esperar a que la interfaz lo pida.
      if (alertaAbierta == id) {
        unawaited(_cargarDetalle(id));
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

  Future<void> cargarRecordatorios() async {
    try {
      recordatorios = await api.recordatorios();
      conectadoAlServidor = true;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        await cerrarSesion();
        return;
      }
      conectadoAlServidor = false;
    } catch (_) {
      conectadoAlServidor = false;
    }
    notifyListeners();
  }

  String? _horaValida(String? hora) {
    if (hora == null) return null;
    final List<String> partes = hora.split(':');
    if (partes.length != 2) return null;
    final int? h = int.tryParse(partes[0]);
    final int? m = int.tryParse(partes[1]);
    if (h == null || m == null || h < 0 || h > 23 || m < 0 || m > 59) {
      return null;
    }
    return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}';
  }

  /// Crea un recordatorio. Devuelve `true` si el servidor lo aceptó.
  ///
  /// La validación de aquí sólo evita una ida y vuelta por lo obvio (texto vacío,
  /// sin hora, sin día); las reglas de verdad —caracteres, fecha en el pasado,
  /// días coherentes con la repetición— las comprueba el servidor, y su mensaje
  /// es el que se enseña.
  Future<bool> crearRecordatorio({
    required String texto,
    required String? hora,
    required String repeticion,
    String? fecha,
    List<int>? dias,
  }) async {
    final String limpio = texto.trim();
    if (limpio.isEmpty) {
      aviso = Textos.faltaTextoRecordatorio;
      notifyListeners();
      return false;
    }
    final String? horaNormal = _horaValida(hora);
    if (horaNormal == null) {
      aviso = Textos.faltaHoraRecordatorio;
      notifyListeners();
      return false;
    }
    if (repeticion == 'una_vez' && (fecha == null || fecha.isEmpty)) {
      aviso = Textos.faltaDiaRecordatorio;
      notifyListeners();
      return false;
    }
    if (repeticion == 'semanal' && (dias == null || dias.isEmpty)) {
      aviso = Textos.faltaDiasRecordatorio;
      notifyListeners();
      return false;
    }

    cargandoRecordatorio = true;
    aviso = null;
    notifyListeners();
    try {
      final Recordatorio nuevo = await api.crearRecordatorio(
        texto: limpio,
        hora: horaNormal,
        repeticion: repeticion,
        fecha: repeticion == 'una_vez' ? fecha : null,
        dias: repeticion == 'semanal' ? dias : null,
      );
      final List<Recordatorio> copia = <Recordatorio>[nuevo, ...recordatorios]
        ..sort((Recordatorio a, Recordatorio b) =>
            a.proximoEn.compareTo(b.proximoEn));
      recordatorios = copia;
      aviso = Textos.recordatorioCreado;
      return true;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        cargandoRecordatorio = false;
        await cerrarSesion();
        return false;
      }
      aviso = error.mensaje;
      return false;
    } catch (_) {
      aviso = Textos.noSePudoCrearRecordatorio;
      return false;
    } finally {
      cargandoRecordatorio = false;
      notifyListeners();
    }
  }

  Future<void> cancelarRecordatorio(String id) async {
    if (cargandoRecordatorio) return;
    cargandoRecordatorio = true;
    aviso = null;
    notifyListeners();
    try {
      await api.cancelarRecordatorio(id);
      recordatorios = recordatorios
          .where((Recordatorio r) => r.id != id)
          .toList();
      aviso = Textos.recordatorioCancelado;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        cargandoRecordatorio = false;
        await cerrarSesion();
        return;
      }
      aviso = error.mensaje;
    } catch (_) {
      aviso = Textos.noSePudoCancelarRecordatorio;
    }
    cargandoRecordatorio = false;
    notifyListeners();
  }

  /// Cambia los minutos de inactividad. Devuelve `true` si el servidor lo aceptó.
  ///
  /// El servidor devuelve el valor que quedó guardado y con él se repinta el
  /// bloque, sin esperar a la siguiente instantánea. No se reinicia nada del
  /// orquestador: el cambio se nota en su siguiente vuelta (10 s), y eso la
  /// interfaz no lo puede adelantar.
  Future<bool> guardarInactividad(int minutos) async {
    if (cargandoInactividad) return false;
    cargandoInactividad = true;
    aviso = null;
    notifyListeners();
    try {
      final int guardado = await api.guardarInactividad(minutos);
      final Estado? actual = estado;
      if (actual != null) {
        estado = actual.conInactividad(Inactividad(
          ultimoMovimiento: actual.inactividad.ultimoMovimiento,
          tiempoMin: guardado,
          minimo: actual.inactividad.minimo,
          maximo: actual.inactividad.maximo,
        ));
      }
      aviso = Textos.inactividadGuardada;
      return true;
    } on ErrorApi catch (error) {
      if (error.sinSesion) {
        cargandoInactividad = false;
        await cerrarSesion();
        return false;
      }
      aviso = error.mensaje;
      return false;
    } catch (_) {
      aviso = Textos.noSePudoGuardarInactividad;
      return false;
    } finally {
      cargandoInactividad = false;
      notifyListeners();
    }
  }

  Future<void> aceptarAviso() async {
    avisoPendiente = false;
    await sesion.marcarAvisoVisto();
    notifyListeners();
  }

  Future<void> entrar(String codigo) async {
    final String? limpio = normalizarCodigo(codigo);
    if (limpio == null) {
      aviso = Textos.faltaCodigo;
      notifyListeners();
      return;
    }
    aviso = null;
    preparando = true;
    notifyListeners();
    try {
      final Map<String, dynamic> datos = await api.canjear(limpio);
      nombre = datos['nombre'] as String? ?? '';
      await sesion.guardarToken(api.token);
      await sesion.guardarNombre(nombre);
      preparando = false;
      notifyListeners();
      _empezar();
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
    // Al entrar a «Familiares» se vuelve a pedir la lista: es la única que
    // cambia por fuera (alta o baja en Telegram) y no llega por WebSocket.
    if (nuevo == Destino.familiares) {
      unawaited(cargarFamiliares());
    }
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
  const AsistentePanel({super.key, this.estado, this.arrancar = true});

  final EstadoPanel? estado;
  final bool arrancar;

  @override
  State<AsistentePanel> createState() => _AsistentePanelState();
}

class _AsistentePanelState extends State<AsistentePanel> {
  late final EstadoPanel _estado;

  @override
  void initState() {
    super.initState();
    _estado = widget.estado ?? EstadoPanel();
    if (widget.arrancar) {
      _estado.arrancar();
    }
  }

  @override
  void dispose() {
    if (widget.estado == null) {
      _estado.dispose();
    }
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

    if (estado.errorArranque != null) {
      return PantallaErrorArranque(mensaje: estado.errorArranque!);
    }

    // El detalle de una alerta tapa la pantalla de `destino` y «volver» lo
    // borra: así se abre desde Inicio o desde Alertas y se vuelve al sitio de
    // donde se vino, sin perder la navegación de abajo ni la de arriba.
    if (estado.alertaAbierta != null) {
      return PantallaAlertaDetalle(id: estado.alertaAbierta!);
    }

    switch (estado.destino) {
      case Destino.inicio:
        return const PantallaPanel();
      case Destino.alertas:
        return const PantallaAlertas();
      case Destino.recordatorios:
        return const PantallaRecordatorios();
      case Destino.mensajes:
        return const PantallaProximamente(
            titulo: Textos.mensajes,
            texto: Textos.mensajesVacio);
      case Destino.familiares:
        return const PantallaFamiliares();
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
