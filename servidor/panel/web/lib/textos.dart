class Textos {
  Textos._();

  static const String asistenteDeVoz = 'Asistente de Voz';
  static const String salaPrincipal = 'Sala principal';
  static const String panel = 'Panel';
  static const String historialDeAlertas = 'Historial de alertas';

  static const String inicio = 'Inicio';
  static const String alertas = 'Alertas';
  static const String recordatorios = 'Recordatorios';
  static const String mensajes = 'Mensajes';
  static const String barraAgenda = 'Agenda';
  static const String barraFamilia = 'Familia';
  static const String verLaConversacion = 'Ver la conversación';

  static const String panelDeLaSala = 'Panel de la sala';

  static const String pasoUno =
      'Escriba /panel al bot de la casa en Telegram.';
  static const String pasoDos =
      'Le enviará un código de seis caracteres.';
  static const String pasoTres =
      'Escríbalo aquí y entre. Vale diez minutos y una sola vez.';
  static const String yaLoTiene = '¿Ya tiene su código?';
  static const String ayudaCodigo = 'Escríbalo tal cual: no distingue entre '
      'mayúsculas y minúsculas, y puede pegarlo.';
  static const String pistaCodigo = 'Código de seis caracteres';
  static const String entrarAlPanel = 'Entrar al panel';
  static const String faltaCodigo =
      'Escriba el código de seis caracteres que le envió el bot por Telegram.';
  static const String codigoNoReconocido =
      'Ese código no tiene seis caracteres. Cópielo del mensaje del bot y '
      'vuelva a intentarlo.';
  static const String errorDeRed = 'No se pudo contactar con el servidor. '
      'Compruebe la conexión e inténtelo de nuevo.';

  static const String susDatos = 'Sus datos';
  static const String avisoUno = 'Este panel muestra el estado de la casa: la '
      'luz, la temperatura, el gas y las alertas. La información se guarda en '
      'el servidor de la casa. Solo sale de ella para mostrarse en este panel y '
      'para enviar las alertas por Telegram.';
  static const String avisoDos = 'Solo pueden entrar los familiares '
      'registrados en el bot de Telegram. Cada código sirve una sola vez y la '
      'sesión vence a los 30 días.';
  static const String avisoTres = 'El asistente no guarda grabaciones de lo '
      'que se dice en la casa. El panel no tiene micrófono ni cámara y no pide '
      'su ubicación.';
  static const String avisoCuatro = 'Si algo de esto cambia, se le avisará por '
      'Telegram antes del cambio.';
  static const String entendido = 'Entendido, continuar';
  static const String piePrivacidad = 'El asistente no guarda grabaciones de '
      'voz. El panel no tiene micrófono ni cámara.';

  static const String sinConexion = 'Sin conexión';
  static const String sinConexionSala = 'Sin conexión con la sala';
  static const String salaConectada = 'Sala conectada';
  static const String conectada = 'Conectada';
  static const String casaNoResponde = 'La casa no responde';

  static const String sinConexionServidor = 'Sin conexión con el servidor';
  static const String sinConexionServidorTexto = 'No se está recibiendo '
      'información. Se muestra la última información recibida.';
  static const String casaNoRespondeTexto = 'El panel sigue conectado, pero la '
      'casa no ha dado señales desde hace un rato.';
  static const String sinConexionSalaTexto = 'Los datos de abajo son los últimos '
      'recibidos. La alarma local de gas y el botón de luz siguen funcionando en '
      'la sala.';

  static const String luzDeLaSala = 'Luz de la sala';
  static const String encendida = 'Encendida';
  static const String apagada = 'Apagada';
  static const String sinDatos = 'Sin datos';
  static const String sinSensor = 'Sin sensor';
  static const String todaviaNoHayLuz = 'Todavía no hay información de la luz.';
  static const String sinDatosDeLaLuz = 'Sin datos de la luz';
  static const String apagarLaLuz = 'Apagar la luz';
  static const String encenderLaLuz = 'Encender la luz';
  static const String luzNoInstalada = 'La luz no está instalada en esta casa.';

  static const String ambiente = 'Ambiente';
  static const String temperatura = 'Temperatura';
  static const String humedad = 'Humedad';
  static const String gas = 'Gas';
  static const String movimiento = 'Movimiento';
  static const String noSeDetectaGas = 'No se detecta gas';
  static const String ahoraMismo = 'Ahora mismo';
  static const String movimientoDetectado = 'Movimiento detectado';
  static const String sinMovimiento = 'Sin movimiento';
  static String sinMovimientoMinutos(int min) =>
      'No se ha detectado movimiento en $min minutos.';
  static const String noSeDetectaNadie = 'Sin movimiento';
  static const String ultimaLectura = 'Última lectura: ';

  static const String avisoInactividad = 'Aviso de inactividad · ';
  static const String ultimoMovimiento = 'Último movimiento';
  static const String sinDatosDelSensor = 'Sin datos del sensor';
  static const String avisarSiNoHayMovimiento =
      'Avisar si no hay movimiento en';
  static const String minutosUnidad = 'minutos';
  static const String guardarInactividad = 'Guardar';
  static const String inactividadGuardada = 'Aviso de inactividad guardado.';
  static const String noSePudoGuardarInactividad =
      'No se pudo guardar el aviso de inactividad.';
  static const String soloNumerosDeMinutos =
      'Escriba solo el número de minutos.';

  static const String sensoresYLuz = 'Sensores y luz';
  static const String conexionConLaCasa = 'Conexión con la casa';
  static const String noSeUsaAqui = 'No se usa en esta instalación';

  static const String verAviso = 'Ver el aviso de privacidad';
  static const String cerrarSesion = 'Cerrar sesión';
  static const String cerrarSesionTitulo = '¿Cerrar sesión?';
  static const String cerrarSesionTexto = 'En este navegador se borrará la '
      'sesión. Para volver a entrar hará falta un código nuevo con /panel.';
  static const String cancelar = 'Cancelar';

  static const String alertasRecientes = 'Alertas recientes';
  static const String verTodas = 'Ver todas';
  static const String nuevo = 'Nuevo';
  static const String sinAlertas =
      'Todavía no ha habido ninguna alerta en esta casa.';
  static const String alertasSubtitulo = 'Lo más reciente arriba. Si alguien '
      'la atendió desde Telegram, aquí se refleja.';
  static const String alertasBorradoAuto =
      'Las alertas se borran automáticamente después de 30 días.';
  static const String atendida = 'Atendida';
  static const String sinAutor = 'Sin autor';
  static const String sinTexto = 'Sin texto';
  static const String descartada = 'Descartada';
  static const String enviada = 'Enviada';
  static const String pendiente = 'Pendiente';
  static const String alertaActivaPildora = 'Alerta activa';
  static const String pidenAyuda = 'Piden ayuda';
  static const String sinMovimientoPildora = 'Sin movimiento';
  static const String aviso = 'Aviso';

  static const String pidenAyudaEnLaSala = 'Piden ayuda en la sala';
  static const String sinMovimientoEnLaSala = 'Sin movimiento en la sala';
  static const String avisoEnLaSala = 'Aviso en la sala';

  static const String ayudaPorVoz = 'Se pidió ayuda por voz.';
  static const String ayudaPorAlexa = 'Se pidió ayuda por Alexa.';
  static const String ayudaGenerica = 'Se pidió ayuda.';
  static const String seRecibioUnAviso = 'Se recibió un aviso.';
  static const String alertaDeGas = 'Alerta de gas';
  static const String peticionDeAyuda = 'Petición de ayuda';
  static const String sinMovimientoEnLaCasa = 'Sin movimiento en la casa';
  static const String haDichoTenerMiedo = 'Ha dicho que tiene miedo';
  static const String otraPersonaAtendio =
      'Otra persona ya había atendido esta alerta.';

  static const String noSePudoAbrir = 'No se pudo abrir el panel';
  static const String volverAIntentar = 'Volver a intentar';
  static const String noSePudoEnviarOrden = 'No se pudo enviar la orden.';
  static const String noSePudoAtender = 'No se pudo atender la alerta.';
  static const String noSePudoCompletar = 'No se pudo completar la solicitud.';

  static const String recordatoriosVacio = 'Todavía no hay recordatorios en '
      'esta casa. Cuando existan, aparecerán aquí con la hora de cada uno.';
  static const String proximoRecordatorio = 'Próximo recordatorio';
  static const String ultimaAlerta = 'Última alerta';
  static const String recordatoriosSubtitulo = 'Lo que el asistente dice en '
      'voz alta a su hora.';
  static const String nuevoRecordatorio = 'Nuevo recordatorio';
  static const String nuevoRecordatorioSubtitulo =
      'El asistente lo dirá en voz alta a la hora que elija.';
  static const String queTieneQueDecir = 'Qué tiene que decir';
  static const String pistaTextoRecordatorio =
      'Por ejemplo: tomar la medicina';
  static const String aQueHora = 'A qué hora';
  static const String cuando = 'Cuándo';
  static const String unaSolaVez = 'Una sola vez';
  static const String todosLosDias = 'Todos los días';
  static const String ciertosDias = 'Ciertos días';
  static const String queDia = 'Qué día';
  static const String elegirHora = 'Elegir la hora';
  static const String elegirDia = 'Elegir el día';
  static const String lunesAViernes = 'Lunes a viernes';
  static const String crearRecordatorio = 'Crear recordatorio';
  static const String cancelarRecordatorio = 'Cancelar este recordatorio';
  static const String proximaVez = 'La próxima vez: ';
  static const String noSePudoDecir = 'La última vez no se pudo decir: el '
      'asistente no estaba conectado.';
  static const String recordatorioCreado = 'Recordatorio creado.';
  static const String recordatorioCancelado = 'Recordatorio cancelado.';
  static const String noSePudoCrearRecordatorio =
      'No se pudo crear el recordatorio.';
  static const String noSePudoCancelarRecordatorio =
      'No se pudo cancelar el recordatorio.';
  static const String faltaTextoRecordatorio =
      'Escriba lo que tiene que decir el recordatorio.';
  static const String faltaHoraRecordatorio = 'Elija la hora.';
  static const String faltaDiaRecordatorio = 'Elija el día.';
  static const String faltaDiasRecordatorio =
      'Elija al menos un día de la semana.';

  /// Los días ISO en su nombre: el índice 0 es lunes y el 6, domingo.
  static const List<String> diasSemana = <String>[
    'Lunes',
    'Martes',
    'Miércoles',
    'Jueves',
    'Viernes',
    'Sábado',
    'Domingo',
  ];

  /// Los meses en su nombre: el índice 0 es enero y el 11, diciembre.
  static const List<String> meses = <String>[
    'Enero',
    'Febrero',
    'Marzo',
    'Abril',
    'Mayo',
    'Junio',
    'Julio',
    'Agosto',
    'Septiembre',
    'Octubre',
    'Noviembre',
    'Diciembre',
  ];
  static const String mensajesVacio =
      'Esta función estará disponible próximamente.';
  static const String proximamente = 'Próximamente';

  // Chat
  static const String chatPlaceholder = 'Escriba un mensaje para la sala';
  static const String chatEnviar = 'Enviar a la sala';
  static const String chatDemasiadoLargo = 'El mensaje no puede pasar de 200 caracteres.';
  static const String chatDemasiadoRapido = 'Solo puede enviar un mensaje cada 5 segundos.';
  static const String verMasAntiguos = 'Ver más antiguos';
  static const String cargando = 'Cargando...';
  static const String yaLoAtendi = 'Ya lo atendí';
  static const String volverAlInicio = 'Volver al inicio';

  static const String luzEnviadaEncender = 'Se envió la orden de encender la '
      'luz. El estado se actualizará en unos segundos.';
  static const String luzEnviadaApagar = 'Se envió la orden de apagar la luz. '
      'El estado se actualizará en unos segundos.';

  // --- La cabecera y la navegación -------------------------------------------

  static const String familiares = 'Familiares';

  // --- La tarjeta de gas -----------------------------------------------------

  static const String gasNormal = 'Normal';
  static const String gasElevado = 'Gas elevado';
  static const String gasDetectado = 'Gas detectado';
  static const String lectura = 'Lectura: ';
  static const String umbral = 'Umbral: ';
  static const String gasSinUmbral = 'Sin umbral configurado';

  // --- La tarjeta de movimiento ---------------------------------------------

  static const String cambiar = 'Cambiar';
  static const String pausado = 'Pausado';
  static const String avisoDeInactividad = 'Aviso de inactividad a los ';
  static const String dialogoInactividad = 'Aviso por inactividad';

  // --- Equipos y mensaje -----------------------------------------------------

  static const String equipos = 'Equipos';
  static const String nodoDeLaSala = 'Nodo de la sala';
  static const String servidor = 'Servidor';
  static const String enLinea = 'En línea';
  static const String mensajeParaLaSala = 'Mensaje para la sala';

  // --- La alerta activa y su detalle -----------------------------------------

  static const String alertaActiva = 'Alerta activa en la sala';
  static const String ver = 'Ver';
  static const String quePaso = 'Qué pasó';
  static const String alertaEnLaSala = 'Alerta · Sala principal';
  static const String verEstadoDeLaSala = 'Ver el estado de la sala';
  static const String avisoEmergencia = 'Si nadie responde en la sala, '
      'considera llamar a alguien cercano o a los servicios de emergencia.';
  static const String seCreoLaAlerta = 'Se creó esta alerta.';
  static const String seDescartoLaAlerta = 'Se descartó esta alerta.';
  static const String seAtendioLaAlerta = 'Se atendió esta alerta.';
  static const String volver = 'Volver';
  static const String noSePudoCargarLaAlerta = 'No se pudo cargar la alerta.';

  // --- Familiares ------------------------------------------------------------

  static const String familiaresSubtitulo =
      'Estas personas reciben los avisos de esta casa.';
  static const String sinFamiliares =
      'Todavía no hay ningún familiar registrado en esta casa.';
  static const String sinFamiliaresAyuda = 'Para añadir a alguien, escriba '
      '/registro seguido del código al bot de la casa en Telegram.';
  static const String nombreSinDefinir = 'Familiar';
  static const String avisoFamiliares = 'La lista sale de Telegram y sólo '
      'incluye a los familiares activos: los que de verdad reciben los avisos.';
  static const String reintentar = 'Reintentar';
}

String familiares(int n) =>
    n == 1 ? '1 familiar registrado' : '$n familiares registrados';

String minutos(int n) => n == 1 ? '1 minuto' : '$n minutos';

String horas(int n) => n == 1 ? '1 hora' : '$n horas';

/// El error en línea del ajuste de inactividad. La franja sale del servidor, que
/// es quien la hace cumplir; aquí sólo se dice con esas cifras.
String minutosFueraDeRango(int minimo, int maximo) =>
    'Escriba un número entre $minimo y $maximo minutos.';

/// La línea del sensor de gas en el detalle de una alerta: la lectura que saltó
/// y, si el servidor la tiene, contra qué umbral saltó (decisión A.2: el panel
/// expone `gas_on_adc`).
String parrafoDeGas(num gasAdc, num? umbral) => umbral == null
    ? '${Textos.lectura}$gasAdc'
    : '${Textos.lectura}$gasAdc · ${Textos.umbral}$umbral';

/// La línea de inactividad, con sus minutos siempre en plural bien dicho.
String parrafoDeInactividad(int min) =>
    'Sin movimiento durante ${minutos(min)}';


/// Qué ocurrió en una alerta, sólo con lo que la alerta trae de verdad.
///
/// Se manda la `carga` tal cual de la base y el umbral de gas actual del
/// servidor; si algo no viene, no se pinta (decisión B.3). Devuelve `null` cuando
/// no hay nada que contar con honestidad.
String? quePasoDe(String tipo, Map<String, dynamic> carga,
    {num? umbralGas}) {
  if (tipo == 'gas') {
    final Object? g = carga['gas_adc'];
    if (g is num) return parrafoDeGas(g, umbralGas);
  }
  if (tipo == 'inactividad') {
    final Object? m = carga['minutos'];
    if (m is num) return parrafoDeInactividad(m.toInt());
  }
  if (tipo == 'ayuda') {
    final Object? origen = carga['origen'];
    if (origen == null) return 'Se creó la alerta: pidieron ayuda.';
    final String o = origen.toString();
    if (o == 'voz') return 'Se creó la alerta: pidieron ayuda por voz.';
    if (o == 'alexa') return 'Se creó la alerta: pidieron ayuda por Alexa.';
    if (o == 'boton') return 'Se creó la alerta: pidieron ayuda con el botón.';
    if (o == 'panel') return 'Se creó la alerta: pidieron ayuda desde el panel.';
    return 'Se creó la alerta: pidieron ayuda.';
  }
  final Object? origen = carga['origen'];
  if (origen == null) return null;
  return detalleOrigen(origen.toString());
}

/// El detalle del origen de una alerta de ayuda, como lo dice el bot.
/// Devuelve null para lo que no se sabe decir, y entonces no se pinta la línea.
String? detalleOrigen(String? origen) {
  switch (origen) {
    case 'boton':
      return 'Se pidió por medio del botón de ayuda';
    case 'voz':
      return 'Se pidió por medio del asistente de voz';
    case 'alexa':
      return 'Se pidió por medio de Alexa';
    case 'panel':
      return 'Se pidió desde el panel';
    default:
      return null;
  }
}

String origenDeLuz(String? origen) {
  switch (origen) {
    case 'voz':
      return 'Por voz';
    case 'panel':
      return 'Desde el panel';
    case 'boton':
      return 'Con el botón';
    case 'alexa':
      return 'Con Alexa';
    case 'arranque':
      return 'Tras un reinicio';
    default:
      return 'Origen desconocido';
  }
}

/// Frase corta para la cabecera: "hace X s", "hace X min", "hace X h".
String haceCorto(int? epoch, {int? ahora}) {
  if (epoch == null) return '';
  final int base = ahora ?? (DateTime.now().millisecondsSinceEpoch ~/ 1000);
  int seg = base - epoch;
  if (seg < 0) seg = 0;
  if (seg < 5) return 'ahora mismo';
  if (seg < 60) return 'hace $seg s';
  if (seg < 3600) {
    final int m = seg ~/ 60;
    return 'hace $m min';
  }
  final int h = seg ~/ 3600;
  return 'hace $h h';
}

/// Para el banner sin conexión si se quiere sin tiempo.
String ultimoDatoSinTiempo() => Textos.sinConexionSala;

/// El nombre de una repetición, como se elige en el formulario.
String repeticionDe(String repeticion) {
  switch (repeticion) {
    case 'una_vez':
      return Textos.unaSolaVez;
    case 'diario':
      return Textos.todosLosDias;
    case 'semanal':
      return Textos.ciertosDias;
    default:
      return repeticion;
  }
}

/// Los días de un semanal en una línea: «Lunes, miércoles y viernes».
///
/// Se ordenan y se filtran aquí aunque el servidor ya los mande ordenados: la
/// función es también la que usa el formulario antes de guardar, y un día fuera
/// de rango no debe pintar un índice inválido.
String diasDe(List<int> dias) {
  final List<int> ordenados =
      dias.where((int d) => d >= 1 && d <= 7).toSet().toList()..sort();
  final List<String> nombres =
      ordenados.map((int d) => Textos.diasSemana[d - 1].toLowerCase()).toList();
  if (nombres.isEmpty) return '';
  if (nombres.length == 1) return nombres.first;
  return '${nombres.sublist(0, nombres.length - 1).join(', ')} y ${nombres.last}';
}

/// La frase de la última entrega cuando sí se dijo. `cuando` ya viene formateado
/// por `cuandoSeDijo`, porque la frase vive aquí y el formato de fechas allí.
String ultimaVezSeDijo(String cuando) => 'La última vez se dijo $cuando.';

/// El aviso de inactividad con sus minutos, como se pinta en la tarjeta de
/// movimiento: «Aviso de inactividad a los 120 min».
String avisoInactividadAlos(int minutosMin) =>
    'Aviso de inactividad a los ${minutosMin.toString()} min · ';

/// Quién atendió una alerta, con su hora. Si el nombre falta (una alerta muy
/// antigua, anterior al campo), sólo se dice cuándo: inventar «Familiar» aquí
/// sería dar un nombre a alguien que no lo tiene.
String atendidaCuando(String cuando, String? quien) {
  final String nombre = (quien ?? '').trim();
  return nombre.isEmpty
      ? '${Textos.atendida} $cuando'
      : '${Textos.atendida} $cuando por $nombre';
}

String etiquetaEstadoAlerta(String estado) {
  if (estado == 'resuelta' || estado == 'atendida') return Textos.atendida;
  if (estado == 'pendiente' || estado == 'enviada') return Textos.pendiente;
  if (estado == 'descartada') return Textos.descartada;
  return estado;
}

/// Un evento de «Qué pasó» en una frase, tal como lo cuenta el servidor.
///
/// Sólo se llama con los tipos que `GET /api/alertas/{id}` devuelve de verdad
/// (`creada`, `enviada`, `pendiente`, `atendida`, `descartada`); lo demás se
/// enseña tal cual, sin inventarle hechos a una línea de tiempo.
String eventoAlerta(String tipo, String? nombre) {
  final String quien = (nombre ?? '').trim();
  switch (tipo) {
    case 'creada':
      return Textos.seCreoLaAlerta;
    case 'enviada':
      return quien.isEmpty
          ? 'Se envió el aviso por Telegram.'
          : 'Se envió el aviso por Telegram a $quien.';
    case 'pendiente':
      return quien.isEmpty
          ? 'El aviso por Telegram sigue pendiente de envío.'
          : 'El aviso para $quien sigue pendiente de envío.';
    case 'atendida':
      return quien.isEmpty ? Textos.seAtendioLaAlerta : '$quien la atendió.';
    case 'descartada':
      return Textos.seDescartoLaAlerta;
    default:
      return tipo;
  }
}

/// El nombre de la alerta en pantalla. El servidor manda `gas`, `ayuda`,
/// `inactividad` y `miedo`; se traducen aquí y no en cada pantalla.
String tituloAlerta(String tipo) {
  switch (tipo) {
    case 'gas':
      return Textos.alertaDeGas;
    case 'ayuda':
      return Textos.peticionDeAyuda;
    case 'inactividad':
      return Textos.sinMovimientoEnLaCasa;
    case 'miedo':
      return Textos.haDichoTenerMiedo;
    default:
      return tipo;
  }
}
