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

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
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
- bien: Productos fisicos: insumos dentales (resinas, anestesia, brackets, fresas, guantes, silicona, postes, jeringas, colutorios, enjuague bucal), productos farmaceuticos, agua en bidon, materiales de laboratorio, equipos, mercaderia. Clave: lista productos con cantidad y precio unitario (UND, UNID, CAJA, KG).

DATOS A EXTRAER:
- ruc_emisor: RUC del EMISOR (quien emite el documento). NO es {mi_ruc}. 11 digitos.
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
    tipos = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
             '.webp': 'image/webp', '.bmp': 'image/bmp'}
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

        # Overrides por RUC conocido
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
            result['categoria'] = 'bien'
            result['tipo_documento'] = 'factura'
            result['nombre_emisor'] = result.get('nombre_emisor') or RUCS_BIENES[ruc]

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
st.caption("Soporta JPG, PNG, PDF (incluso escaneados). Separa Soles/Dólares automáticamente.")

st.divider()

# -- Configuracion --
col1, col2 = st.columns(2)
with col1:
    api_key = st.text_input("API Key de Anthropic", type="password", placeholder="sk-ant-...")
with col2:
    col2a, col2b = st.columns(2)
    with col2a:
        mi_ruc = st.text_input("RUC de tu empresa", placeholder="11 digitos")
    with col2b:
        mes_trabajo = st.text_input("Mes (MM)", placeholder="05", max_chars=2)

st.divider()

# -- Subida de archivos --
st.subheader("📤 Sube tus documentos")
archivos_subidos = st.file_uploader(
    "Arrastra o selecciona archivos (JPG, PNG, PDF) o un ZIP con todos",
    type=["jpg", "jpeg", "png", "webp", "bmp", "pdf", "zip"],
    accept_multiple_files=True
)

st.divider()

# -- Boton principal --
if st.button("🚀 Clasificar documentos", type="primary", use_container_width=True):

    # Validaciones
    if not api_key:
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

    client = anthropic.Anthropic(api_key=api_key)

    with tempfile.TemporaryDirectory() as tmp:
        entrada  = Path(tmp) / 'entrada'
        salida   = Path(tmp) / 'Clasificados'
        entrada.mkdir()
        salida.mkdir()

        # Guardar archivos subidos
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

        # Recolectar archivos
        archivos = sorted([f for f in entrada.rglob('*') if f.suffix.lower() in ALL_EXT])

        if not archivos:
            st.error("No se encontraron archivos de imagen o PDF.")
            st.stop()

        st.info(f"Procesando {len(archivos)} archivos...")
        progress = st.progress(0)
        log_area = st.empty()

        vistos = {}
        resultados = []
        log_lines = []

        for i, f in enumerate(archivos):
            progress.progress((i + 1) / len(archivos))
            log_area.text(f"[{i+1}/{len(archivos)}] {f.name[:60]}")

            result = clasificar(f, mi_ruc.strip(), client)

            if result is None:
                dest = salida / 'No Procesados'
                copiar_seguro(f, dest)
                resultados.append({'Archivo': f.name, 'Carpeta': 'No Procesados', 'Estado': 'Error'})
                log_lines.append(f"ERROR | {f.name} -> No Procesados/")
                continue

            # Alerta de fecha
            alerta_fecha = False
            fecha_str = result.get('fecha_emision')
            if fecha_str:
                try:
                    mes_doc = datetime.strptime(fecha_str, '%Y-%m-%d').month
                    alerta_fecha = str(mes_doc).zfill(2) != mes_trabajo.strip()
                except:
                    pass

            # Duplicado
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

            flags = (' | ALERTA FECHA' if alerta_fecha else '') + (' | DUPLICADO' if es_dup else '')
            log_lines.append(f"OK | {f.name} -> {rel}{flags} | {result.get('razon','')}")

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
        log_area.empty()

        # Excel
        df = pd.DataFrame(resultados)
        excel_buf = io.BytesIO()
        with pd.ExcelWriter(excel_buf, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Todos', index=False)
            dups = df[df.get('Duplicado', False) == True] if 'Duplicado' in df.columns else pd.DataFrame()
            if not dups.empty:
                dups.to_excel(writer, sheet_name='Duplicados', index=False)
            alertas = df[df.get('Alerta Fecha', False) == True] if 'Alerta Fecha' in df.columns else pd.DataFrame()
            if not alertas.empty:
                alertas.to_excel(writer, sheet_name='Alerta Fechas', index=False)
        excel_buf.seek(0)
        (salida / 'resumen_clasificacion.xlsx').write_bytes(excel_buf.getvalue())

        # ZIP final
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in salida.rglob('*'):
                if file.is_file():
                    zf.write(file, file.relative_to(salida))
        zip_buf.seek(0)

        # Resultados
        st.success(f"Listo. {len(archivos)} archivos clasificados.")

        dups_count = sum(1 for r in resultados if r.get('Duplicado'))
        alert_count = sum(1 for r in resultados if r.get('Alerta Fecha'))
        if dups_count:
            st.warning(f"{dups_count} duplicado(s) detectado(s).")
        if alert_count:
            st.warning(f"{alert_count} factura(s) con fecha fuera del mes {mes_trabajo}.")

        # Tabla resumen
        st.subheader("Resumen")
        st.dataframe(
            df[['Archivo', 'Emisor', 'Moneda', 'Categoria', 'Carpeta', 'Monto Total']],
            use_container_width=True,
            hide_index=True
        )

        # Descargas
        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                label="📥 Descargar ZIP clasificado",
                data=zip_buf,
                file_name=f"Facturas_Clasificadas_{mi_ruc}_{mes_trabajo}.zip",
                mime="application/zip",
                use_container_width=True
            )
        with col_b:
            excel_buf.seek(0)
            st.download_button(
                label="📊 Descargar Excel resumen",
                data=excel_buf,
                file_name=f"Resumen_{mi_ruc}_{mes_trabajo}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
