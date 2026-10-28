"""Todas las frases que el orquestador dice o escribe, en un solo sitio.

Hay dos públicos, y por eso dos secciones:

- **Hablado**: lo que el satélite dice en voz alta en la casa. Lo oye la persona
  mayor, así que son frases cortas, de usted y sin abreviaturas que Piper lea mal.
- **Telegram**: lo que lee la familia. También de usted, en texto plano (sin
  Markdown, ver `alertas.texto_para_telegram`).

Reglas de redacción que se cumplen aquí y no en quien llama:

- Singular y plural se resuelven en el código, con una función por frase que
  lleva un número. Nunca «aviso(s)» ni «registrado(s)».
- «de» + «el» se contrae en «del» (`_contraer`). Las plantillas con «de {algo}»
  lo pasan por ahí, para que «el panel» dé «por medio del panel».

Corregir una frase es cambiarla aquí. Las pruebas que fijan textos importan este
módulo en vez de copiar la frase, salvo las que comprueban la redacción exacta.
"""

import time

# =============================================================================
# Hablado (satelite/decir)
# =============================================================================

NO_ENTENDIDO = "Perdón, no le entendí. ¿Me lo repite?"

# Paso 3: respuestas de las intenciones de voz.
RESPUESTA_ENCENDER_LUZ = "Con gusto. Ya encendí la luz."
RESPUESTA_APAGAR_LUZ = "Listo, apagué la luz."
LUZ_YA_ENCENDIDA = "La luz ya está encendida."
LUZ_YA_APAGADA = "La luz ya está apagada."
NO_PUDO_ENCENDER_LUZ = "No pude encender la luz."
NO_PUDO_APAGAR_LUZ = "No pude apagar la luz."
RESPUESTA_AYUDA = "No se preocupe, ya avisé a su familia."
RESPUESTA_AYUDA_RECIENTE = "Ya avisé a su familia hace un momento. Alguien se comunicará con usted pronto."
RESPUESTA_CANCELAR = "De acuerdo, cancelado."
RESPUESTA_CHAT_ENVIADA = "Listo, le envié su respuesta."
PERDON_NO_ESCUCHO = "Perdón, no le escuché bien. ¿Me lo repite?"

# El eco de prueba (se queda hasta el Paso 3).
ECO = "Usted dijo: {texto}."

AVISO_ATENDIDA = {
    "gas": "Su familia ya recibió la alerta de gas y la está atendiendo.",
    "ayuda": "Su familia ya recibió su petición de ayuda y la está atendiendo.",
}
AVISO_ATENDIDA_OTRO = "Su familia ya recibió el aviso y lo está atendiendo."


def eco(texto: str) -> str:
    """El eco de lo reconocido. Quien llama ya descartó `[unk]` y el vacío."""
    return ECO.format(texto=texto.strip())


def aviso_atendida(tipo: str) -> str:
    return AVISO_ATENDIDA.get(tipo, AVISO_ATENDIDA_OTRO)


# Las alertas habladas (Fase 2C): las dice el satelite en cuanto el orquestador
# registra la alerta. `{lugar}` lleva su articulo (`lugar_con_articulo`).
ALERTA_HABLADA_GAS = (
    "Atención: se detectó gas en {lugar}. Por favor, no encienda ni apague luces "
    "ni aparatos, abra las ventanas y salga de la casa. Estoy avisando a su familia."
)
ALERTA_HABLADA_AYUDA = ("No se preocupe, estoy avisando a su familia. Alguien se "
                        "comunicará con usted pronto.")


def alerta_hablada(tipo: str, lugar: str):
    """El texto hablado de una alerta, o `None` si el tipo no tiene."""
    if tipo == "gas":
        return ALERTA_HABLADA_GAS.format(lugar=lugar)
    if tipo == "ayuda":
        return ALERTA_HABLADA_AYUDA
    return None


def recordatorio_hablado(texto: str) -> str:
    """La frase con la que el satélite dice un recordatorio.

    Es "{texto}" dos veces, con la misma normalización en las dos: espacios
    dobles/múltiples a uno, y sin duplicar el punto final si el texto ya termina
    en punto. Se recorta antes de meter el texto, y no después, porque el punto
    que agrega la plantilla va pegado al texto.

    El texto ya pasó por la validación de caracteres de la API (letras, dígitos,
    signos de uso común, sin emojis), así que aquí no hay que quitarlos: solo
    normalizar espacios y el punto.
    """
    limpio = " ".join((texto or "").split())
    if limpio.endswith("."):
        limpio = limpio[:-1]
    return "Le recuerdo: {texto}. Repito: {texto}.".format(texto=limpio)


# =============================================================================
# Utilidades de redacción
# =============================================================================

def _contraer(frase: str) -> str:
    """«de el» -> «del». Solo el artículo en minúscula: «de El Salvador» no."""
    return frase.replace(" de el ", " del ")


# El artículo de los lugares más comunes de una casa. Un lugar que no esté aquí
# se escribe en `UBICACION_GAS` con su artículo («el cuarto de lavado»).
_ARTICULOS = {
    "cocina": "la", "sala": "la", "estancia": "la", "recámara": "la",
    "recamara": "la", "habitación": "la", "habitacion": "la",
    "lavandería": "la", "lavanderia": "la", "bodega": "la", "azotea": "la",
    "comedor": "el", "baño": "el", "bano": "el", "cuarto": "el",
    "dormitorio": "el", "patio": "el", "garaje": "el", "pasillo": "el",
    "sótano": "el", "sotano": "el", "estudio": "el", "cuarto de servicio": "el",
    "cuarto de lavado": "el", "zotehuela": "la",
}
_ARTICULOS_VALIDOS = ("el ", "la ", "los ", "las ")
LUGAR_POR_DEFECTO = "cocina"


def lugar_con_articulo(valor: str) -> str:
    """`cocina` -> `la cocina`. Si ya trae artículo, se respeta.

    Formato de `UBICACION_GAS`: el nombre del lugar en minúsculas, con o sin
    artículo (`cocina`, `la cocina`, `el cuarto de lavado`). Sin artículo, se toma
    de la tabla de arriba; si el lugar no está en la tabla, se usa «la» si
    termina en «a» y «el» en otro caso. Vacío = «la cocina».
    """
    lugar = " ".join((valor or "").split()).lower() or LUGAR_POR_DEFECTO
    if lugar.startswith(_ARTICULOS_VALIDOS):
        return lugar
    articulo = _ARTICULOS.get(lugar)
    if articulo is None:
        articulo = "la" if lugar.endswith("a") else "el"
    return "%s %s" % (articulo, lugar)


def lugar_conocido(valor: str) -> bool:
    """¿El artículo de este lugar es seguro (tabla o escrito a mano)?"""
    lugar = " ".join((valor or "").split()).lower() or LUGAR_POR_DEFECTO
    return lugar.startswith(_ARTICULOS_VALIDOS) or lugar in _ARTICULOS


# =============================================================================
# Telegram: avisos de alerta
# =============================================================================

PIE = ("Este aviso lo envía el asistente de la casa de forma automática.\n"
       "Referencia de la alerta: {id}")

PASO_1_GAS = ("1. Llame de inmediato a la persona que está en la casa. Pídale que "
              "no encienda ni apague luces ni aparatos, que abra puertas y ventanas "
              "y que salga de la casa.")
PASO_1_AYUDA = "1. Llame de inmediato a la persona que está en la casa."
PASOS_2_Y_3 = ("2. Si no contesta o la situación es grave, llame al 911.\n"
               "3. Cuando todo esté resuelto, presione «Ya lo atendí».")

ALERTA_GAS = (
    "ALERTA DE GAS en la casa.\n\n"
    "El sensor de {lugar} detectó gas (valor: {valor}).\n\n"
    "Qué hacer:\n" + PASO_1_GAS + "\n" + PASOS_2_Y_3 + "\n\n" + PIE
)

ALERTA_AYUDA = (
    "PETICIÓN DE AYUDA desde la casa.\n\n"
    "{linea_origen}\n\n"
    "Qué hacer:\n" + PASO_1_AYUDA + "\n" + PASOS_2_Y_3 + "\n\n" + PIE
)

# La inactividad es distinta de las otras dos: no hay un botón que nadie pulsó ni
# un sensor que midió algo raro, sino AUSENCIA de señales durante demasiado
# tiempo. El lugar de la frase («la sala») es donde vive el sensor de movimiento,
# y va escrito en el texto porque es lo que le dice a la familia POR QUÉ no hay
# noticias: no es que el sistema no sepa nada, es que el PIR no ha visto pasar a
# nadie por la sala. La primera acción es la misma que en la ayuda —llamar—, así
# que se reutiliza `PASO_1_AYUDA`.
ALERTA_INACTIVIDAD = (
    "No se ha detectado movimiento en la sala en los últimos {minutos} minutos.\n\n"
    "Qué hacer:\n" + PASO_1_AYUDA + "\n" + PASOS_2_Y_3 + "\n\n" + PIE
)

# Los códigos de `origen` del contrato (config.ORIGENES_VALIDOS), en palabras.
ORIGENES = {
    "boton": "el botón de ayuda",
    "voz": "el asistente de voz",
    "alexa": "Alexa",
    "panel": "el panel",
}
AYUDA_CON_ORIGEN = "Se pidió ayuda por medio de {origen}."
AYUDA_SIN_ORIGEN = "Se pidió ayuda desde la casa."

ALERTA_DESCONOCIDA = "Aviso de la casa: {texto}\n\nReferencia de la alerta: {id}"


def alerta_gas(lugar: str, valor, id_alerta: str) -> str:
    return _contraer(ALERTA_GAS.format(lugar=lugar, valor=valor, id=id_alerta))


def linea_origen(origen) -> str:
    """La segunda línea de la petición de ayuda. Desconocido = sin origen."""
    nombre = ORIGENES.get(origen) if isinstance(origen, str) else None
    if nombre is None:
        return AYUDA_SIN_ORIGEN
    return _contraer(AYUDA_CON_ORIGEN.format(origen=nombre))


def alerta_ayuda(origen, id_alerta: str) -> str:
    return ALERTA_AYUDA.format(linea_origen=linea_origen(origen), id=id_alerta)


def alerta_inactividad(minutos, id_alerta: str) -> str:
    """El aviso de que la casa lleva demasiado tiempo sin movimiento.

    `minutos` es el número que quedó cuando se disparó la alerta, y se imprime tal
    cual: si el ajuste de la familia son 120 minutos, el texto dice 120, y no una
    cifra redondeada que no coincide con lo que ven en el panel. Un valor que no
    sea un entero no debería llegar aquí (lo pone el propio servicio), pero si
    llegara se imprime lo que sea antes que romper el aviso.
    """
    return ALERTA_INACTIVIDAD.format(minutos=minutos, id=id_alerta)


def alerta_desconocida(texto: str, id_alerta: str) -> str:
    return ALERTA_DESCONOCIDA.format(texto=texto, id=id_alerta)


ATENDIDA = "ATENDIDA.\n\n{frase} a las {hora}.\n\nReferencia de la alerta: {id}"
ATENDIDA_FRASE = {
    "gas": "La alerta de gas se marcó como atendida",
    "ayuda": "La petición de ayuda se marcó como atendida",
    "inactividad": "El aviso de inactividad se marcó como atendido",
}
ATENDIDA_FRASE_OTRO = "El aviso se marcó como atendido"


def atendida(tipo: str, cuando: int, id_alerta: str) -> str:
    return ATENDIDA.format(
        frase=ATENDIDA_FRASE.get(tipo, ATENDIDA_FRASE_OTRO),
        hora=time.strftime("%H:%M", time.localtime(cuando)),
        id=id_alerta,
    )


# =============================================================================
# Telegram: el bot
# =============================================================================

BOTON_ABRIR_PANEL = "Abrir panel"
BOTON_ATENDI = "Ya lo atendí"

PEDIR_REGISTRO = (
    "Falta el código.\n\n"
    "Escriba:\n"
    "/registro CÓDIGO\n\n"
    "El código se lo entrega la persona que instaló el asistente."
)


def pedir_registro(registradas: int) -> str:
    if registradas == 1:
        return (PEDIR_REGISTRO + "\n\nYa hay una persona registrada. Si usted se "
                "registra, ambos recibirán las alertas.")
    if registradas > 1:
        return (PEDIR_REGISTRO + "\n\nYa hay %d personas registradas. Si usted se "
                "registra, todos recibirán las alertas." % registradas)
    return PEDIR_REGISTRO


REGISTRO_FALLIDO = "No se pudo completar el registro: {motivo}.\n\nPida un código nuevo."

# Los motivos de `registro.comprobar_codigo`. Salen al familiar dentro de
# REGISTRO_FALLIDO y también al log.
MOTIVO_NO_EXISTE = "el código no existe"
MOTIVO_YA_USADO = "el código ya se usó"
MOTIVO_ANULADO = "el código se anuló después de {n} intentos"
MOTIVO_VENCIDO = "el código ya venció (valía {minutos} minutos)"


def registro_fallido(motivo: str) -> str:
    return REGISTRO_FALLIDO.format(motivo=motivo)


REGISTRO_LISTO = (
    "Listo. Desde ahora le avisaré aquí si hay una alerta de gas o una petición "
    "de ayuda.\n\n"
    "Para ver el estado de las alertas, use /alertas."
)


def registro_listo(pendientes: int, hay_mas_personas: bool) -> str:
    texto = REGISTRO_LISTO
    if pendientes == 1:
        texto += "\n\nAdemás, le llegará un aviso que estaba pendiente."
    elif pendientes > 1:
        texto += ("\n\nAdemás, le llegarán %d avisos que estaban pendientes."
                  % pendientes)
    if hay_mas_personas:
        texto += ("\n\nLas próximas alertas las recibirán todas las personas "
                  "registradas.")
    return texto


# --- /alertas ---------------------------------------------------------------

ALERTAS_TITULO = "Estado de las alertas:"
ALERTAS_ESTADOS = (
    ("pendiente", "Pendientes"),
    ("enviada", "Enviadas"),
    ("resuelta", "Atendidas"),
    ("descartada", "Descartadas"),
)
ALERTAS_ULTIMAS = "Últimas cinco:"
ALERTAS_NINGUNA = "Todavía no hay ninguna alerta."

NOMBRE_TIPO = {"gas": "alerta de gas", "ayuda": "petición de ayuda"}
NOMBRE_ESTADO = {"pendiente": "pendiente", "enviada": "enviada",
                 "resuelta": "atendida", "descartada": "descartada"}


def personas_registradas(n: int) -> str:
    if n == 1:
        return "Hay una persona registrada."
    if n > 1:
        return "Hay %d personas registradas." % n
    return "Todavía no hay ninguna persona registrada."


def destinos(enviados: int, total: int) -> str:
    """A cuántas personas salió el aviso: el antiguo «(n de m)».

    `total` son las copias que ya salieron más las que siguen en la cola (una por
    persona registrada al llegar la alerta; al atenderla, las que no habían salido
    se cancelan y dejan de contar). `enviados` son las que ya salieron. Vacío si
    no hay copias.
    """
    if total <= 0:
        return ""
    if enviados >= total:
        return ("enviada a 1 persona" if total == 1
                else "enviada a las %d personas" % total)
    if enviados == 0:
        return ("todavía no se ha enviado" if total == 1
                else "todavía no se ha enviado a ninguna de las %d personas" % total)
    return "enviada a %d de %d personas" % (enviados, total)


def linea_reciente(creado_en: int, tipo: str, estado: str, enviados: int,
                   total: int) -> str:
    linea = "%s, %s: %s" % (
        time.strftime("%d/%m %H:%M", time.localtime(creado_en)),
        NOMBRE_TIPO.get(tipo, tipo), NOMBRE_ESTADO.get(estado, estado))
    detalle = destinos(enviados, total)
    if detalle:
        linea += " (%s)" % detalle
    return linea


# --- /panel -----------------------------------------------------------------

PANEL_NO_DISPONIBLE = (
    "El panel todavía no está disponible. Mientras tanto, puede consultar el "
    "estado con /alertas. Las alertas le seguirán llegando por aquí."
)

# El mensaje de /panel va con parse_mode=HTML (2026-10-19): el codigo se enseña en
# <code> para que Telegram lo pinte en monoespaciado y lo pueda copiar con un toque,
# y el teclado lleva ademas un boton «Abrir panel» con la URL. El codigo NO va
# dentro de la URL: teclearlo es el camino, y un enlace con el codigo a la vista
# en un chat seria una puerta abierta a quien vea la pantalla.
#
# Regla al tocar esta plantilla: los unicos caracteres con significado en HTML son
# los del codigo (<code>), que es texto fijo; el codigo y la URL se escapan con
# html.escape() en bot.cmd_panel antes de llamar a .format(), y el codigo solo puede
# traer letras y numeros del alfabeto de panel_acceso, o sea nunca < ni &.
PANEL_CODIGO = (
    "Su código para entrar al panel es:\n"
    "<code>{codigo}</code>\n\n"
    "{url}\n\n"
    "Escriba el código en la pantalla de acceso. Es personal: vale por diez minutos "
    "y solo se puede usar una vez. Si lo comparte, solo funcionará para quien lo use "
    "primero."
)

# Los dos botones que acompanan al codigo. El primero copia el codigo con un toque
# (copy_text, que Telegram no ofrece en web: ahi hace falta selecionarlo a mano); el
# segundo abre el panel. Van separados en dos filas para que ninguno de los dos
# quede sin tocar por apretar demasiado cerca.
PANEL_BOTON_COPIAR = "Copiar código"
PANEL_BOTON_ABRIR = "Abrir panel"

# --- Botón «Ya lo atendí» y errores ------------------------------------------

BOTON_SIN_ALERTA = "Este botón ya no corresponde a ninguna alerta."
SOLO_CHAT_PRIVADO = "Este bot solo funciona en un chat privado."
SOLO_REGISTRADOS = "Solo los familiares registrados pueden usar este botón."
ANOTADO_SIN_EDITAR = "Anotado. No se pudo actualizar el mensaje anterior."
YA_ATENDIDA = "Ya está atendida."
YA_ESTABA_ATENDIDA = "Esta alerta ya estaba atendida."

# --- Chat de dos vias ------------------------------------------------------
CHAT_CONFIRMACION_ENVIADO = "Se envió a la sala: «{texto}»\nEstado: Enviado."
CHAT_CONFIRMACION_SE_DJO = "Se envió a la sala: «{texto}»\nEstado: Se dijo en la sala."
CHAT_CONFIRMACION_NO_SE_PUDO = "Se envió a la sala: «{texto}»\nEstado: No se pudo decir (satélite desconectado)."
CHAT_DE_OTRO = "{nombre} a la sala: {texto}"
CHAT_DESDE_PANEL = "{nombre} a la sala (desde el panel): {texto}"
CHAT_RESPUESTA_SALA = "La sala respondió: «{texto}»"
CHAT_DEMASIADO_RAPIDO = "Solo puede enviar un mensaje cada 5 segundos."
CHAT_DEMASIADO_LARGO = "El mensaje no puede pasar de 200 caracteres."

ERROR_BOTON = "Ocurrió un error. Por favor, inténtelo de nuevo."
ERROR_COMANDO = "Ocurrió un error al procesar el comando. Por favor, inténtelo de nuevo."

