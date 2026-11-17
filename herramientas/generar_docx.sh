#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="/home/ilse/Servidores/documentacion"
OUT_NAME="Lucia-documentacion-fases-1-a-6.docx"
OUT_PATH="$OUT_DIR/$OUT_NAME"

mkdir -p "$OUT_DIR"

if [ -f "$OUT_PATH" ]; then
  mv -f "$OUT_PATH" "$OUT_PATH.anterior"
fi

export REPO_DIR OUT_DIR OUT_NAME
export LD_LIBRARY_PATH="/snap/mesa-2404/1839/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"

python3 - "$REPO_DIR" "$OUT_PATH" <<'PY'
import os, sys, re, subprocess, shutil
from pathlib import Path

repo = Path(sys.argv[1])
out_path = Path(sys.argv[2])
out_dir = out_path.parent
img_dir = Path("/home/ilse/Servidores/documentacion/imagenes")

from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

PURPLE = RGBColor(0x5A, 0x35, 0x63)

doc = Document()

# Base styles
style = doc.styles['Normal']
style.font.name = 'Fredoka'
style.font.size = Pt(11)
style.paragraph_format.space_after = Pt(6)
style.paragraph_format.line_spacing = 1.08

for sec in doc.sections:
    sec.page_width = Inches(8.5)
    sec.page_height = Inches(11)
    sec.top_margin = Cm(2.5)
    sec.bottom_margin = Cm(2.5)
    sec.left_margin = Cm(2.5)
    sec.right_margin = Cm(2.5)

for hname, size in (('Heading 1', 16), ('Heading 2', 13), ('Heading 3', 11.5)):
    st = doc.styles[hname]
    st.font.name = 'Fredoka'
    st.font.size = Pt(size)
    st.font.color.rgb = PURPLE
    st.font.bold = True

def shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hexcolor)
    tcPr.append(shd)

def set_cell_text(cell, text, bold=False, size=9):
    cell.text = ''
    p = cell.paragraphs[0]
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.name = 'Fredoka'

def redact(text):
    text = re.sub(r'\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[omitido]', text)
    text = re.sub(r'\b[a-zA-Z0-9-]+\.ts\.net\b', '[omitido]', text)
    text = re.sub(r'\b[a-zA-Z0-9-]+\.tailscale\.com\b', '[omitido]', text)
    text = text.replace('208.111.34.11', '[omitido]')
    text = text.replace('ilsevillaca@gmail.com', '[omitido]')
    return text

def add_para(text, style=None, bold=False, italic=False, mono=False, size=None, space_after=None):
    p = doc.add_paragraph(style=style)
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    r = p.add_run(text)
    r.bold = bold
    r.italic = italic
    if mono:
        r.font.name = 'Courier New'
        r.font.size = Pt(9)
    elif size:
        r.font.size = Pt(size)
    return p

def add_code_block(code):
    for line in code.split('\n'):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.0
        r = p.add_run(line if line != '' else ' ')
        r.font.name = 'Courier New'
        r.font.size = Pt(9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)

def add_bullet(text, level=0):
    style = 'List Bullet' if level == 0 else 'List Bullet 2'
    p = doc.add_paragraph(text, style=style)
    return p

def add_number(text):
    return doc.add_paragraph(text, style='List Number')

def md_inline(text):
    # returns list of (text, bold, italic)
    parts = []
    # simple **bold** and *italic*
    token_re = re.compile(r'(\*\*[^*]+\*\*|\*[^*]+\*)')
    for chunk in token_re.split(text):
        if chunk.startswith('**') and chunk.endswith('**'):
            parts.append((chunk[2:-2], True, False))
        elif chunk.startswith('*') and chunk.endswith('*'):
            parts.append((chunk[1:-1], False, True))
        else:
            parts.append((chunk, False, False))
    return parts

def add_rich_para(text, style=None):
    p = doc.add_paragraph(style=style)
    for t, b, i in md_inline(text):
        r = p.add_run(t)
        r.bold = b
        r.italic = i
    return p

def add_table(rows):
    if not rows:
        return
    # rows: list of list of str
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=ncols)
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row in enumerate(rows):
        cells = table.add_row().cells
        for j in range(ncols):
            val = row[j] if j < len(row) else ''
            set_cell_text(cells[j], redact(val), bold=(i == 0))
        if i == 0:
            for j in range(ncols):
                shade(table.rows[0].cells[j], 'D9C9E0')
    # set column widths roughly
    for r in table.rows:
        for c in r.cells:
            c.width = Inches(6.5 / ncols)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)

def parse_markdown_table(lines, start):
    # returns (rows, end_index)
    rows = []
    i = start
    while i < len(lines) and '|' in lines[i]:
        line = lines[i].strip()
        if re.match(r'^\|[\s\-:|]+\|$', line):
            i += 1
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        rows.append(cells)
        i += 1
    return rows, i

def insert_markdown(md_text, skip_headings=None, demote=0):
    skip_headings = skip_headings or []
    lines = md_text.split('\n')
    i = 0
    n = len(lines)
    # accumulate normal paragraph
    para_lines = []
    def flush_para():
        nonlocal para_lines
        if para_lines:
            text = ' '.join(l.strip() for l in para_lines if l.strip())
            if text:
                add_rich_para(redact(text))
            para_lines = []
    while i < n:
        line = lines[i]
        stripped = line.strip()
        # code fence
        if stripped.startswith('```'):
            flush_para()
            fence = stripped
            i += 1
            code = []
            while i < n and not lines[i].strip().startswith('```'):
                code.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            add_code_block(redact('\n'.join(code)))
            continue
        # heading
        m = re.match(r'^(#{1,3})\s+(.*)$', stripped)
        if m:
            flush_para()
            level = min(3, len(m.group(1)) + demote)
            title = redact(m.group(2).strip())
            if title in skip_headings:
                # skip until next same-or-higher heading
                i += 1
                while i < n:
                    s = lines[i].strip()
                    m2 = re.match(r'^(#{1,3})\s+', s)
                    if m2 and len(m2.group(1)) <= level:
                        break
                    i += 1
                continue
            style = 'Heading 1' if level == 1 else ('Heading 2' if level == 2 else 'Heading 3')
            doc.add_heading(title, level=level) if False else None
            p = doc.add_paragraph(style=style)
            r = p.add_run(title)
            r.font.color.rgb = PURPLE
            r.bold = True
            i += 1
            continue
        # table
        if '|' in stripped and stripped.startswith('|'):
            flush_para()
            rows, i = parse_markdown_table(lines, i)
            add_table(rows)
            continue
        # image
        mimg = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)$', stripped)
        if mimg:
            flush_para()
            alt = mimg.group(1)
            imgpath = (repo / mimg.group(2)).resolve() if not Path(mimg.group(2)).is_absolute() else Path(mimg.group(2))
            # also try relative to repo
            if not imgpath.exists():
                imgpath = (repo / mimg.group(2).lstrip('./')).resolve()
            if imgpath.exists():
                add_image(str(imgpath), alt)
            else:
                add_para(f"[Imagen no encontrada: {mimg.group(2)}]", italic=True)
            i += 1
            continue
        # bullet
        if re.match(r'^[-*]\s+', stripped):
            flush_para()
            add_bullet(redact(re.sub(r'^[-*]\s+', '', stripped)))
            i += 1
            continue
        # numbered
        if re.match(r'^\d+\.\s+', stripped):
            flush_para()
            add_number(redact(re.sub(r'^\d+\.\s+', '', stripped)))
            i += 1
            continue
        # blank
        if stripped == '':
            flush_para()
            i += 1
            continue
        para_lines.append(line)
        i += 1
    flush_para()

def add_image(path, caption=None, width=6.5):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(path, width=Inches(width))
    if caption:
        cap = doc.add_paragraph(caption)
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in cap.runs:
            r.italic = True
            r.font.size = Pt(9)

def convert_svg_to_png(svg_path, png_path):
    rsvg = '/snap/gnome-46-2404/177/usr/bin/rsvg-convert'
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = '/snap/mesa-2404/1839/usr/lib/x86_64-linux-gnu'
    try:
        subprocess.run([rsvg, '-w', '2000', '-o', str(png_path), str(svg_path)],
                       check=True, env=env, capture_output=True)
        if png_path.exists() and png_path.stat().st_size > 0:
            return True
    except Exception as e:
        pass
    # fallback cairosvg
    try:
        import cairosvg
    except Exception:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'cairosvg', '--break-system-packages', '-q'], check=True)
        import cairosvg
    cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=2000)
    return png_path.exists() and png_path.stat().st_size > 0

def extract_pdf_text(pdf_path):
    out_txt = Path('/tmp/opencode') / (Path(pdf_path).stem + '.txt')
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['pdftotext', '-layout', str(pdf_path), str(out_txt)], check=True)
    return out_txt.read_text(encoding='utf-8', errors='replace')

def split_bitacora_by_date(md_text):
    # returns list of (date_str, content) sorted by date
    month_map = {
        'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
        'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
    lines = md_text.split('\n')
    sections = []
    current_title = None
    current_lines = []
    for line in lines:
        m = re.match(r'^##\s+(.+)$', line.strip())
        if m:
            if current_title is not None:
                sections.append((current_title, '\n'.join(current_lines)))
            current_title = m.group(1).strip()
            current_lines = [line]
        else:
            if current_title is None:
                # preamble before first H2
                if not sections and not current_lines:
                    current_lines.append(line)
                else:
                    current_lines.append(line)
            else:
                current_lines.append(line)
    if current_title is not None:
        sections.append((current_title, '\n'.join(current_lines)))
    # separate preamble (title etc.)
    preamble = ''
    if sections and not re.match(r'^##\s+', sections[0][0] if False else ''):
        pass
    # sort sections by date parsed from title
    def sort_key(item):
        title = item[0].lower()
        # find day number and month
        mday = re.search(r'(\d{1,2})\s+de\s+([a-záéíóú]+)', title)
        if mday:
            day = int(mday.group(1))
            mon = month_map.get(mday.group(2), 0)
            # year from title if present else 2026
            myear = re.search(r'(\d{4})', title)
            year = int(myear.group(1)) if myear else 2026
            return (year, mon, day)
        return (9999, 99, 99)
    # keep first non-date block (intro) first
    dated = [s for s in sections if re.search(r'\d{1,2}\s+de\s+[a-záéíóú]+', s[0].lower())]
    other = [s for s in sections if s not in dated]
    dated_sorted = sorted(dated, key=sort_key)
    return other + dated_sorted

# ---------- Portada ----------
title_p = doc.add_paragraph()
title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = title_p.add_run('Lucía · Asistente de voz para adultos mayores')
r.bold = True; r.font.size = Pt(20); r.font.color.rgb = PURPLE
sub = doc.add_paragraph()
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = sub.add_run('Documentación del proyecto, fases 1 a 6')
r.font.size = Pt(14); r.font.color.rgb = PURPLE
meta = doc.add_paragraph()
meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
import datetime
r = meta.add_run(f'Fecha de generación: {datetime.date.today().strftime("%d de %B de %Y")}\n')
r.font.size = Pt(10)
r = meta.add_run(f'Rama: {subprocess.check_output(["git","-C",str(repo),"branch","--show-current"]).decode().strip()}  ·  Commit: {subprocess.check_output(["git","-C",str(repo),"rev-parse","--short","HEAD"]).decode().strip()}')
r.font.size = Pt(10)
doc.add_page_break()

# ---------- TOC ----------
toc_heading = doc.add_paragraph(style='Heading 1')
r = toc_heading.add_run('Índice')
r.font.color.rgb = PURPLE
# TOC field
p = doc.add_paragraph()
fldChar1 = OxmlElement('w:fldChar'); fldChar1.set(qn('w:fldCharType'), 'begin')
instrText = OxmlElement('w:instrText'); instrText.set(qn('xml:space'), 'preserve'); instrText.text = 'TOC \\o "1-2" \\h \\z \\u'
fldChar2 = OxmlElement('w:fldChar'); fldChar2.set(qn('w:fldCharType'), 'separate')
fldChar3 = OxmlElement('w:fldChar'); fldChar3.set(qn('w:fldCharType'), 'end')
run = p.add_run()
run._r.append(fldChar1); run._r.append(instrText); run._r.append(fldChar2); run._r.append(fldChar3)
doc.add_page_break()

# ---------- Resumen del proyecto ----------
doc.add_heading('Resumen del proyecto', level=1)
resumen = (
    "Lucía es un asistente de voz para adultos mayores que controla una luz de la sala, "
    "detecta gas e inactividad, responde consultas sencillas (hora, fecha, temperatura, "
    "recordatorios) y comunica a la familia por Telegram y panel web. El sistema se "
    "compone de un nodo ESP32 con sensores, un servidor local (Mosquitto, Vosk, "
    "orquestador, panel) y un satélite de voz en una laptop. Este documento reúne la "
    "documentación de las fases 1 a 6 del proyecto a partir de los archivos del "
    "repositorio."
)
add_para(resumen)
doc.add_page_break()

# ---------- Fase 1 ----------
doc.add_heading('Fase 1 · Investigación y análisis', level=1)
fuentes_dir = Path("/home/ilse/Servidores/documentacion/fuentes")
f1_file = None
if fuentes_dir.exists():
    for cand in sorted(fuentes_dir.iterdir()):
        if cand.is_file():
            f1_file = cand
            break
if f1_file:
    if f1_file.suffix.lower() in ('.md', '.txt'):
        insert_markdown(f1_file.read_text(encoding='utf-8'), demote=1)
    else:
        add_para(f"[Archivo de fuente: {f1_file.name}]")
else:
    add_para('Pendiente: sin documento en el repositorio', italic=True)

# ---------- Fase 2 ----------
doc.add_heading('Fase 2 · Definición de requisitos', level=1)
pdf2 = repo / 'docs' / 'Etapa 3 - Definicion de requisitos actualizada.pdf'
txt2 = extract_pdf_text(pdf2)
insert_markdown('## Texto extraído del PDF\n\n' + txt2)

# ---------- Fase 3 ----------
doc.add_heading('Fase 3 · Diseño y selección de componentes', level=1)
pdf3 = repo / 'docs' / 'Etapas 4 y 5 - Arquitectura y componentes actualizada.pdf'
txt3 = extract_pdf_text(pdf3)
insert_markdown('## Texto extraído del PDF\n\n' + txt3)

# ---------- Fase 4 ----------
doc.add_heading('Fase 4 · Desarrollo e integración', level=1)
md4_path = repo / 'fase4_md' / 'Fase_4_Desarrollo_e_Integracion.md'
md4 = md4_path.read_text(encoding='utf-8')
# remove plan de pruebas section (goes to Fase 5)
insert_markdown(md4, skip_headings=['Plan de pruebas'], demote=1)
# note about wake word contradiction will be inserted after architecture? Insert note here.
add_para('Nota (actualización nov): donde este documento indique que la palabra de activación «Lucía» se descartó, se implementó posteriormente; se activa con lucia.activa en config.toml.', italic=True)

# Arquitectura final
doc.add_heading('Arquitectura final del sistema', level=2)
img_lucia = img_dir / 'Lucia-asistente-armado.jpg'
if img_lucia.exists():
    add_image(str(img_lucia), 'Lucía, asistente de voz: armado', width=6.5)
arch_svg = repo / 'fase4_md' / 'img' / 'arquitectura.svg'
arch_png = out_dir / '_arch.png'
if convert_svg_to_png(arch_svg, arch_png):
    add_image(str(arch_png), 'Arquitectura del prototipo: sitio de la demo, Internet y servidor en casa', width=6.5)

# Estación de la sala
doc.add_heading('Estación de la sala: torre, lámpara y cubeta', level=2)
est_md = Path('/home/ilse/Servidores/documentacion/fuentes/Estacion-de-la-sala-torre-lampara-y-cubeta.md')
if est_md.exists():
    insert_markdown(est_md.read_text(encoding='utf-8'), demote=1)
else:
    add_para('Pendiente: sin documento en el repositorio', italic=True)
est_svg = img_dir / 'Estacion de la sala - torre y lampara (11 nov).svg'
est_png = out_dir / '_est.png'
if convert_svg_to_png(est_svg, est_png):
    add_image(str(est_png), 'Estación de la sala: torre y lámpara', width=6.5)

# Conexiones eléctricas
doc.add_heading('Conexiones eléctricas', level=2)
conex = (repo / 'docs' / 'CONEXIONES.md').read_text(encoding='utf-8')
insert_markdown(conex, demote=1)
esq_svg = img_dir / 'Esquema de conexiones - montaje completo (11 nov).svg'
esq_png = out_dir / '_esq.png'
if convert_svg_to_png(esq_svg, esq_png):
    add_image(str(esq_png), 'Esquema de conexiones: montaje completo', width=6.5)

# ---------- Fase 5 ----------
doc.add_heading('Fase 5 · Pruebas y evaluación', level=1)
# extract plan de pruebas section from md4
m = re.search(r'^##\s+Plan de pruebas\s*$', md4, re.MULTILINE)
plan_text = ''
if m:
    start = m.start()
    # until end of file or next top-level heading of same level
    rest = md4[start:]
    # cut at next '\n## ' after first
    idx = rest.find('\n## ', 1)
    if idx != -1:
        plan_text = rest[:idx]
    else:
        plan_text = rest
insert_markdown(plan_text)
add_para('Resumen del plan de pruebas: 59 casos — 39 aprobadas, 6 parciales, 11 descartadas, 3 no aplica.', bold=True)

# ---------- Fase 6 ----------
doc.add_heading('Fase 6 · Mejoras y documentación', level=1)
# mejoras de noviembre segun bitacora
add_para('Mejoras de noviembre (según la bitácora): «Lucía cuida» (frases de la vida diaria y saludo diario apagado por defecto), saludo con escucha automática, panel con la marca Lucía (base neutra, flamita, agua junto a movimiento, pestaña con línea rosa), sensor de agua habilitado y calibrado (20/5 cm), gráfica de identidad visual.', )
# identidad visual
doc.add_heading('Identidad visual de Lucía', level=2)
id_md = Path('/home/ilse/Servidores/documentacion/fuentes/Identidad-visual-de-Lucia.md')
if id_md.exists():
    insert_markdown(id_md.read_text(encoding='utf-8'), demote=1)
else:
    add_para('Pendiente: sin documento en el repositorio', italic=True)
# manual de uso
doc.add_heading('Manual de uso', level=2)
manual = (repo / 'docs' / 'MANUAL.md').read_text(encoding='utf-8')
insert_markdown(manual, demote=1)
# trabajo futuro
doc.add_heading('Trabajo futuro: música por voz y «Lucía, …» de corrido', level=2)
add_para('Música por voz (MUSICA_HABILITADO=0): las frases existen, pero la orden no llega al satélite; se mantiene apagada para la demostración.')
add_para('Palabra de activación «Lucía» de corrido («Lucía, prende la luz») no funciona: primero la palabra de activación y después la orden.')
# alexa subseccion
doc.add_heading('Skill de Alexa (subsección)', level=2)
alexa_consola = (repo / 'docs' / 'alexa' / 'consola.md').read_text(encoding='utf-8')
alexa_pruebas = (repo / 'docs' / 'alexa' / 'pruebas.md').read_text(encoding='utf-8')
insert_markdown(alexa_consola, demote=1)
insert_markdown(alexa_pruebas, demote=1)

# ---------- Anexo A: Bitacora ----------
doc.add_page_break()
doc.add_heading('Anexo A · Bitácora', level=1)
bitacora = (repo / 'docs' / 'BITACORA.md').read_text(encoding='utf-8')
# remove top title
bitacora = re.sub(r'^#\s+Bitácora del proyecto\s*\n', '', bitacora)
sections = split_bitacora_by_date(bitacora)
for title, content in sections:
    # promote H2 date to heading 2
    first_line = content.split('\n', 1)[0]
    if first_line.startswith('## '):
        doc.add_heading(first_line[3:].strip(), level=2)
        body = content.split('\n', 1)[1] if '\n' in content else ''
    else:
        body = content
    # render body as markdown (without the heading)
    insert_markdown(body, demote=1)
# note pending timezone
add_para('Nota: la zona horaria del ESP32 (firmware/include/config.h) sigue con "America/Mazatlan"; la bitácora señala que debe ser "MST7". Pendiente, no se modifica el firmware.', italic=True)

# ---------- Anexo B: Cronograma ----------
doc.add_heading('Anexo B · Cronograma', level=1)
crono = repo / 'fase4_md' / 'img' / 'cronograma.png'
if crono.exists():
    add_image(str(crono), 'Cronograma: 8 pasos en 7 semanas', width=6.5)

# redact all text in document
def redact_document(d):
    for p in d.paragraphs:
        for r in p.runs:
            if r._r.find(qn('w:drawing')) is None and r._r.find(qn('w:pict')) is None:
                r.text = redact(r.text)
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for r in p.runs:
                        if r._r.find(qn('w:drawing')) is None and r._r.find(qn('w:pict')) is None:
                            r.text = redact(r.text)
redact_document(doc)

# Note: redact already applied during insertion mostly; ensure code blocks etc.
doc.save(str(out_path))
print(f"OK: {out_path}")
PY

echo "=== ls -la ==="
ls -la /home/ilse/Servidores/documentacion/
echo "=== media count ==="
cd /home/ilse/Servidores/documentacion && unzip -l Lucia-documentacion-fases-1-a-6.docx | tail -5
echo "word/media count:"
unzip -l Lucia-documentacion-fases-1-a-6.docx | grep -c 'word/media/'
