# Pruebas de la skill (Fase A)

## Automáticas

Desde `servidor/`:

```
docker run --rm -v "$PWD:/app" -w /app asistente-panel:1.0 \
  sh -c "pip install -q -r panel/requerimientos-pruebas.txt && \
         python panel/pruebas_api.py"
```

Incluye los casos de Alexa: petición válida, firma inválida, marca de tiempo vieja,
applicationId ajeno, bandera apagada, luz con confirmación y sin confirmación
(timeout) y ayuda.

## Manuales (sin desplegar)

1. Levante el panel en modo desarrollo con la skill habilitada:
   `ALEXA_SKILL_HABILITADO=1 ALEXA_SKILL_ID=<id> PANEL_SIN_MQTT_EN_PRUEBAS=0`.
2. Envíe una petición firmada por Alexa (o use la consola de pruebas) a
   `https://asistente.tail722206.ts.net/alexa`.
3. Verifique en los logs que el userId y deviceId aparecen enmascarados.
4. Con la luz apagada, diga «enciende la luz» y confirme que el relé se mueve y
   Alexa responde «He encendido la luz».
5. Repita con el nodo desconectado para comprobar el mensaje de no confirmación.
6. Diga «ayuda» dos veces seguidas y verifique que solo llega un aviso a Telegram.

## Plan de despliegue (sin ejecutar)

1. `docker tag asistente-panel:1.0 asistente-panel:pre-alexa`
2. Reconstruir solo el panel: `docker compose build panel` (sin `--no-deps` no aplica; el panel no depende de otros servicios).
   Para no tocar dependencias: `docker compose up -d --no-deps panel`
3. Si cambió `acl.conf`, recargar Mosquitto con HUP:
   `docker kill -s HUP asistente-mosquitto`
   Solo si cambió.
