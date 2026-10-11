"""El registro de la familia.

Quién recibe las alertas, y desde cuándo se sabe.

La lista de familiares vive en SQLite, en la tabla `familiares`, y no en este
archivo. La razón es que son varios: el plan-approved es que cada chat que se
registra con un código válido se suma a la lista, sin quitar a los anteriores. Un
archivo con un único `chat_id` era el diseño de "un familiar", y en cuanto hay dos
hay que migrarlo; la tabla ya es el sitio donde vivir, porque es donde vive el
historial y porque `alertas.py` necesita los familiares en el mismo `INSERT` que
encola un aviso.

Lo que sí sigue en este archivo son los **códigos** de registro, en
`codigos.json`: son de un solo uso y de 10 minutos, así que un archivo es
suficiente y se puede leer y borrar a mano. Vive en el mismo directorio que la base
porque los dos tienen que sobrevivir a un reinicio del servicio.

**El archivo manda, la memoria no.** El comando que genera un código
(`docker compose exec orquestador python -m registro código`) corre en un proceso
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

## Los dos comandos que no son `código`

    docker compose exec orquestador python -m registro familiares
    docker compose exec orquestador python -m registro baja CHAT_ID

`familiares` lista los chats registrados, con su nombre y su fecha. `baja` quita
uno y cancela lo que le quedaba por recibir.

Los dos escriben a la consola del operador y NO al log, por la misma razón que el
código: un `chat_id` de Telegram es el número de una persona, y `docker logs` se lee
con cualquiera que tenga acceso al servidor. El `docker compose exec` lo ve quien
está delante del servidor, que es quien tiene el shell del servidor.
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


def leer_familia_legacy(carpeta: str = None) -> str:
    """El `chat_id` del `familia.json` viejo, o `None`.

    Solo lo usa la migración de `db.py`. `familia.json` era el diseño de un solo
    familiar: un archivo con un `chat_id`. Con la lista de familiares en SQLite, ese
    archivo ya no manda sobre nada, pero su contenido es el único registro del
    familiar que ya estaba dado de alta, y sin copiarlo sus alertas pasan a estar
    pendientes sin nadie a quien mandárselas.

    `carpeta` es dónde está ese `familia.json`, y por qué se pasa en vez de sacarlo
    de la configuración: quien pregunta es la migración del esquema, que puede estar
    migrando la base que se le ha pasado sin que sea `config.RUTA_DB` (una copia de
    una prueba, una base de un servicio en otro directorio, la de otro puerto). Si
    la ruta se sacara de `config`, la migración miraría el `familia.json` de la
    instalación de al lado y no encontraría el suyo. En el servidor, donde las dos
    rutas coinciden, funciona igual; es en cuanto se migra una base que no es la de
    `config` cuando se nota, y entonces el familiar de verdad no se copia y sus
    alertas quedan huerfanas. Por defecto se usa `config.RUTA_DB`, que es lo que
    necesita `migrar_familia_legacy`.

    Se lee sin comprobaciones extra: si el archivo no existe, no se pudo leer, o no
    trae un `chat_id`, se devuelve `None` y la migración sigue. La migración no
    puede fallar por un archivo huerfano: un `familia.json` corrupto no puede impedir
    que el servicio arranque y reciba alertas.

    El archivo NO se borra aquí. Lo renombra `migrar_familia_legacy`, despues de
    copiar la fila, para que el archivo bueno siga en el disco hasta que la copia
    este confirmada, y para que la lectura de este archivo no se repita en cada
    arranque.
    """
    if carpeta is None:
        import config

        carpeta = os.path.dirname(config.RUTA_DB)
    ruta = os.path.join(carpeta, "familia.json")
    try:
        with open(ruta, encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(datos, dict):
        return None
    chat_id = datos.get("chat_id")
    if chat_id in (None, ""):
        return None
    return str(chat_id)


def carpeta_legacy(base) -> str:
    """La carpeta donde vive el `familia.json` de ESTA base.

    Se saca de `base.ruta`, la base que se ha abierto, y no de `config.RUTA_DB`. En
    el servidor son la misma carpeta, asi que la distinción no se ve; en cuanto se
    abre una base que no es la de `config` (la copia de una prueba, la de otro
    servicio) leer y renombrar el `familia.json` de `config` sería buscar el
    registro de otra gente: o no aparece y el familiar de verdad no se copia, o
    aparece y se renombra el archivo ajeno.

    Se devuelve vacía si la ruta de la base no tiene carpeta (una base en el
    directorio actual), que es lo mismo que decir «el archivo esta aqui».
    """
    return os.path.dirname(getattr(base, "ruta", "") or "")


def migrar_familia_legacy(base) -> None:
    """Deja el familiar del `familia.json` viejo en la tabla y aparta el archivo.

    Se llama una vez al arrancar el servicio. Son dos pasos y conviene separarlos
    porque los hace quien puede:

    **La copia la hace la migración del esquema**, dentro de la misma transacción
    que reparte los envíos de la cola vieja, y para eso necesita saber quién era el
    familiar de entonces: eso es lo que lee `familia.json`. Al terminar, la tabla ya
    tiene al familiar.

    **El renombrado lo hace esta función**, y por eso no puede depender de que la
    tabla esté vacía. Esa es la trampa en la que se cae fácil: si `migrar_familia_legacy`
    empieza con «si ya hay familiares, no hay nada que migrar», en una instalación
    que viene del diseño de un solo destino la tabla *nunca* esta vacía (la acaba de
    rellenar la migración) y el `return` se come también el renombrado. Resultado:
    la lista está en SQLite, que es lo importante, pero `familia.json` se queda ahí
    para siempre, con un `chat_id` que ya nadie lee y que parece la fuente de verdad.

    Así que la pregunta que hace esta función no es «¿hay algo que migrar?» sino
    «¿queda el archivo viejo?». Si queda, se assure de que su familiar esté en la
    tabla (el `INSERT OR IGNORE` no duplica nada) y luego aparta el archivo.

    Renombrar a `familia.json.migrado`, en vez de borrar, es a propósito. Si el
    servicio se muere entre la copia y el renombrado, el archivo sigue ahí para el
    siguiente arranque, y como el alta es idempotente no pasa nada. Si se borrara y
    la copia no llegara a confirmar, el familiar registrado se perdía sin ninguna
    segunda copia.
    """
    chat_id = leer_familia_legacy(carpeta_legacy(base))
    if chat_id is None:
        return

    ruta = os.path.join(carpeta_legacy(base), "familia.json")
    destino = ruta + ".migrado"

    if not base.familiar_registrado(chat_id):
        try:
            base.registrar_familiar(chat_id, nombre="")
        except Exception as error:  # noqa: BLE001
            registro.error("no se pudo migrar el familiar de familia.json: %s", error)
            return

    try:
        os.replace(ruta, destino)
    except OSError as error:
        # El familiar ya esta en la tabla, que es lo importante. Si el archivo no se
        # puede renombrar se avisa y se deja: el próximo arranque lo intentara otra
        # vez, y mientras tanto el archivo no molesta porque la tabla manda.
        registro.warning("el familiar de familia.json ya esta en la tabla pero el "
                         "archivo no se pudo renombrar a %s: %s",
                         os.path.basename(destino), error)
    else:
        registro.info("el familiar de familia.json paso a la tabla de familiares; "
                      "el archivo quedo en %s", os.path.basename(destino))


# --- Comandos del contenedor ------------------------------------------------
#
#   docker compose exec orquestador python -m registro codigo
#   docker compose exec orquestador python -m registro familiares
#   docker compose exec orquestador python -m registro baja CHAT_ID
#
# Imprimen a la consola del operador y no escriben nada en el log. El código de
# registro es lo que hay que proteger (quien lo lea puede darse de alta como
# familiar), y un `chat_id` es el número de una persona. Los dos van a la consola,
# que es donde está quien ya tiene el shell del servidor.

USO = """Uso:
  python -m registro código           imprime un código de un solo uso
  python -m registro familiares       lista los familiares registrados
  python -m registro baja CHAT_ID     da de baja a un familiar
  python -m registro inactivar CHAT_ID  deja de avisarle SIN borrarlo
  python -m registro activar CHAT_ID    vuelve a avisarle
"""


def _abrir_base():
    """Abre la base. Solo la necesitan `familiares` y `baja`.

    `código` no la abre, y no por ahorrar tiempo: abrir la base crea la carpeta y
    ejecuta el esquema, y un comando que solo genera un código no necesita tocar la
    base. En un servidor recien instalado, generar el primer código no debería crear
    archivos.
    """
    import db

    return db.BaseDatos()


def _listar_familiares(base) -> int:
    familias = base.familiares_registrados()
    if not familias:
        print("No hay ningun familiar registrado.")
        print("Las alertas se encolan y no se mandan hasta que uno se registre.")
        return 0

    print("Familiares registrados: %d" % len(familias))
    print()
    for fila in familias:
        nombre = fila["nombre"] or "(sin nombre)"
        fecha = time.strftime("%Y-%m-%d %H:%M", time.localtime(fila["registrado_en"]))
        pendientes = base.envios_sin_mandar_de(fila["chat_id"])
        estado = "activo" if fila["activo"] else "INACTIVO (no recibe avisos)"
        print("  chat_id   %s" % fila["chat_id"])
        print("  nombre    %s" % nombre)
        print("  alta      %s" % fecha)
        print("  estado    %s" % estado)
        print("  sin mandar %d" % pendientes)
        print()
    print("Para dar de baja a uno:")
    print("  python -m registro baja CHAT_ID")
    print("Para desactivar SIN borrar (o reactivar):")
    print("  python -m registro inactivar CHAT_ID")
    print("  python -m registro activar CHAT_ID")
    return 0


def _dar_de_baja(base, chat_id: str) -> int:
    chat_id = chat_id.strip()
    if not chat_id:
        print("Falta el chat_id. Mira la lista con:", file=sys.stderr)
        print("  python -m registro familiares", file=sys.stderr)
        return 2

    if not base.familiar_registrado(chat_id):
        print("Ese chat_id no esta registrado. No hay nada que dar de baja.")
        return 1

    pendientes = base.envios_sin_mandar_de(chat_id)
    base.dar_de_baja(chat_id)

    # El log lleva el chat enmascarado y la consola el entero. La razon es la misma
    # que con el código: `docker logs` lo lee cualquiera con acceso al servidor, y un
    # chat_id es el número de una persona. La consola la ve quien ya tiene el shell.
    from db import enmascarar_chat

    registro.warning("familiar %s dado de baja desde la consola del operador; %d "
                     "aviso(s) sin mandar cancelados",
                     enmascarar_chat(chat_id), pendientes)
    print("Familiar %s dado de baja." % chat_id)
    if pendientes:
        print("Se cancelaron %d aviso(s) que no le habian llegado." % pendientes)
        print("Los avisos ya enviados a ese chat no se pueden deshacer: esa persona")
        print("ya los vio.")
    return 0


def _cambiar_activo(base, chat_id: str, activo: bool) -> int:
    """`inactivar`/`activar`: desactiva SIN borrar, o devuelve a un inactivo.

    Devuelven los codigos de salida de `_dar_de_baja`: 0 = se hizo, 1 = el chat no
    esta o ya estaba como se pide, 2 = falta el argumento. Los codigos importan
    porque los repiten los scripts y un shell distingue «cambio hecho» de «no
    habia nada que hacer».
    """
    chat_id = chat_id.strip()
    if not chat_id:
        print("Falta el chat_id. Mira la lista con:", file=sys.stderr)
        print("  python -m registro familiares", file=sys.stderr)
        return 2

    if not base.familiar_registrado(chat_id):
        print("Ese chat_id no esta registrado. No hay nada que %s."
              % ("inactivar" if not activo else "activar"))
        return 1

    if activo:
        cambio = base.marcar_familiar_activo(chat_id)
    else:
        cambio = base.marcar_familiar_inactivo(chat_id)
    if not cambio:
        print("Ese familiar ya estaba %s. Nada que hacer."
              % ("activo" if activo else "inactivo"))
        return 0

    # El log lleva el chat enmascarado y la consola el entero, igual que en la baja.
    from db import enmascarar_chat

    verbo = "reactivado" if activo else "inactivado"
    registro.warning("familiar %s %s desde la consola del operador",
                     enmascarar_chat(chat_id), verbo)
    print("Familiar %s %s." % (chat_id, verbo))
    if not activo:
        print("Ya no recibe avisos nuevos, pero su registro se queda.")
    return 0


def main(argumentos) -> int:
    if not argumentos:
        print(USO, file=sys.stderr)
        return 2

    orden = argumentos[0]

    if orden == "codigo":
        codigo = generar_codigo(_ruta_por_defecto())
        # A la consola, no al log: el `docker logs` lo lee cualquiera que pueda ver el
        # contenedor, y un código de registro en un log es un código de registro
        # filtrado.
        print("Codigo de registro para Telegram: %s" % codigo)
        print("Valido %d minutos, de un solo uso." % MINUTOS_CODIGO)
        print("Enviarlo al bot con:  /registro %s" % codigo)
        return 0

    if orden == "familiares":
        base = _abrir_base()
        try:
            return _listar_familiares(base)
        finally:
            base.cerrar()

    if orden == "baja":
        if len(argumentos) < 2:
            print("Falta el chat_id que se quiere dar de baja.", file=sys.stderr)
            print(USO, file=sys.stderr)
            return 2
        base = _abrir_base()
        try:
            return _dar_de_baja(base, argumentos[1])
        finally:
            base.cerrar()

    if orden in ("inactivar", "activar"):
        if len(argumentos) < 2:
            print("Falta el chat_id que se quiere %s."
                  % ("desactivar" if orden == "inactivar" else "reactivar"),
                  file=sys.stderr)
            print(USO, file=sys.stderr)
            return 2
        base = _abrir_base()
        try:
            return _cambiar_activo(base, argumentos[1], activo=orden == "activar")
        finally:
            base.cerrar()

    print("No se '%s'." % orden, file=sys.stderr)
    print(USO, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
