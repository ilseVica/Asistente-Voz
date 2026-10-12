# Servicio de voz (Vosk + WebSocket) - etapa A del Paso 2.
#
# Imagen minima. El modelo NO se copia aqui: se monta en solo-lectura desde el
# host en /modelo (ver descargar-modelo.sh y docker-compose.yml). Asi la imagen
# pesa poco y el modelo, que es un dato, no vive dentro de una capa de Docker.

FROM python:3.12-slim

# El proceso no necesita escribir en su sistema de archivos; el compose lo
# levanta con read_only y le da /tmp como tmpfs.
RUN useradd --system --create-home --uid 1000 voz

WORKDIR /app

COPY requerimientos.txt ./
RUN pip install --no-cache-dir -r requerimientos.txt

COPY --chown=voz:voz . .

USER voz

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 2700

CMD ["python", "servidor_voz.py"]
