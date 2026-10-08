"""Quita secretos de un texto antes de escribirlo en el log.

Esta función vive sola, y no en `telegram_cliente.py`, por un motivo concreto: el
servicio del panel necesita poder registrar un error sin arrastrar `python-telegram-bot`
detras. Importar `telegram_cliente` desde `alertas.py` obligaria a que la imagen del
panel tuviera instalado el bot entero, que son varios megas y una dependencia que el
panel no usa, y si faltara, el fallo saldria DENTRO de un `except`: justo cuando ya
se esta informando de un problema, se cae otra vez y encima tapa el primero.

Aqui no hay nada de Telegram: solo una sustitución de texto y `config`.
"""

import logging

import config

# Estado de la fabrica de registros. Ver `instalar_filtro_secretos`.
_FABRICA_ANTERIOR = None
_FABRICA_PUESTA = False


def _limpiar_registro(registro):
    """Quita los secretos de un registro, sin romper su formato.

    `record.msg` y `record.args` se limpian los dos porque no es lo mismo: un
    `logging.info("peticion a %s", url)` tiene el token en `args`, y limpiar solo
    `msg` no serviria de nada.

    Los numeros se respetan porque `httpx` registra
    `"HTTP Request: %s %s \"%s %d %s\""` con el codigo HTTP entero: si ese `200`
    llega como `'200'`, el `%d` revienta con `TypeError: %d format: a real number is
    required, not str`, y el manejador escupe un «Logging error» con el contenido
    entero del registro dentro. O sea, el token entero y ademas se pierde el log.

    Lo demas SI se convierte, y no solo los `str`, porque la URL que filtra el
    token no llega como texto: `httpx` pasa un objeto `httpx.URL`, que al
    formatearlo con `%s` sale entero y con el token dentro. Comprobar
    `isinstance(valor, str)` deja pasar justo lo que hay que cazar.
    """
    if isinstance(registro.msg, str):
        registro.msg = sanear(registro.msg)
    if registro.args:
        if isinstance(registro.args, dict):
            registro.args = {k: _si_texto(v) for k, v in registro.args.items()}
        else:
            registro.args = tuple(_si_texto(a) for a in registro.args)
    return registro


class FiltroSecretos(logging.Filter):
    """Lo mismo que `_limpiar_registro`, pero como filtro de manejador.

    El metodo se llama `filter` porque es lo que pide la clase de la que hereda, y
    devuelve `True` siempre, que es lo que significa «este registro pasa».
    """

    def filter(self, registro):
        _limpiar_registro(registro)
        return True


def _si_texto(valor):
    """Limpia un argumento de log sin romper el formato.

    Numeros, booleanos y `None` se dejan como estan; el resto pasa por `sanear()`.
    El detalle de por que esta regla y no la de «solo si es `str`» esta en
    `_limpiar_registro`.
    """
    if valor is None or isinstance(valor, (int, float, bool)):
        return valor
    if isinstance(valor, str):
        return sanear(valor)
    if isinstance(valor, dict):
        return {k: _si_texto(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_si_texto(v) for v in valor]
    if isinstance(valor, tuple):
        return tuple(_si_texto(v) for v in valor)
    return sanear(valor)


def instalar_filtro_secretos() -> None:
    """Pone la limpieza de secretos en TODOS los registros. Idempotente.

    Se hace con una **fabrica de registros** (`logging.setLogRecordFactory`), no
    solo con filtros en los manejadores, y el motivo no es el que parece:
    un filtro va en un manejador, y hay manejadores que todavia no existen cuando
    se instala. El de esta prueba, el de uvicorn, el que pone el script de al lado
    que se importa sin avisar. Cada uno de esos se cuela por debajo de un filtro
    puesto antes de tiempo, y con la URL del bot en el registro.

    La fabrica se ejecuta al CREAR cada registro, antes de que pase por ningun
    logger ni por ningun manejador. Por eso da igual cuando se llame y por quien
    llame: no hay hueco por el que entrar.

    Si ya habia una fabrica puesta, no se pisa: se encadena. Si la hubiera, alguien
    estaba haciendo algo a proposito con los registros y romperlo seria peor que no
    limpiar el token. En este proyecto no hay ninguna, pero el codigo no lo da por
    hecho porque este fichero se puede importar desde cualquiera.
    """
    global _FABRICA_ANTERIOR, _FABRICA_PUESTA

    if not _FABRICA_PUESTA:
        _FABRICA_ANTERIOR = logging.getLogRecordFactory()
        _FABRICA_PUESTA = True

    anterior = _FABRICA_ANTERIOR

    def fabrica(*args, **kwargs):
        registro = anterior(*args, **kwargs)
        return _limpiar_registro(registro)

    logging.setLogRecordFactory(fabrica)

    # Y ademas un filtro en los manejadores que ya haya. La fabrica es la garantia
    # de verdad; esto es la red de mas, para el manejador que se haya puesto antes
    # de que nadie pensara en esto.
    for manejador in logging.getLogger().handlers:
        if not any(isinstance(f, FiltroSecretos) for f in manejador.filters):
            manejador.addFilter(FiltroSecretos())



def sanear(texto) -> str:
    """Sustituye TODOS los secretos del entorno por `***`.

    Los secretos no están escritos aquí: los da `config.secretos()`, que es el
    único sitio donde se declaran. La lista de nombres vive en `config.py` y esta
    función se limita a aplicarla.

    Por qué no una lista escrita aquí: la versión anterior conocía dos valores
    (el token de Telegram y la contraseña del panel) y los tenía escritos en el
    filtro. Eso es una lista que hay que mantener en dos sitios —el `.env` y el
    código— y el sitio que se olvida es el segundo, sin que nada avise. Con una
    contraseña de por medio en la lista corta, el que se queda fuera es el
    orquestador o el panel, y lo que sale es una contraseña en `docker logs`.

    Sustitución literal y no expresión regular: cada secreto es una cadena fija y
    conocida, así que buscar el texto exacto no puede fallar ni dar falsos
    positivos con algo que se le parezca.

    Los valores vacíos no se tapan, y por eso una prueba puede usar esta función
    sin inventar un token falso.
    """
    texto = str(texto)
    for secreto in config.secretos():
        if secreto:
            texto = texto.replace(secreto, "***")
    return texto