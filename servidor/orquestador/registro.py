"""El registro de la familia.

quién recibe las alertas, y desde cuándo se sabe.

Es un archivo aparte porque es lo único de este servicio que cambia de estado
durante la vida del proceso, y porque tiene que quedar escrito en disco: si el
servicio se reinicia, el chat registrado tiene que seguir siendo el mismo. Si
estuviera solo en memoria, cada reinicio dejaría las alertas sin destino hasta
que alguien escribiera `/registro` otra vez, y en un reinicio no hay nadie
escribiendo.

Un solo archivo, porque un solo archivo es lo que hay: una persona mayor, sus
familiares. No hay un sistema de usuarios ni de permisos, y esta parte del plan
tampoco lo pide. Si algún día lo hubiera, esta clase es el sitio donde empieza.

`codigo` es un código de un solo uso, de 10 minutos, para confirmar el número.
Está en el contenedor (`python -m registro codigo`) y luego en Telegram, no al revés:
al revés, el bot de Telegram queda verificable por cualquiera que le escriba,
porque no hay nada que comprobar.

Los códigos viven en un archivo aparte, `codigos.json`, y no en SQLite. Son de un
solo uso y de 10 minutos, así que un archivo es suficiente y se puede leer y borrar
a mano. Vive en el mismo directorio que `familia.json` porque los dos tienen que
sobrevivir a un reinicio del servicio.

**El archivo manda, la memoria no.** El comando que genera un código
(`docker compose exec orquestador python -m registro codigo`) corre en un proceso
distinto del servicio, y el bot de Telegram vive en el proceso del servicio. Si el
servicio cargara los códigos solo al arrancar, un código generado después sería
invisible para el bot hasta el siguiente reinicio, y `/registro` respondería «ese
codigo no existe» con un código perfectamente válido. Por eso `comprobar_codigo` y
`marcar_codigo_usado` releen el archivo si ha cambiado (mtime y tamaño) y escriben
dentro los cambios: el intento que consume un código y el uso que lo invalida tienen
que sobrevivir no solo a un reinicio del servicio, sino a estos dos procesos.

Y hay una diferencia entre lo que este archivo hace y lo que dice el plan. El plan
pide «código de un solo uso, válido 10 minutos, con límite de intentos», y el
límite de intentos se cuenta por código. Un código que se adivina es un código que
se genera otro, así que el límite no protege de un atacante de verdad: protege de
que alguien pruebe cinco veces el código que le sobra de un mensaje de Telegram.
"""

import json
import logging
import os
import secrets
import sys
import threading
import time

registro = logging.getLogger("registro")

MINUTOS_CODIGO = 10
SEGUNDOS_CODIGO = MINUTOS_CODIGO * 60
MAX_INTENTOS_CODIGO = 5

# Sin 0/O ni 1/l/I. Un código de 16 caracteres se dicta por teléfono, y un
# "O" identificado como "cero" por el que lo lee es un código que se teclea mal y
# se caduca sin que nadie entienda por qué.
ALFABETO = "abcdefghjkmnpqrstuvwxyz23456789"
LONGITUD_CODIGO = 16

# La tabla de códigos en memoria, y el archivo en disco. El candado serializa las
# lecturas y las escrituras: el bot atiende en un hilo y el comando corre en otro,
# y sin candado dos personas que escribieran `/registro` a la vez con el mismo
# código podrían pasar las dos el límite de intentos.
_candado = threading.Lock()
CODIGOS = {}

# Dónde está el archivo, y con qué firma se leyó por última vez. Lo rellena
# `cargar_codigos`; si está vacío, `comprobar_codigo` deduce la ruta de `config`.
RUTA_CODIGOS = None
_firma_archivo = None


def _ruta_por_defecto() -> str:
    import config
    return os.path.join(os.path.dirname(config.RUTA_DB), "codigos.json")


def _firma(ruta: str):
    """La «firma» del archivo: (mtime en nanosegundos, tamaño).

    Es lo que permite saber si hay que releerlo sin coste. Comparar el contenido
    entero en cada comando sería tirar la mitad del trabajo de este archivo por
    algo que casi nunca cambia; y sin comparar nada, el proceso del servicio nunca
    vería el código que acaba de generar el otro proceso.
    """
    try:
        info = os.stat(ruta)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def sincronizar() -> None:
    """Relee el archivo si cambió desde la última lectura.

    Es la pieza que hace que el bot vea los códigos que genera el comando del
    contenedor. Sin ella, el flujo de registro solo funcionaría si el servicio se
    reiniciara después de generar el código, que es justo lo que no se puede
    pedirle a quien está instalando el sistema.
    """
    global _firma_archivo
    with _candado:
        ruta = RUTA_CODIGOS or _ruta_por_defecto()
        firma = _firma(ruta)
        if firma == _firma_archivo:
            return
        _leer_archivo(ruta)
        _firma_archivo = firma


def _leer_archivo(ruta: str) -> None:
    global CODIGOS
    CODIGOS = {}
    try:
        with open(ruta, encoding="utf-8") as archivo:
            contenido = json.load(archivo)
    except (FileNotFoundError, ValueError, OSError):
        return
    if not isinstance(contenido, dict):
        return
    for codigo, entrada in (contenido.get("codigos") or {}).items():
        try:
            CODIGOS[codigo] = {
                "creado_en": int(entrada.get("creado_en", 0)),
                "expira_en": int(entrada.get("expira_en", 0)),
                "intentos": int(entrada.get("intentos", 0)),
                "usado_en": entrada.get("usado_en"),
            }
        except (AttributeError, TypeError, ValueError):
            # Una entrada con la forma equivocada no se cuela en la tabla: un
            # código que no se puede leer no puede validar nada.
            continue


def comprobar_codigo(codigo: str) -> str:
    """Devuelve `None` si el código sirve, o el motivo por el que no.

    El orden de las comprobaciones es el del plan: que exista, que no se haya
    usado, que no haya caducado y que no se hayan pasado los intentos.

    Cada intento fallido suma uno, y al quinto el código queda invalidado para
    siempre. Sin ese límite, un código de 16 caracteres se podría probar cinco
    veces con cinco intentos y volver a genera otro, indefinidamente.

    Devolver el motivo y no un `False` es lo que permite que el bot le diga a la
    persona qué pasó, sin confirmar que el bot existe: el mensaje es el mismo para
    un código que no existe y para uno que caducó.

    El intento que se consume aquí se ESCRIBE en el archivo, no solo en la memoria
    del proceso. Antes solo se guardaba en memoria, así que reiniciar el servicio
    devolvía los cinco intentos de un código, y el límite no limitaba nada.
    """
    sincronizar()
    ahora = int(time.time())

    with _candado:
        entrada = CODIGOS.get(codigo)

        if entrada is None:
            return "ese codigo no existe"
        if entrada["usado_en"] is not None:
            return "ese codigo ya se uso"
        if entrada["intentos"] >= MAX_INTENTOS_CODIGO:
            return "ese codigo se invalido tras %d intentos" % MAX_INTENTOS_CODIGO
        if ahora > entrada["expira_en"]:
            return "ese codigo ya caduco (valia %d minutos)" % MINUTOS_CODIGO

        entrada["intentos"] += 1
        _escribir_archivo()

    return None


def marcar_codigo_usado(codigo: str) -> bool:
    """Invalida el código para siempre. Devuelve si estaba en la tabla.

    Un código usado deja de funcionar para siempre, que es lo que pide la P20: si
    volviera a servir, alguien que lo hubiera visto en el historial de un celular
    podría quitar a la familia de la lista de destinatarios.

    Escribe en el archivo por lo mismo que `comprobar_codigo`: si el uso solo se
    quedara en la memoria de este proceso, un reinicio del servicio devolvería el
    código a la lista de los que sirven, y la P20 («el código ya usado deja de
    funcionar») fallaría justo en la prueba que la mide.
    """
    sincronizar()
    with _candado:
        entrada = CODIGOS.get(codigo)
        if entrada is None or entrada["usado_en"] is not None:
            return False
        entrada["usado_en"] = int(time.time())
        _escribir_archivo()
        return True


def cargar_codigos(ruta: str) -> dict:
    """Carga la tabla desde el archivo. La usa el servicio al arrancar."""
    global RUTA_CODIGOS, _firma_archivo
    with _candado:
        RUTA_CODIGOS = ruta
        _leer_archivo(ruta)
        _firma_archivo = _firma(ruta)
        return CODIGOS


def guardar_codigos(ruta: str) -> None:
    global RUTA_CODIGOS
    RUTA_CODIGOS = ruta
    with _candado:
        _escribir_archivo()


def _escribir_archivo() -> None:
    """Escribe la tabla en disco. Llama con el candado tomado.

    Se escribe a un temporal y se renombra: si el proceso se muere a mitad, el
    archivo bueno sigue intacto. Un JSON cortado por la mitad no se lee, y el
    código que se pierde es el que la familia iba a usar.
    """
    global _firma_archivo
    ruta = RUTA_CODIGOS or _ruta_por_defecto()
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    temporal = ruta + ".tmp"
    try:
        with open(temporal, "w", encoding="utf-8") as archivo:
            json.dump({"codigos": CODIGOS}, archivo, indent=2)
            archivo.write("\n")
        os.replace(temporal, ruta)
    except OSError as error:
        registro.error("no se pudieron guardar los codigos: %s", error)
        return
    _firma_archivo = _firma(ruta)


def generar_codigo(ruta: str) -> str:
    """Crea un código de un solo uso y lo guarda con su caducidad.

    No escribe el código en el log. Sale por la salida estándar del comando, que
    es la consola del operador, y no por `logging`, que va al `docker logs` y se
    lee con cualquiera dentro del servidor.

    Los códigos caducados o usados se borran al generar uno nuevo, y no con un
    temporizador: son pocos, un operador genera uno cada pocos minutos, y un
    proceso que genera un código cada hora tardaría un día entero en tener diez.
    """
    ahora = int(time.time())
    with _candado:
        _leer_archivo(ruta)
        for codigo, entrada in list(CODIGOS.items()):
            caducado_o_usado = (entrada["usado_en"] is not None
                                or ahora > entrada["expira_en"] + 3600)
            if caducado_o_usado:
                del CODIGOS[codigo]

        codigo = "".join(secrets.choice(ALFABETO) for _ in range(LONGITUD_CODIGO))
        CODIGOS[codigo] = {
            "creado_en": ahora,
            "expira_en": ahora + SEGUNDOS_CODIGO,
            "intentos": 0,
            "usado_en": None,
        }
        _escribir_archivo()
    return codigo


class RegistroFamilia:
    def __init__(self, ruta: str):
        self.ruta = ruta
        self.datos = {"chat_id": "", "nombre": "", "registrado_en": None}
        self._cargar()

    def _cargar(self) -> None:
        try:
            with open(self.ruta, encoding="utf-8") as archivo:
                self.datos = json.load(archivo)
        except FileNotFoundError:
            registro.info("todavia no hay nadie registrado (%s no existe)",
                          self.ruta)
        except (OSError, ValueError) as error:
            # Un archivo corrupto no puede tumbar el servicio: se avisa y se
            # empieza de cero. Perder el registro es malo; no arrancar es peor.
            registro.error("no se pudo leer %s (%s); se empieza vacio",
                           self.ruta, error)

    def _guardar(self) -> None:
        carpeta = os.path.dirname(self.ruta)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        temporal = self.ruta + ".tmp"
        # Se escribe a un temporal y se renombra. Si el proceso se muere a mitad,
        # el archivo bueno sigue intacto: un `write` directo puede dejar un JSON
        # cortado por la mitad, y un JSON cortado no se lee.
        with open(temporal, "w", encoding="utf-8") as archivo:
            json.dump(self.datos, archivo, indent=2, ensure_ascii=False)
            archivo.write("\n")
        os.replace(temporal, self.ruta)

    def esta_registrado(self) -> bool:
        return bool(self.datos.get("chat_id"))

    def registrar(self, chat_id: str, nombre: str = "") -> None:
        self.datos = {
            "chat_id": str(chat_id),
            "nombre": nombre,
            "registrado_en": int(time.time()),
        }
        self._guardar()
        registro.warning("familia registrada")


# --- Comando del contenedor ------------------------------------------------
#
#   docker compose exec orquestador python -m registro codigo
#
# Imprime el código en la consola del operador y no lo escribe en ningún log.
# Está en el contenedor y no en el panel porque el panel todavía no existe, y
# porque quien genera el código es el que instala el sistema, que ya está en el
# servidor.


def main(argumentos) -> int:
    import config

    if not argumentos or argumentos[0] != "codigo":
        print("Uso: python -m registro codigo", file=sys.stderr)
        print("  Imprime un codigo de un solo uso, valido %d minutos." % MINUTOS_CODIGO,
              file=sys.stderr)
        return 2

    codigo = generar_codigo(_ruta_por_defecto())
    # A la consola, no al log: el `docker logs` lo lee cualquiera que pueda ver el
    # contenedor, y un código de registro en un log es un código de registro
    # filtrado.
    print("Codigo de registro para Telegram: %s" % codigo)
    print("Valido %d minutos, de un solo uso." % MINUTOS_CODIGO)
    print("Enviarlo al bot con:  /registro %s" % codigo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
