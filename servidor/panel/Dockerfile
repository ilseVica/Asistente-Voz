# Imagen del panel web.
#
# Sale de la misma imagen base que el orquestador, `python:3.12-slim`, y no de node ni
# de alpine: las otras dos estarían muy desactualizadas o no tendrían binarios para esta
# arquitectura. Se comparte la base a propósito, para que un `docker compose build` de
# los dos servicios no baje dos veces lo mismo.
FROM python:3.12-slim

# El servicio corre como un usuario sin privilegios, con el MISMO uid que el
# orquestador (1000) y por el motivo que se puso allí: si este proceso corre como
# root, un fallo aquí es un fallo del servidor entero.
#
# El mismo uid, además, es lo que permite que los dos contenedores monten el volumen de
# `/datos` con permisos de lectura y escritura. Con uids distintos habría que dejar el
# volumen en modo grupo y confiar en que los dos grupos coincidan, que es una forma de
# que un `docker compose up` falle según el orden.
RUN useradd --system --create-home --uid 1000 panel

WORKDIR /app

# Primero las dependencias y después el código, para que un cambio de código no
# reinstale las librerías.
COPY panel/requerimientos.txt ./
RUN pip install --no-cache-dir -r requerimientos.txt

# El código del panel y el del orquestador. Se copia el directorio entero porque el
# panel importa `config`, `db`, `alertas`, `panel_acceso` y `sanear` del orquestador
# (apartado 2.1 de la propuesta: son procesos distintos con una base compartida, no dos
# copias del mismo código). Copiar solo los que hacen falta sería más rápido de
# escribir, pero el día que uno más empiece a hacer falta, el fallo sería «ModuleNotFound
# Error» en un contenedor ya construido.
COPY --chown=panel:panel orquestador/ ./orquestador/
COPY --chown=panel:panel panel/ ./panel/

# `/datos` es el volumen compartido con el orquestador: ahí está la base. El panel lee
# de ella (las alertas y los familiares) y escribe en las dos tablas del panel, así que
# necesita leer Y escribir, y por eso no va en solo lectura.
RUN mkdir -p /datos && chown panel:panel /datos

USER panel

# El puerto NO se expone con `EXPOSE` a propósito: el panel va con `network_mode: host`
# y escucha en las direcciones de `PANEL_DIRECCIONES`, no en todas. Un `EXPOSE` no abre
# nada (es solo documentación), pero dejar aquí un 8080 sugeriría que el puerto está
# abierto al mundo, y no lo está: `0.0.0.0` no aparece en ningún sitio.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ORQUESTADOR_DB=/datos/orquestador.sqlite3 \
    PANEL_RAIZ_WEB=/srv/web \
    PYTHONPATH=/app/orquestador:/app/panel

# `PYTHONPATH` está por `api.py`, y es obligatorio, no una comodidad. Python mete en
# `sys.path` el directorio del guion que ejecuta, que al lanzar `python panel/api.py` es
# `/app/panel`. El directorio de trabajo (`/app`) NO se añade: solo se añade si el guion
# se ejecuta con `-`, o sea por la entrada estándar. Así que sin `PYTHONPATH` el primer
# `import config` de `api.py` falla con `ModuleNotFoundError` y el contenedor muere
# entero, sin llegar a abrir el puerto.
#
# `/app/panel` está por si algún módulo del panel acaba importando a otro del panel. Hoy
# no hace falta (los ficheros se cargan por ruta, no por paquete), pero ponerlo evita
# que añadir un import entre ellos rompa el arranque.
#
# Con `WORKDIR /app` y el comando tal cual, el proceso escucha en las direcciones de
# `PANEL_DIRECCIONES`. No se usa `-m`, porque `api.py` no es un paquete y su `if
# __name__ == "__main__"` solo se ejecuta si es el guion principal.
CMD ["python", "panel/api.py"]