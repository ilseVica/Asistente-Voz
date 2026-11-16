#!/usr/bin/env bash
# Funciones comunes para leer variables de servidor/.env sin hacer `source`
# (el .env no es un script de bash válido).

# Uso: cargar_var_env <archivo> <NOMBRE_VARIABLE>
# Imprime el valor (sin comillas envolventes) por stdout. Devuelve 1 si no está.
cargar_var_env() {
    local archivo="$1" nombre="$2"
    local linea valor
    linea="$(grep -E "^${nombre}=" "$archivo" 2>/dev/null | tail -n 1)" || true
    if [[ -z "$linea" ]]; then
        return 1
    fi
    valor="$(printf '%s' "$linea" | cut -d= -f2-)"
    valor="${valor%\"}"; valor="${valor#\"}"
    valor="${valor%\'}"; valor="${valor#\'}"
    printf '%s' "$valor"
}
