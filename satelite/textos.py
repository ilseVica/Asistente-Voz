"""Los textos que el satelite dice en voz alta.

Son frases para OIR, no codigos de error: las lee Piper y las pronuncia la
bocina de la casa. Viven aqui, y no sueltas en `satelite.py`, para que un cambio
de redaccion se haga en un solo sitio y para poder probarlas sin red.

El tono es de USTED, porque quien las oye es el adulto mayor de la casa.
"""

# Cuando el WebSocket falla o el audio no se oyo bien.
MENSAJE_REPETIR = "Disculpe, no le escuché bien. ¿Me lo puede repetir?"

# Cuando el satelite no tiene conexion con el servidor (WebSocket caido).
MENSAJE_SIN_CONEXION = (
    "Disculpe, en este momento no tengo conexión. "
    "Por favor, inténtelo de nuevo en unos minutos."
)
