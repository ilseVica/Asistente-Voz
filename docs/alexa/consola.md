# Skill «Asistente de casa» en la consola de Alexa

## 1. Crear la skill

1. Consola de desarrollador de Alexa: https://developer.amazon.com/alexa/console/ask
2. Create Skill.
   - Skill name: `Asistente de casa`
   - Default language: `Spanish (MX)` (es-MX)
   - Choose a model: `Custom`
   - Hosting: `Provision your own` (HTTPS)
3. Invocation name: `asistente de casa`.

## 2. Modelo de interacción

1. En la pestaña Build, abra el JSON Editor.
2. Pegue el contenido de `resultados/alexa-modelo-es-MX.json` (solo el JSON, sin texto extra).
3. Save Model y Build Model.

## 3. Endpoint

1. En Endpoint, elija HTTPS.
2. Default Region URL: `https://asistente.tail722206.ts.net/alexa`
3. SSL certificate type: `My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority` (Tailscale Funnel ya sirve HTTPS válido).

## 4. Permisos de cuenta (no se usan)

No se piden permisos de cuenta. La skill no accede a datos del usuario.

## 5. Pruebas en la consola

En la pestaña Test, con Development activado:

- «abre asistente de casa»
- «enciende la luz»
- «apaga la luz»
- «ayuda»
- «para» / «cancela»

Verifique en los logs del panel (`docker logs asistente-panel`) que la petición se aceptó y que el userId/deviceId salen enmascarados.
