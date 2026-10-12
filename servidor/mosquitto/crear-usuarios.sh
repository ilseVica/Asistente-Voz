#!/usr/bin/env bash
# ============================================================================
# crear-usuarios.sh - crea o rota las contrasenas del broker Mosquitto
#
# Que hace:
#   1. Crea el directorio de secretos con permisos 750.
#   2. Genera el archivo passwd con los hashes PBKDF2 de los siete usuarios.
#   3. Lo deja en 640, propiedad del usuario que ejecuta el script.
#
# Por que el passwd NO se escribe a mano: son hashes con sal. Y por que las
# contrasenas NO van en la linea de comandos: el script las pide por
# teclado y las pipea por la entrada estandar de mosquitto_passwd, de modo
# que no aparecen en "ps", ni en el historial de bash, ni en el historial del
# agente.
#
# Uso:
#   crear-usuarios.sh              -> los siete usuarios (crea passwd)
#   crear-usuarios.sh nodo         -> solo ese usuario (crea o rota)
#
# Despues hay que copiar cada contrasena a servidor/.env, que queda en 600.
# ============================================================================

set -euo pipefail

IMAGEN="eclipse-mosquitto:2.0.22"
USUARIOS_POR_DEFECTO="nodo puente orquestador satelite voz salud panel"

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$RAIZ/servidor/.env"
PASSWD_RELATIVO="passwd"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- 1. Parametros ---------------------------------------------------------
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi
if [ "$#" -gt 0 ]; then
    USUARIOS="$*"
else
    USUARIOS="$USUARIOS_POR_DEFECTO"
fi

# --- 2. Directorio de secretos --------------------------------------------
# El .env tiene la ruta; si no esta, se puede pasar por entorno.
SECRETS_DIR=""
if [ -n "${MOSQUITTO_SECRETS_DIR:-}" ]; then
    SECRETS_DIR="$MOSQUITTO_SECRETS_DIR"
elif [ -f "$ENV_FILE" ]; then
    SECRETS_DIR="$(grep -E '^MOSQUITTO_SECRETS_DIR=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"
fi
[ -n "$SECRETS_DIR" ] || die "no encuentro MOSQUITTO_SECRETS_DIR. Crea servidor/.env a partir de .env.example o exporta la variable."
[ -d "$SECRETS_DIR" ] || mkdir -p "$SECRETS_DIR"
chmod 750 "$SECRETS_DIR"
PASSWD="$SECRETS_DIR/$PASSWD_RELATIVO"

case "$SECRETS_DIR" in
    "$RAIZ"/*)
        die "el directorio de secretos esta dentro del repositorio ($RAIZ). Asi un 'git add .' lo capturaria."
        ;;
esac

# --- 3. Imagen -------------------------------------------------------------
if ! docker image inspect "$IMAGEN" >/dev/null 2>&1; then
    printf 'La imagen %s no esta; descargando...\n' "$IMAGEN"
    docker pull "$IMAGEN"
fi

# --- 4. Generacion ---------------------------------------------------------
# El primer usuario crea el archivo con -c. Los siguientes lo agregan.
# -c reescribe el archivo desde cero: nunca se usa cuando ya existe.
primero=1
[ -f "$PASSWD" ] && primero=0

printf '\nDireccion de secretos: %s\n' "$SECRETS_DIR"
if [ "$primero" -eq 0 ]; then
    printf 'El archivo ya existe: se actualizan o agregan usuarios, no se borra nada.\n'
fi
printf 'Con contrasena vacia no se hace nada. Ctrl-C para salir.\n\n'

for usuario in $USUARIOS; do
    printf -- '--- %s ---\n' "$usuario"
    printf 'Contrasena para %s: ' "$usuario"
    IFS= read -r -s pw
    printf '\nRepetir: '
    IFS= read -r -s pw2
    printf '\n'

    if [ -z "$pw" ]; then
        printf 'Vacia. Se omite %s.\n\n' "$usuario"
        continue
    fi
    if [ "$pw" != "$pw2" ]; then
        die "las contrasenas no coinciden. No se escribio nada."
    fi
    case "$pw" in
        *:*) printf 'Aviso: esa contrasena contiene ":". Funciona, pero es mas simple evitarla.\n' ;;
    esac

    flag=""
    [ "$primero" -eq 1 ] && flag="-c"
    primero=0

    printf '%s\n%s\n' "$pw" "$pw2" | docker run --rm -i \
        --user "$(id -u):$(id -g)" \
        -v "$SECRETS_DIR:/mosquitto/secrets" \
        "$IMAGEN" mosquitto_passwd $flag "/mosquitto/secrets/$PASSWD_RELATIVO" "$usuario" \
        || die "fallo mosquitto_passwd para $usuario"

    unset pw pw2
    printf 'Listo: %s\n\n' "$usuario"
done

# --- 5. Permisos finales ---------------------------------------------------
[ -f "$PASSWD" ] || die "no se genero $PASSWD"

# mosquitto_passwd deja una copia del passwd anterior en passwd.backup.XXXXXX
# cada vez que actualiza un usuario. Son copias del mismo secreto, asi que se
# borran.
rm -f "$SECRETS_DIR"/"$PASSWD_RELATIVO".backup.* 2>/dev/null || true

# 640: el broker corre como uid 1883 / gid 1883 y no es el dueno del archivo,
# asi que lo lee por el grupo. El grupo es ilse (gid 1000) y el compose
# declara group_add 1000 para el contenedor.
chmod 640 "$PASSWD"

# 750 y no 700: el directorio tambien tiene que ser recorrible por el grupo,
# no solo legible. Con 700 el broker no podria ni entrar al directorio.
chmod 750 "$SECRETS_DIR"

# --- 6. Comprobacion -------------------------------------------------------
printf 'Contenido del archivo (solo nombres, sin contrasenas):\n'
cut -d: -f1 "$PASSWD" | sed 's/^/  /'
printf '\nPermisos: %s\n' "$(stat -c '%A %U:%G' "$PASSWD")"
printf 'Directorio: %s\n' "$(stat -c '%A %U:%G' "$SECRETS_DIR")"

printf '\nSIGUIENTE PASO\n'
printf '  1. Copia cada contrasena a servidor/.env (chmod 600).\n'
printf '  2. docker compose up -d\n'
printf '  3. Lee la seccion 6 del README para las pruebas.\n'
