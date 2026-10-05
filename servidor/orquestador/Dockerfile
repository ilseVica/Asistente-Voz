# Imagen del orquestador.
#
# Sale de la imagen oficial de Python, no de node ni de alpine: las otras dos
# estarian muy desactualizadas o no tendrian binarios para esta arquitectura.
# `python:3.12-slim` es la que trae exactamente la version que se probo.
FROM python:3.12-slim

# El servicio corre como un usuario sin privilegios. No es defensa en profundidad:
# es que un proceso de red que corre como root no tiene motivo para hacerlo, y si
# este servicio corre como root, un fallo aqui es un fallo del servidor entero.
RUN useradd --system --create-home --uid 1000 orquestador

WORKDIR /app

# Primero las dependencias, y después el codigo. Asi el `docker build` de un cambio
# de codigo no reinstala las librerias, y solo se reconstruye esta capa cuando
# cambian las versiones de `requerimientos.txt`.
COPY requerimientos.txt ./
RUN pip install --no-cache-dir -r requerimientos.txt

COPY --chown=orquestador:orquestador . .

# `/datos` es el volumen: la base de datos vive ahi. Sin volumen, cada
# `docker compose up` que recree el contenedor perderia los ids ya procesados, y
# con ellos la deduplicacion. Volver a mandar una alerta de gas que ya se habia
# avisado es justo lo que este servicio existe para evitar.
RUN mkdir -p /datos && chown orquestador:orquestador /datos

USER orquestador

# El puerto del bot de Telegram no hace falta abrirlo: la conexion es saliente.
EXPOSE 1883

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ORQUESTADOR_DB=/datos/orquestador.sqlite3

CMD ["python", "orquestador.py"]
