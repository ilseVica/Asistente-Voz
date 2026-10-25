# Diseño del panel — 24 oct 2026

Referencia visual aprobada por Ilse para el panel web de familiares. Fuente: lienzo «Asistente de Voz — Prototipo del panel» (Claude Design). Este diseño manda sobre los cinco HTML de `fase4_md/files/` del 4 oct.

Cada archivo es una pantalla. El marcado está dentro de `<x-dc>…</x-dc>` y usa estilos en línea: los colores, tamaños de letra, espaciados, radios y textos son los que hay que reproducir en Flutter. Las líneas `<script src="./support.js">` y el bloque `<script type="text/x-dc">` son del editor de diseño y se ignoran. Los datos (nombres, horas, lecturas) son de ejemplo: el panel muestra los reales.

## Tamaños

| Prefijo | Tamaño | Diseño del panel |
|---|---|---|
| `Esc` | 1280 px (contenido de máx. 1200) | Escritorio, ancho ≥ 900 px (también iPad horizontal) |
| `Tab` | 820 px | iPad vertical: barra inferior, tarjetas en 2 columnas |
| `Mov` | 390 px | Celular: barra inferior |

## Pantallas (cada una existe con los tres prefijos)

| Archivo | Pantalla |
|---|---|
| `…Panel.dc.html` | Inicio con la sala conectada |
| `…PanelSinConexion.dc.html` | Inicio sin conexión (píldora y banner ámbar, «Sin datos») |
| `…PanelAlerta.dc.html` | Inicio con alerta de gas activa (píldora y banner rojos) |
| `…Historial.dc.html` | Historial de alertas |
| `…Alerta.dc.html` | Detalle de una alerta |
| `…Recordatorios.dc.html` | Lista de recordatorios |
| `…NuevoRecordatorio.dc.html` | Formulario de nuevo recordatorio |
| `…Inactividad.dc.html` | Diálogo «Aviso por inactividad» |
| `…Familiares.dc.html` | Familiares |
| `…Acceso.dc.html` | Entrar con el código (con estado de error) |
| `…Privacidad.dc.html` | Aviso de privacidad («Sus datos») |

## Paleta

Fondo #F4F5F2 · texto #1B1F1E · texto secundario #4A524F · bordes #DADFDB · primario #0E5E5A · primario suave #E3F0EE · peligro #9F1D1D · peligro suave #F7E1E1 · ámbar fondo #FBEFD9, texto #7A4205 / #5C3204, borde #E8C98F · gris pastilla #ECEEEC. Fuente: Atkinson Hyperlegible 400 y 700.
