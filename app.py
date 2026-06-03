import streamlit as st
import os
import shutil
import zipfile
import json
import base64
import io
import re
import tempfile
from pathlib import Path
from datetime import datetime

import anthropic
import fitz
import pandas as pd
from pdf2image import convert_from_path

# ============================================================
# CONFIGURACION DE PAGINA
# ============================================================
st.set_page_config(
    page_title="Clasificador de Facturas",
    page_icon="📂",
    layout="centered"
)

# ============================================================
# API KEY — se lee desde Streamlit Secrets (no aparece en GitHub)
# En Streamlit Cloud: Settings → Secrets → agregar:
# ANTHROPIC_API_KEY = "sk-ant-..."
# ============================================================
API_KEY = st.secrets.get("ANTHROPIC_API_KEY", None)

# ============================================================
# LISTAS DE RUCs CONOCIDOS
# ============================================================
RUCS_BANCOS = {
    '20100047218': 'BCP',
    '20100130204': 'BBVA',
    '20354766437': 'Interbank',
    '20522108720': 'Scotiabank',
    '20258702832': 'BanBif',
    '20451844326': 'Pichincha',
    '20100105862': 'Banco de la Nacion',
}
RUCS_COMBUSTIBLE = {
    '20258092133': 'Repsol',
    '20100128056': 'Primax',
    '20330291017': 'Petroperu',
    '20543298922': 'Petrogas',
}
RUCS_RESTAURANTES = {
    '20509828235': 'KFC',
    '20268571286': 'McDonalds',
    '20505101688': 'Starbucks',
    '20388829452': 'Pizza Hut',
    '20424024268': 'Bembos',
    '20613563700': 'Pardos Chicken',
    '20563571498': 'Norkys',
    '20607085600': 'Popeyes',
}
RUCS_SEGUROS = {
    '20504262242': 'Rimac',
    '20552083401': 'Pacifico Seguros',
    '20608644467': 'La Positiva',
    '20100036773': 'Mapfre',
}
RUCS_SERVICIOS_PUBLICOS = {
    '20331898008': 'Luz del Sur',
    '20467534026': 'Claro',
    '20106253251': 'Movistar',
    '20602235914': 'Entel',
    '20100167628': 'Sedapal',
}
RUCS_BIENES = {
    '20512002090': 'Mifarma',
    '20100579228': 'Pareja Lecaros',
    '20602457029': 'Rigodent / Medical Dental',
    '20601096022': 'Fresh Life',
}

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.jfif'}
PDF_EXT   = {'.pdf'}
ALL_EXT   = IMAGE_EXT | PDF_EXT
NO_COMPROBANTE = {'guia_remision', 'nota_pedido', 'recibo_servicio',
                  'documento_autorizado', 'codigo_30', 'otro'}


# ============================================================
# PROMPT
# ============================================================
def get_prompt(mi_ruc):
    return f"""Eres un experto contable peruano. Analiza este documento y clasifícalo.

MONEDA:
- soles: S/, PEN, SOLES
- dolares: $, USD, DOLARES, US$
- desconocido: si no se puede determinar

TIPO DE DOCUMENTO:
- factura: Factura Electronica (serie F, E, FM, B, etc.)
- boleta: Boleta de Venta
- nota_credito: Nota de Credito
- nota_debito: Nota de Debito
- recibo_honorarios: Recibo por Honorarios
- guia_remision: Guia de Remision (documento de traslado, NO es comprobante de pago)
- nota_pedido: Nota de Pedido o Proforma
- recibo_servicio: Recibo de luz, agua, gas, telefono
- documento_autorizado: Peajes, maquinas registradoras
- codigo_30: Liquidacion de pasarela bancaria sin IGV desglosado
- otro: Cualquier otro documento

IMPORTANTE: Si el documento dice "NUM.GUIA" pero el encabezado dice FACTURA ELECTRONICA,
clasificalo como "factura". NUM.GUIA es solo un numero de referencia interno, no significa
que el documento sea una guia de remision.

CATEGORIA (solo para facturas, boletas y recibos por honorarios):
- banco: BCP, BBVA, Interbank, Scotiabank, BanBif, Pichincha. Generalmente sin IGV.
- combustible_peaje: Combustible (gasohol, diesel, gasolina, GLP) o peaje (autopista, via expresa)
- restaurante_consumo: Restaurantes, cafeterias, Starbucks, KFC, McDonalds, catering, delivery comida
- seguro: Seguro medico, SCTR, EPS, vida ley, poliza
- servicio_detraccion: Servicio CON detraccion (tiene seccion INFORMACION DE LA DETRACCION)
- servicio: Servicio SIN detraccion: internet, alquiler, mantenimiento, consultoria, limpieza, seguridad, transporte, dental a pacientes
- bien: Productos fisicos: insumos dentales (resinas, anestesia, brackets, fresas, guantes, silicona, postes, jeringas, colutorios), productos farmaceuticos, agua en bidon, materiales, equipos, mercaderia. Clave: lista productos con cantidad y precio unitario.

DATOS A EXTRAER:
- ruc_emisor: RUC del EMISOR (quien emite). NO es {mi_ruc}. 11 digitos.
- serie_numero: Serie y numero exacto (ej: F001-00001234)
- fecha_emision: Fecha en formato YYYY-MM-DD
- tiene_igv: true o false
- tiene_detraccion: true o false
- nombre_emisor: Nombre o razon social del emisor
- monto_total: Monto total numerico

Responde SOLO con este JSON sin texto adicional:
{{
  "moneda": "soles",
  "tipo_documento": "factura",
  "categoria": "bien",
  "ruc_emisor": "string o null",
  "serie_numero": "string o null",
  "fecha_emision": "YYYY-MM-DD o null",
  "tiene_igv": true,
  "tiene_detraccion": false,
  "nombre_emisor": "string o null",
  "monto_total": 0,
  "razon": "Una linea explicando la clasificacion"
}}"""


# ============================================================
# EXTRACCION DE IMAGEN
# ============================================================
def a_base64(path):
    ext = Path(path).suffix.lower()
    tipos = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.jfif': 'image/jpeg',
             '.png': 'image/png', '.webp': 'image/webp', '.bmp': 'image/bmp'}
    if ext in IMAGE_EXT:
        with open(path, 'rb') as f:
            return base64.standard_b64encode(f.read()).decode(), tipos[ext]
    elif ext == '.pdf':
        try:
            pages = convert_from_path(str(path), first_page=1, last_page=1, dpi=200)
            buf = io.BytesIO()
            pages[0].save(buf, format='PNG')
            return base64.standard_b64encode(buf.getvalue()).decode(), 'image/png'
        except Exception as e:
            st.warning(f"Error convirtiendo PDF: {e}")
    return None, None


# ============================================================
# CLASIFICACION CON CLAUDE
# ============================================================
def clasificar(path, mi_ruc, client):
    img_data, media_type = a_base64(path)
    if not img_data:
        return None
    try:
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=500,
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': img_data}},
                {'type': 'text', 'text': get_prompt(mi_ruc)}
            ]}]
        )
        raw = msg.content[0].text.strip()
        s, e = raw.find('{'), raw.rfind('}') + 1
        if s < 0:
            return None
        result = json.loads(raw[s:e])

        ruc = result.get('ruc_emisor', '')
        if ruc in RUCS_BANCOS:
            result['categoria'] = 'banco'
            result['nombre_emisor'] = result.get('nombre_emisor') or RUCS_BANCOS[ruc]
        elif ruc in RUCS_COMBUSTIBLE:
            result['categoria'] = 'combustible_peaje'
            result['nombre_emisor'] = result.get('nombre_emisor') or RUCS_COMBUSTIBLE[ruc]
        elif ruc in RUCS_RESTAURANTES:
            result['categoria'] = 'restaurante_consumo'
            result['nombre_emisor'] = result.get('nombre_emisor') or RUCS_RESTAURANTES[ruc]
        elif ruc in RUCS_SEGUROS:
            result['categoria'] = 'seguro'
            result['nombre_emisor'] = result.get('nombre_emisor') or RUCS_SEGUROS[ruc]
        elif ruc in RUCS_SERVICIOS_PUBLICOS:
            result['tipo_documento'] = 'recibo_servicio'
            result['categoria'] = None
            result['nombre_emisor'] = result.get('nombre_emisor') or RUCS_SERVICIOS_PUBLICOS[ruc]
        elif ruc in RUCS_BIENES:
            result['nombre_emisor'] = result.get('nombre_emisor') or RUCS_BIENES[ruc]
            # Solo forzar "bien" si es comprobante de pago, no una guia o nota de pedido
            if result.get('tipo_documento') not in ('guia_remision', 'nota_pedido', 'otro'):
                result['categoria'] = 'bien'
                result['tipo_documento'] = 'factura'

        return result
    except Exception as ex:
        st.warning(f"Error API: {ex}")
        return None


# ============================================================
# CARPETA DESTINO
# ============================================================
def carpeta_destino(result, base_path):
    moneda = result.get('moneda', 'desconocido')
    tipo   = result.get('tipo_documento', 'otro')
    cat    = result.get('categoria')
    detrac = result.get('tiene_detraccion', False)

    base = Path(base_path)
    if moneda == 'soles':
        cur = base / 'Soles'
    elif moneda == 'dolares':
        cur = base / 'Dolares'
    else:
        cur = base / 'Moneda No Detectada'

    if tipo in NO_COMPROBANTE:
        return cur / 'Otros Documentos'
    if tipo == 'nota_credito':
        return cur / 'Notas de Credito'
    if tipo == 'nota_debito':
        return cur / 'Notas de Debito'
    if cat == 'banco':
        return cur / 'Bancos'
    if cat == 'combustible_peaje':
        return cur / 'Combustible y Peajes'
    if cat == 'restaurante_consumo':
        return cur / 'Restaurantes y Consumos'
    if cat == 'seguro':
        return cur / 'Seguros'
    if cat == 'servicio_detraccion' or detrac:
        return cur / 'Servicios' / 'Con Detraccion'
    if cat == 'servicio':
        return cur / 'Servicios' / 'Sin Detraccion'
    return cur / 'Bienes'


def copiar_seguro(src, dest_folder):
    dest_folder.mkdir(parents=True, exist_ok=True)
    dst = dest_folder / Path(src).name
    c = 1
    while dst.exists():
        dst = dest_folder / (Path(src).stem + '_' + str(c) + Path(src).suffix)
        c += 1
    shutil.copy2(src, dst)


# ============================================================
# INTERFAZ STREAMLIT
# ============================================================
st.title("📂 Clasificador de Facturas")
st.caption("Soporta JPG, JFIF, PNG, PDF (incluso escaneados). Separa Soles/Dólares automáticamente.")
st.divider()

if 'uploader_key' not in st.session_state:
    st.session_state['uploader_key'] = 0

if not API_KEY:
    api_key_input = st.text_input("API Key de Anthropic", type="password", placeholder="sk-ant-...")
    API_KEY = api_key_input

col1, col2 = st.columns(2)
with col1:
    mi_ruc = st.text_input("RUC de tu empresa", placeholder="11 digitos")
with col2:
    mes_trabajo = st.text_input("Mes (MM)", placeholder="05", max_chars=2)

st.divider()
st.subheader("📤 Sube tus documentos")
archivos_subidos = st.file_uploader(
    "Arrastra o selecciona archivos (JPG, JFIF, PNG, PDF) o un ZIP con todos",
    type=["jpg", "jpeg", "jfif", "png", "webp", "bmp", "pdf", "zip"],
    accept_multiple_files=True,
    key=f"uploader_{st.session_state['uploader_key']}"
)
st.divider()

if st.button("🚀 Clasificar documentos", type="primary", use_container_width=True):

    if not API_KEY:
        st.error("Ingresa tu API Key de Anthropic.")
        st.stop()
    if not re.fullmatch(r'\d{11}', mi_ruc.strip()):
        st.error("RUC invalido. Debe tener exactamente 11 digitos.")
        st.stop()
    if not re.fullmatch(r'(0[1-9]|1[0-2])', mes_trabajo.strip()):
        st.error("Mes invalido. Usa formato MM (01-12). Ej: 05")
        st.stop()
    if not archivos_subidos:
        st.error("Sube al menos un archivo.")
        st.stop()

    client = anthropic.Anthropic(api_key=API_KEY)

    with tempfile.TemporaryDirectory() as tmp:
        entrada = Path(tmp) / 'entrada'
        salida  = Path(tmp) / 'Clasificados'
        entrada.mkdir()
        salida.mkdir()

        for archivo in archivos_subidos:
            dest = entrada / archivo.name
            dest.write_bytes(archivo.read())
            if archivo.name.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(dest, 'r') as zf:
                        zf.extractall(entrada)
                    dest.unlink()
                except zipfile.BadZipFile:
                    st.warning(f"ZIP corrupto: {archivo.name}")

        archivos = sorted([f for f in entrada.rglob('*') if f.suffix.lower() in ALL_EXT])
        if not archivos:
            st.error("No se encontraron archivos de imagen o PDF.")
            st.stop()

        st.info(f"Procesando {len(archivos)} archivos...")
        progress = st.progress(0)
        status   = st.empty()

        vistos     = {}
        resultados = []

        for i, f in enumerate(archivos):
            progress.progress((i + 1) / len(archivos))
            status.text(f"[{i+1}/{len(archivos)}] {f.name[:60]}")

            result = clasificar(f, mi_ruc.strip(), client)

            if result is None:
                copiar_seguro(f, salida / 'No Procesados')
                resultados.append({'Archivo': f.name, 'Carpeta': 'No Procesados', 'Estado': 'Error'})
                continue

            alerta_fecha = False
            fecha_str = result.get('fecha_emision')
            if fecha_str:
                try:
                    mes_doc = datetime.strptime(fecha_str, '%Y-%m-%d').month
                    alerta_fecha = str(mes_doc).zfill(2) != mes_trabajo.strip()
                except:
                    pass

            ruc_e = result.get('ruc_emisor') or ''
            serie = result.get('serie_numero') or ''
            clave = ruc_e + '|' + serie
            es_dup = (clave not in ('|', '') and clave in vistos)

            if es_dup:
                dest = salida / 'Duplicados'
            else:
                if clave not in ('|', ''):
                    vistos[clave] = f.name
                dest = carpeta_destino(result, salida)

            copiar_seguro(f, dest)
            rel = str(dest.relative_to(salida))

            resultados.append({
                'Archivo'         : f.name,
                'Emisor'          : result.get('nombre_emisor', ''),
                'RUC Emisor'      : result.get('ruc_emisor', ''),
                'Tipo'            : result.get('tipo_documento', ''),
                'Categoria'       : result.get('categoria', ''),
                'Moneda'          : result.get('moneda', ''),
                'Serie / Numero'  : result.get('serie_numero', ''),
                'Fecha Emision'   : result.get('fecha_emision', ''),
                'Monto Total'     : result.get('monto_total', ''),
                'Tiene IGV'       : result.get('tiene_igv', ''),
                'Tiene Detraccion': result.get('tiene_detraccion', ''),
                'Carpeta'         : rel,
                'Alerta Fecha'    : alerta_fecha,
                'Duplicado'       : es_dup,
            })

        progress.progress(1.0)
        status.empty()

        # Excel en memoria
        df = pd.DataFrame(resultados)
        excel_buf = io.BytesIO()
        with pd.ExcelWriter(excel_buf, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Todos', index=False)
            dups = df[df['Duplicado'] == True] if 'Duplicado' in df.columns else pd.DataFrame()
            if not dups.empty:
                dups.to_excel(writer, sheet_name='Duplicados', index=False)
            alertas = df[df['Alerta Fecha'] == True] if 'Alerta Fecha' in df.columns else pd.DataFrame()
            if not alertas.empty:
                alertas.to_excel(writer, sheet_name='Alerta Fechas', index=False)
        excel_buf.seek(0)
        (salida / 'resumen_clasificacion.xlsx').write_bytes(excel_buf.getvalue())

        # ZIP en memoria
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in salida.rglob('*'):
                if file.is_file():
                    zf.write(file, file.relative_to(salida))
        zip_buf.seek(0)

        # Guardar en session_state para que no desaparezcan al hacer clic en descarga
        st.session_state['zip_bytes']   = zip_buf.getvalue()
        st.session_state['excel_bytes'] = excel_buf.getvalue()
        st.session_state['resultados']  = resultados
        st.session_state['mi_ruc']      = mi_ruc.strip()
        st.session_state['mes']         = mes_trabajo.strip()

# ============================================================
# RESULTADOS Y DESCARGAS (persisten aunque se haga clic)
# ============================================================
if 'resultados' in st.session_state:
    if st.button("🔄 Nueva clasificación (otra empresa)", use_container_width=True):
        for key in ['zip_bytes', 'excel_bytes', 'resultados', 'mi_ruc', 'mes']:
            st.session_state.pop(key, None)
        # Cambiar la key del uploader fuerza que se limpien los archivos
        st.session_state['uploader_key'] += 1
        st.rerun()

    df = pd.DataFrame(st.session_state['resultados'])
    total = len(df)
    dups_count  = int(df['Duplicado'].sum())    if 'Duplicado'    in df.columns else 0
    alert_count = int(df['Alerta Fecha'].sum()) if 'Alerta Fecha' in df.columns else 0

    st.success(f"✅ {total} archivos clasificados.")
    if dups_count:
        st.warning(f"🔁 {dups_count} duplicado(s) detectado(s).")
    if alert_count:
        st.warning(f"⚠️ {alert_count} factura(s) con fecha fuera del mes {st.session_state['mes']}.")

    st.subheader("Resumen")
    cols_show = [c for c in ['Archivo','Emisor','Moneda','Categoria','Carpeta','Monto Total'] if c in df.columns]
    st.dataframe(df[cols_show], use_container_width=True, hide_index=True)

    st.divider()
    col_a, col_b = st.columns(2)
    with col_a:
        st.download_button(
            label="📥 Descargar ZIP clasificado",
            data=st.session_state['zip_bytes'],
            file_name=f"Facturas_{st.session_state['mi_ruc']}_{st.session_state['mes']}.zip",
            mime="application/zip",
            use_container_width=True
        )
    with col_b:
        st.download_button(
            label="📊 Descargar Excel resumen",
            data=st.session_state['excel_bytes'],
            file_name=f"Resumen_{st.session_state['mi_ruc']}_{st.session_state['mes']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
