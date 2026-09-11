# -*- coding: utf-8 -*-
"""
xml_facturas.py — Núcleo del Clasificador XML → PDF
====================================================
1. parse_xml()      : lee un XML UBL 2.1 de SUNAT (Factura, Boleta, NC, ND) y devuelve un dict limpio
2. clasificar()     : clasificación 100% por reglas (sin API). Opcionalmente afina con Claude (texto)
3. carpeta_destino(): misma estructura de carpetas que el clasificador anterior
4. generar_pdf()    : representación impresa tipo factura (reportlab), legible y uniforme

Se importa desde app_xml.py, pero también sirve por línea de comandos:
    python xml_facturas.py carpeta_con_xml/ salida/
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional

from lxml import etree
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

# ============================================================
# NAMESPACES UBL / SUNAT
# ============================================================
NS = {
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'sac': 'urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1',
    'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2',
}
# Algunos emisores generan xmlns:schemaLocation inválidos; recover=True los tolera.
_PARSER = etree.XMLParser(recover=True, huge_tree=True, remove_blank_text=True)

# ============================================================
# CATÁLOGOS SUNAT
# ============================================================
TIPOS_DOC = {          # Catálogo 01
    '01': ('factura', 'FACTURA ELECTRÓNICA'),
    '03': ('boleta', 'BOLETA DE VENTA ELECTRÓNICA'),
    '07': ('nota_credito', 'NOTA DE CRÉDITO ELECTRÓNICA'),
    '08': ('nota_debito', 'NOTA DE DÉBITO ELECTRÓNICA'),
    '30': ('codigo_30', 'COMPROBANTE DE OPERACIONES - LEY 29972 / SERVICIOS FINANCIEROS'),
    '09': ('guia_remision', 'GUÍA DE REMISIÓN ELECTRÓNICA'),
}
MONEDAS = {'PEN': ('soles', 'S/'), 'USD': ('dolares', 'US$'), 'EUR': ('euros', '€')}
UNIDADES = {          # Catálogo 03 (las más comunes)
    'NIU': 'UNIDAD', 'ZZ': 'SERVICIO', 'BX': 'CAJA', 'PK': 'PAQUETE', 'CS': 'CAJA', 'EA': 'UNIDAD',
    'ST': 'HOJA', 'KGM': 'KILOGRAMO', 'GRM': 'GRAMO', 'LTR': 'LITRO', 'MTR': 'METRO', 'MTK': 'M2',
    'MTQ': 'M3', 'GLL': 'GALÓN', 'DZN': 'DOCENA', 'SET': 'JUEGO', 'BG': 'BOLSA', 'BO': 'BOTELLA',
    'CT': 'CARTÓN', 'PR': 'PAR', 'RO': 'ROLLO', 'TNE': 'TONELADA', 'HUR': 'HORA', 'DAY': 'DÍA',
    'MON': 'MES', 'ANN': 'AÑO', 'NMP': 'PACK', 'MIL': 'MILLAR', 'GLI': 'GALÓN UK',
}
LEYENDAS = {          # Catálogo 52
    '1000': None,   # monto en letras (se muestra aparte)
    '1002': 'TRANSFERENCIA GRATUITA DE UN BIEN Y/O SERVICIO PRESTADO GRATUITAMENTE',
    '2000': 'COMPROBANTE DE PERCEPCIÓN',
    '2001': 'BIENES TRANSFERIDOS EN LA AMAZONÍA REGIÓN SELVA PARA SER CONSUMIDOS EN LA MISMA',
    '2002': 'SERVICIOS PRESTADOS EN LA AMAZONÍA REGIÓN SELVA PARA SER CONSUMIDOS EN LA MISMA',
    '2003': 'CONTRATOS DE CONSTRUCCIÓN EJECUTADOS EN LA AMAZONÍA REGIÓN SELVA',
    '2004': 'AGENCIA DE VIAJE - PAQUETE TURÍSTICO',
    '2005': 'VENTA REALIZADA POR EMISOR ITINERANTE',
    '2006': 'OPERACIÓN SUJETA AL SISTEMA DE PAGO DE OBLIGACIONES TRIBUTARIAS CON EL GOBIERNO CENTRAL',
    '2007': 'OPERACIÓN SUJETA AL IVAP',
    '2008': 'VENTA EXONERADA DEL IGV-ISC-IPM. PROHIBIDA LA VENTA FUERA DE LA ZONA COMERCIAL DE TACNA',
}
BIENES_SERVICIOS_DETRACCION = {   # Catálogo 54 (resumen)
    '001': 'Azúcar y melaza de caña', '003': 'Alcohol etílico', '004': 'Recursos hidrobiológicos',
    '005': 'Maíz amarillo duro', '007': 'Caña de azúcar', '008': 'Madera', '009': 'Arena y piedra',
    '010': 'Residuos, subproductos, desechos', '011': 'Bienes gravados con IGV por renuncia a exoneración',
    '012': 'Intermediación laboral y tercerización', '013': 'Animales vivos', '014': 'Carnes y despojos',
    '015': 'Abonos, cueros y pieles', '016': 'Aceite de pescado', '017': 'Harina de pescado',
    '019': 'Arrendamiento de bienes', '020': 'Mantenimiento y reparación de bienes muebles',
    '021': 'Movimiento de carga', '022': 'Otros servicios empresariales', '023': 'Leche',
    '024': 'Comisión mercantil', '025': 'Fabricación de bienes por encargo', '026': 'Servicio de transporte de personas',
    '027': 'Servicio de transporte de carga', '028': 'Transporte de pasajeros', '030': 'Contratos de construcción',
    '031': 'Oro gravado con IGV', '034': 'Minerales metálicos no auríferos', '035': 'Bienes exonerados del IGV',
    '036': 'Oro y demás minerales metálicos exonerados', '037': 'Demás servicios gravados con IGV',
    '039': 'Minerales no metálicos', '040': 'Bien inmueble gravado con IGV', '041': 'Plomo',
}
MEDIOS_PAGO_DETRACCION = {'001': 'Depósito en cuenta', '002': 'Giro', '003': 'Transferencia de fondos', '004': 'Orden de pago', '005': 'Tarjeta de débito', '006': 'Tarjeta de crédito', '008': 'Efectivo', '011': 'Tarjeta de crédito no bancaria', '999': 'Otros'}

# ============================================================
# LISTAS DE RUCs CONOCIDOS  ←  ÚNICO LUGAR DONDE SE AGREGAN RUCs
# app.py importa estas listas, así que sirven para imágenes, PDF y XML.
# Para agregar uno: copia una línea y cambia RUC y nombre, ej.
#     '20601234567': 'La Lucha Sangucheria',
# ============================================================
RUCS_BANCOS = {
    '20100047218': 'BCP', '20100130204': 'BBVA', '20354766437': 'Interbank', '20100053455': 'Interbank',
    '20522108720': 'Scotiabank', '20258702832': 'BanBif', '20451844326': 'Pichincha', '20100105862': 'Banco de la Nacion',
    '20100043140': 'Scotiabank',
}
RUCS_COMBUSTIBLE = {
    '20258092133': 'Repsol', '20330291017': 'Petroperu', '20543298922': 'Petrogas', '20511995028': 'Terpel Peru',
    '20523621212': 'Lima Expresa (Peaje)',
    # NOTA: '20100128056' se quitó de aquí — ese RUC es de Saga Falabella, no de Primax
    # (estaba mal asignado y hacía que las facturas de Saga se clasificaran como combustible).
    # Ver RUCS_BIENES para el RUC correcto de Saga Falabella.
    # Si tienes el RUC real de Primax, agrégalo aquí, ej: '20xxxxxxxxx': 'Primax',
}
RUCS_RESTAURANTES = {
    '20509828235': 'KFC', '20268571286': 'McDonalds', '20505101688': 'Starbucks', '20388829452': 'Pizza Hut',
    '20424024268': 'Bembos', '20613563700': 'Pardos Chicken', '20563571498': 'Norkys', '20607085600': 'Popeyes',
    '20602122779': 'Little Caesars Pizza', '20600193342': 'EHJ Inversiones (Consumo)', '20100315751': 'Haiti Miraflores',
    '20386489263': 'Inversiones Reixa - Delicass', '20603010524': 'Tere Stabile', '10078403816': 'Zavaleta Zavaleta Rosa Cerolinda (Restaurante)',
    '20127765279': 'Coesti S.A. (Tienda Conveniencia Primax)', '20521370042': 'Eterno Retorno SAC', '20537230399': 'Inversiones SAP - Don Tito',
    '20553689962': 'Taller 109 SRL (Heladeria)',
}
RUCS_SEGUROS = {'20504262242': 'Rimac', '20552083401': 'Pacifico Seguros', '20608644467': 'La Positiva', '20100036773': 'Mapfre'}
RUCS_SERVICIOS_PUBLICOS = {'20331898008': 'Luz del Sur', '20467534026': 'Claro', '20106253251': 'Movistar', '20602235914': 'Entel', '20100167628': 'Sedapal'}
RUCS_BIENES = {'20512002090': 'Mifarma', '20100579228': 'Pareja Lecaros', '20602457029': 'Rigodent / Medical Dental', '20601096022': 'Fresh Life',
               '20100128056': 'Saga Falabella'}
RUCS_SERVICIOS = {'20544547756': 'Despegar.com Peru'}

# Palabras clave para categoría por reglas (se evalúan sobre las descripciones de ítems)
_KW_COMBUSTIBLE = ('GASOHOL', 'DIESEL', 'GASOLINA', 'GLP', 'GNV', 'PETROLEO', 'COMBUSTIBLE', 'PEAJE', 'PREMIUM 9', 'REGULAR 9')
_KW_SEGURO = ('SEGURO', 'POLIZA', 'PÓLIZA', 'SCTR', 'EPS ', 'VIDA LEY', 'PRIMA ')
_KW_RESTAURANTE = ('MENU', 'MENÚ', 'ALMUERZO', 'CENA', 'DESAYUNO', 'LOMO SALTADO', 'CEVICHE', 'POLLO A LA BRASA', 'HAMBURGUESA', 'PIZZA',
                   'CHILCANO', 'PISCO SOUR', 'CAFE ', 'CAFÉ ', 'CAPPUCCINO', 'LATTE', 'SANDWICH', 'SÁNDWICH', 'CONSUMO', 'PLATO', 'ENTRADA', 'POSTRE', 'JUGO ')
_KW_SERVICIO = ('SERVICIO', 'ALQUILER', 'ARRENDAMIENTO', 'MANTENIMIENTO', 'CONSULTORIA', 'CONSULTORÍA', 'ASESORIA', 'ASESORÍA', 'HONORARIO',
                'TRANSPORTE', 'FLETE', 'INTERNET', 'PUBLICIDAD', 'LICENCIA', 'SUSCRIPCION', 'SUSCRIPCIÓN', 'HOSPEDAJE', 'ALOJAMIENTO', 'PASAJE',
                'COMISION', 'COMISIÓN', 'INSTALACION', 'INSTALACIÓN', 'REPARACION', 'REPARACIÓN', 'CAPACITACION', 'CAPACITACIÓN', 'SOPORTE',
                'HOSTING', 'DOMINIO', 'LIMPIEZA', 'SEGURIDAD', 'VIGILANCIA', 'CONTABLE', 'LABORAL', 'LEGAL', 'AUDITORIA', 'AUDITORÍA', 'MEMBRESIA',
                'MEMBRESÍA', 'CUOTA', 'ENVIO', 'ENVÍO', 'COURIER', 'DELIVERY', 'ESTACIONAMIENTO', 'PARQUEO', 'PLAN ', 'TASA')

NO_COMPROBANTE = {'guia_remision', 'nota_pedido', 'recibo_servicio', 'documento_autorizado', 'codigo_30', 'otro', 'recibo_honorarios'}


# ============================================================
# MODELO DE DATOS
# ============================================================
@dataclass
class Linea:
    nro: str = ''
    cantidad: Decimal = Decimal(0)
    unidad: str = ''
    codigo: str = ''
    descripcion: str = ''
    valor_unitario: Decimal = Decimal(0)   # sin IGV
    precio_unitario: Decimal = Decimal(0)  # con IGV (PricingReference 01)
    descuento: Decimal = Decimal(0)
    igv: Decimal = Decimal(0)
    isc: Decimal = Decimal(0)
    valor_venta: Decimal = Decimal(0)      # LineExtensionAmount
    afectacion: str = ''                   # Catálogo 07: 10 gravado, 20 exonerado, 30 inafecto...


@dataclass
class Comprobante:
    archivo: str = ''
    tipo_codigo: str = ''
    tipo_documento: str = 'otro'
    tipo_nombre: str = 'DOCUMENTO'
    tipo_operacion: str = ''
    serie_numero: str = ''
    fecha_emision: str = ''
    fecha_vencimiento: str = ''
    periodo: str = ''
    moneda_codigo: str = 'PEN'
    moneda: str = 'soles'
    simbolo: str = 'S/'
    # emisor
    ruc_emisor: str = ''
    nombre_emisor: str = ''
    nombre_comercial: str = ''
    direccion_emisor: str = ''
    # cliente
    tipo_doc_cliente: str = ''
    doc_cliente: str = ''
    nombre_cliente: str = ''
    direccion_cliente: str = ''
    # referencias
    orden_compra: str = ''
    doc_referencia: str = ''      # NC/ND: comprobante afectado
    motivo_referencia: str = ''
    guias: List[str] = field(default_factory=list)
    observaciones: List[str] = field(default_factory=list)
    # pago
    forma_pago: str = ''
    cuotas: List[dict] = field(default_factory=list)
    # totales
    gravadas: Decimal = Decimal(0)
    exoneradas: Decimal = Decimal(0)
    inafectas: Decimal = Decimal(0)
    gratuitas: Decimal = Decimal(0)
    exportacion: Decimal = Decimal(0)
    descuentos: Decimal = Decimal(0)
    otros_cargos: Decimal = Decimal(0)
    anticipos: Decimal = Decimal(0)
    igv: Decimal = Decimal(0)
    isc: Decimal = Decimal(0)
    icbper: Decimal = Decimal(0)
    otros_tributos: Decimal = Decimal(0)
    redondeo: Decimal = Decimal(0)
    valor_venta: Decimal = Decimal(0)
    importe_total: Decimal = Decimal(0)
    monto_letras: str = ''
    leyendas: List[str] = field(default_factory=list)
    # detracción / percepción / retención
    tiene_detraccion: bool = False
    detraccion_codigo: str = ''
    detraccion_porcentaje: Decimal = Decimal(0)
    detraccion_monto: Decimal = Decimal(0)
    detraccion_cuenta: str = ''
    detraccion_medio: str = ''
    tiene_percepcion: bool = False
    percepcion_monto: Decimal = Decimal(0)
    percepcion_porcentaje: Decimal = Decimal(0)
    percepcion_total: Decimal = Decimal(0)   # total incluida percepción
    tiene_retencion: bool = False
    retencion_monto: Decimal = Decimal(0)
    # ítems
    lineas: List[Linea] = field(default_factory=list)
    # clasificación
    categoria: Optional[str] = None
    razon: str = ''
    tiene_igv: bool = False
    # errores de parseo
    error: str = ''


# ============================================================
# HELPERS DE LECTURA
# ============================================================
def _d(v, default='0') -> Decimal:
    try:
        return Decimal(str(v).strip()) if v not in (None, '') else Decimal(default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _txt(node, xp, default='') -> str:
    if node is None:
        return default
    r = node.xpath(xp, namespaces=NS)
    if not r:
        return default
    v = r[0] if isinstance(r[0], str) else (r[0].text or '')
    return ' '.join(v.split()) if v else default


def _all(node, xp):
    return node.xpath(xp, namespaces=NS) if node is not None else []


def _direccion(party):
    """Arma dirección legible desde cac:RegistrationAddress."""
    addr = _all(party, './/cac:RegistrationAddress')
    if not addr:
        return ''
    a = addr[0]
    partes = [_txt(a, './cac:AddressLine/cbc:Line'), _txt(a, './cbc:StreetName')]
    ubigeo = ' - '.join(p for p in (_txt(a, './cbc:District'), _txt(a, './cbc:CityName'), _txt(a, './cbc:CountrySubentity')) if p)
    partes.append(ubigeo)
    return ' '.join(p for p in partes if p).strip()


def fmt(v: Decimal, dec=2) -> str:
    try:
        q = Decimal(10) ** -dec
        return f"{Decimal(v).quantize(q):,.{dec}f}"
    except Exception:
        return str(v)


def fmt_qty(v: Decimal) -> str:
    """Cantidad: sin decimales innecesarios (30.000 → 30 ; 0.03333 → 0.03333)."""
    try:
        v = Decimal(v).normalize()
        s = f"{v:f}"
        return s if s != '-0' else '0'
    except Exception:
        return str(v)


# ============================================================
# 1. PARSEO UBL
# ============================================================
def parse_xml(path: Path) -> Comprobante:
    c = Comprobante(archivo=Path(path).name)
    try:
        root = etree.parse(str(path), _PARSER).getroot()
    except Exception as ex:
        c.error = f'XML ilegible: {ex}'
        return c
    if root is None:
        c.error = 'XML vacío o ilegible'
        return c

    raiz = etree.QName(root).localname
    if raiz not in ('Invoice', 'CreditNote', 'DebitNote'):
        c.error = f'No es un comprobante UBL (raíz {raiz}). Puede ser CDR, Retención, Percepción o Guía.'
        return c

    # --- Tipo / serie / fechas / moneda ---
    if raiz == 'Invoice':
        c.tipo_codigo = _txt(root, './cbc:InvoiceTypeCode', '01')
        c.tipo_operacion = _txt(root, './cbc:InvoiceTypeCode/@listID')
    elif raiz == 'CreditNote':
        c.tipo_codigo = '07'
    else:
        c.tipo_codigo = '08'
    c.tipo_documento, c.tipo_nombre = TIPOS_DOC.get(c.tipo_codigo, ('otro', f'DOCUMENTO TIPO {c.tipo_codigo}'))
    c.serie_numero = _txt(root, './cbc:ID')
    c.fecha_emision = _txt(root, './cbc:IssueDate')
    c.fecha_vencimiento = _txt(root, './cbc:DueDate')
    ini, fin = _txt(root, './cac:InvoicePeriod/cbc:StartDate'), _txt(root, './cac:InvoicePeriod/cbc:EndDate')
    if ini or fin:
        c.periodo = f'{_fecha(ini)} al {_fecha(fin)}'
    c.moneda_codigo = _txt(root, './cbc:DocumentCurrencyCode', 'PEN').upper()
    c.moneda, c.simbolo = MONEDAS.get(c.moneda_codigo, ('desconocido', c.moneda_codigo + ' '))

    # --- Emisor ---
    sup = _all(root, './cac:AccountingSupplierParty')
    sup = sup[0] if sup else None
    c.ruc_emisor = _txt(sup, './cac:Party/cac:PartyIdentification/cbc:ID') or _txt(sup, './cbc:CustomerAssignedAccountID')
    c.nombre_emisor = _txt(sup, './cac:Party/cac:PartyLegalEntity/cbc:RegistrationName') or _txt(sup, './cac:Party/cac:PartyName/cbc:Name')
    c.nombre_comercial = _txt(sup, './cac:Party/cac:PartyName/cbc:Name')
    if c.nombre_comercial.upper() == c.nombre_emisor.upper():
        c.nombre_comercial = ''
    c.direccion_emisor = _direccion(sup)

    # --- Cliente ---
    cus = _all(root, './cac:AccountingCustomerParty')
    cus = cus[0] if cus else None
    c.doc_cliente = _txt(cus, './cac:Party/cac:PartyIdentification/cbc:ID') or _txt(cus, './cbc:CustomerAssignedAccountID')
    c.tipo_doc_cliente = {'6': 'RUC', '1': 'DNI', '4': 'C.E.', '7': 'PASAPORTE', '0': 'DOC.'}.get(
        _txt(cus, './cac:Party/cac:PartyIdentification/cbc:ID/@schemeID'), 'RUC' if len(c.doc_cliente) == 11 else 'DOC.')
    c.nombre_cliente = _txt(cus, './cac:Party/cac:PartyLegalEntity/cbc:RegistrationName') or _txt(cus, './cac:Party/cac:PartyName/cbc:Name')
    c.direccion_cliente = _direccion(cus)

    # --- Referencias ---
    c.orden_compra = _txt(root, './cac:OrderReference/cbc:ID')
    for g in _all(root, './cac:DespatchDocumentReference/cbc:ID'):
        if g.text:
            c.guias.append(g.text.strip())
    if raiz in ('CreditNote', 'DebitNote'):
        c.doc_referencia = _txt(root, './cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID')
        c.motivo_referencia = _txt(root, './cac:DiscrepancyResponse/cbc:Description')
        if not c.tipo_operacion:
            c.tipo_operacion = _txt(root, './cac:DiscrepancyResponse/cbc:ResponseCode')

    # --- Notas / leyendas ---
    for n in _all(root, './cbc:Note'):
        code = n.get('languageLocaleID')
        text = ' '.join((n.text or '').split())
        if not text:
            continue
        if code == '1000':
            c.monto_letras = text
        elif code in LEYENDAS and code != '1000':
            c.leyendas.append(text)
        elif code:
            c.leyendas.append(text)
        else:
            c.observaciones.append(text)
    if not c.monto_letras and c.tipo_codigo == '01':
        pass  # algunos emisores no la incluyen; el PDF la omite

    # --- Forma de pago / cuotas / detracción / percepción ---
    for pt in _all(root, './cac:PaymentTerms'):
        pid = _txt(pt, './cbc:ID').upper()
        means = _txt(pt, './cbc:PaymentMeansID')
        if pid == 'FORMAPAGO':
            if means.upper().startswith('CUOTA'):
                c.cuotas.append({'cuota': means, 'monto': _d(_txt(pt, './cbc:Amount')), 'vence': _txt(pt, './cbc:PaymentDueDate')})
            elif means:
                c.forma_pago = means
        elif pid == 'DETRACCION':
            c.tiene_detraccion = True
            c.detraccion_codigo = means
            c.detraccion_porcentaje = _d(_txt(pt, './cbc:PaymentPercent'))
            c.detraccion_monto = _d(_txt(pt, './cbc:Amount'))
        elif pid == 'PERCEPCION':
            c.tiene_percepcion = True
            c.percepcion_total = _d(_txt(pt, './cbc:Amount'))
        elif pid == 'RETENCION':
            c.tiene_retencion = True
            c.retencion_monto = _d(_txt(pt, './cbc:Amount'))
    for pm in _all(root, './cac:PaymentMeans'):
        cuenta = _txt(pm, './cac:PayeeFinancialAccount/cbc:ID')
        if cuenta:
            c.detraccion_cuenta = cuenta
            c.detraccion_medio = _txt(pm, './cbc:PaymentMeansCode')
    # Percepción / retención declaradas como cargo global (código 51/52/53 percepción, 62 retención)
    for ac in _all(root, './cac:AllowanceCharge'):
        es_cargo = _txt(ac, './cbc:ChargeIndicator').lower() == 'true'
        code = _txt(ac, './cbc:AllowanceChargeReasonCode')
        monto = _d(_txt(ac, './cbc:Amount'))
        factor = _d(_txt(ac, './cbc:MultiplierFactorNumeric'))
        if es_cargo and code in ('51', '52', '53'):
            c.tiene_percepcion = True
            c.percepcion_monto = monto
            c.percepcion_porcentaje = factor * 100 if factor < 1 else factor
        elif not es_cargo and code == '62':
            c.tiene_retencion = True
            c.retencion_monto = monto
        elif es_cargo and code in ('45', '46', '47', '48', '49', '50'):
            c.otros_cargos += monto
        elif not es_cargo and code in ('02', '03', '04', '05', '06'):
            c.descuentos += monto
    if c.tiene_percepcion and not c.percepcion_monto and c.percepcion_total:
        pass  # se calcula abajo cuando conozcamos el total
    # Leyenda 2006 → detracción aunque no haya PaymentTerms
    if any('OBLIGACIONES TRIBUTARIAS' in l.upper() or 'DETRACC' in l.upper() for l in c.leyendas):
        c.tiene_detraccion = True
    if any('PERCEPCI' in l.upper() for l in c.leyendas) or c.tipo_operacion == '2001':
        c.tiene_percepcion = True

    # --- Tributos globales ---
    for ts in _all(root, './cac:TaxTotal/cac:TaxSubtotal'):
        nombre = _txt(ts, './cac:TaxCategory/cac:TaxScheme/cbc:Name').upper()
        tid = _txt(ts, './cac:TaxCategory/cac:TaxScheme/cbc:ID')
        monto = _d(_txt(ts, './cbc:TaxAmount'))
        base = _d(_txt(ts, './cbc:TaxableAmount'))
        if nombre == 'IGV' or tid == '1000':
            c.igv += monto
            if not c.gravadas:
                c.gravadas = base
        elif nombre == 'ISC' or tid == '2000':
            c.isc += monto
        elif nombre in ('ICBPER',) or tid == '7152':
            c.icbper += monto
        elif nombre == 'EXO' or tid == '9997':
            c.exoneradas += base
        elif nombre == 'INA' or tid == '9998':
            c.inafectas += base
        elif nombre == 'GRA' or tid == '9996':
            c.gratuitas += base
        elif nombre == 'EXP' or tid == '9995':
            c.exportacion += base
        elif nombre == 'IVAP' or tid == '1016':
            c.otros_tributos += monto
        else:
            c.otros_tributos += monto

    # --- Totales ---
    lmt = _all(root, './cac:LegalMonetaryTotal') or _all(root, './cac:RequestedMonetaryTotal')
    lmt = lmt[0] if lmt else None
    c.valor_venta = _d(_txt(lmt, './cbc:LineExtensionAmount')) or _d(_txt(lmt, './cbc:TaxExclusiveAmount'))
    c.importe_total = _d(_txt(lmt, './cbc:PayableAmount')) or _d(_txt(lmt, './cbc:TaxInclusiveAmount'))
    c.anticipos = _d(_txt(lmt, './cbc:PrepaidAmount'))
    c.redondeo = _d(_txt(lmt, './cbc:PayableRoundingAmount'))
    if not c.descuentos:
        c.descuentos = _d(_txt(lmt, './cbc:AllowanceTotalAmount'))
    if not c.otros_cargos:
        c.otros_cargos = _d(_txt(lmt, './cbc:ChargeTotalAmount'))
    if c.tiene_percepcion:
        if not c.percepcion_total and c.percepcion_monto:
            c.percepcion_total = c.importe_total + c.percepcion_monto
        if not c.percepcion_monto and c.percepcion_total:
            c.percepcion_monto = c.percepcion_total - c.importe_total
    c.tiene_igv = c.igv > 0

    # --- Líneas (InvoiceLine / CreditNoteLine / DebitNoteLine) ---
    line_tag = {'Invoice': 'cac:InvoiceLine', 'CreditNote': 'cac:CreditNoteLine', 'DebitNote': 'cac:DebitNoteLine'}[raiz]
    qty_tag = {'Invoice': 'cbc:InvoicedQuantity', 'CreditNote': 'cbc:CreditedQuantity', 'DebitNote': 'cbc:DebitedQuantity'}[raiz]
    for ln in _all(root, f'./{line_tag}'):
        sub = _all(ln, './cac:SubInvoiceLine')
        if sub:   # Tipo 30 (bancos): las sublíneas son el detalle real
            for s in sub:
                L = Linea(nro=_txt(s, './cbc:ID'), cantidad=Decimal(1), unidad='',
                          codigo=_txt(s, './cac:Item/cac:SellersItemIdentification/cbc:ID'),
                          descripcion=_txt(s, './cac:Item/cbc:Description') or _txt(s, './cac:OriginatorParty/cac:PartyLegalEntity/cbc:RegistrationName'),
                          valor_venta=_d(_txt(s, './cbc:LineExtensionAmount')),
                          igv=_d(_txt(s, './cac:TaxTotal/cbc:TaxAmount')))
                L.valor_unitario = L.valor_venta
                L.precio_unitario = _d(_txt(s, './cac:ItemPriceExtension/cbc:Amount')) or L.valor_venta
                c.lineas.append(L)
            continue
        L = Linea(nro=_txt(ln, './cbc:ID'))
        L.cantidad = _d(_txt(ln, f'./{qty_tag}'), '1')
        uc = _txt(ln, f'./{qty_tag}/@unitCode')
        L.unidad = UNIDADES.get(uc, uc)
        L.codigo = _txt(ln, './cac:Item/cac:SellersItemIdentification/cbc:ID')
        descs = [' '.join((d.text or '').split()) for d in _all(ln, './cac:Item/cbc:Description')]
        L.descripcion = ' / '.join(d for d in descs if d)
        # Algunos emisores meten metadatos con @@ en la descripción (ej. "PRODUCTO@@UND@@0.00@@57.21" o "PRODUCTO@#@ 12- @#@29.36")
        L.descripcion = re.split(r'@#@|@@', L.descripcion)[0].strip()
        L.valor_venta = _d(_txt(ln, './cbc:LineExtensionAmount'))
        L.valor_unitario = _d(_txt(ln, './cac:Price/cbc:PriceAmount'))
        for acp in _all(ln, './cac:PricingReference/cac:AlternativeConditionPrice'):
            if _txt(acp, './cbc:PriceTypeCode') == '01':
                L.precio_unitario = _d(_txt(acp, './cbc:PriceAmount'))
        for ac in _all(ln, './cac:AllowanceCharge'):
            if _txt(ac, './cbc:ChargeIndicator').lower() == 'false':
                L.descuento += _d(_txt(ac, './cbc:Amount'))
        for ts in _all(ln, './cac:TaxTotal/cac:TaxSubtotal'):
            nombre = _txt(ts, './cac:TaxCategory/cac:TaxScheme/cbc:Name').upper()
            tid = _txt(ts, './cac:TaxCategory/cac:TaxScheme/cbc:ID')
            monto = _d(_txt(ts, './cbc:TaxAmount'))
            if nombre == 'IGV' or tid == '1000':
                L.igv += monto
                L.afectacion = _txt(ts, './cac:TaxCategory/cbc:TaxExemptionReasonCode')
            elif nombre == 'ISC' or tid == '2000':
                L.isc += monto
            elif not L.afectacion:
                L.afectacion = _txt(ts, './cac:TaxCategory/cbc:TaxExemptionReasonCode')
        if not L.precio_unitario and L.cantidad:
            L.precio_unitario = ((L.valor_venta + L.igv + L.isc) / L.cantidad) if L.cantidad else Decimal(0)
        c.lineas.append(L)

    if not c.gravadas and c.igv:
        c.gravadas = c.valor_venta
    return c


# ============================================================
# 2. CLASIFICACIÓN POR REGLAS (sin API)
# ============================================================
def _texto_items(c: Comprobante) -> str:
    return ' | '.join(l.descripcion for l in c.lineas).upper()


def clasificar(c: Comprobante) -> Comprobante:
    """Asigna c.categoria y c.razon. Determinista: mismos datos → misma carpeta."""
    ruc = c.ruc_emisor
    txt = _texto_items(c)

    if c.tipo_documento in ('nota_credito', 'nota_debito'):
        c.categoria = None
        c.razon = f'{c.tipo_nombre.title()} — va a su carpeta propia'
        return c

    # 1) RUC conocido (más confiable que cualquier heurística)
    if ruc in RUCS_BANCOS:
        c.categoria, c.razon = 'banco', f'RUC de banco conocido ({RUCS_BANCOS[ruc]})'
        if c.tipo_documento == 'codigo_30':
            c.tipo_documento = 'factura'   # comisiones bancarias tipo 30 → carpeta Bancos (igual que antes)
        return c
    if ruc in RUCS_SERVICIOS_PUBLICOS:
        c.tipo_documento, c.categoria = 'recibo_servicio', None
        c.razon = f'RUC de servicio público ({RUCS_SERVICIOS_PUBLICOS[ruc]})'
        return c
    if ruc in RUCS_COMBUSTIBLE:
        c.categoria, c.razon = 'combustible_peaje', f'RUC conocido ({RUCS_COMBUSTIBLE[ruc]})'
        return c
    if ruc in RUCS_RESTAURANTES:
        c.categoria, c.razon = 'restaurante_consumo', f'RUC conocido ({RUCS_RESTAURANTES[ruc]})'
        return c
    if ruc in RUCS_SEGUROS:
        c.categoria, c.razon = 'seguro', f'RUC conocido ({RUCS_SEGUROS[ruc]})'
        return c
    if ruc in RUCS_SERVICIOS:
        c.categoria, c.razon = 'servicio', f'RUC conocido ({RUCS_SERVICIOS[ruc]})'
        return c
    if ruc in RUCS_BIENES:
        c.categoria, c.razon = 'bien', f'RUC conocido ({RUCS_BIENES[ruc]})'
        return c

    if c.tipo_documento in NO_COMPROBANTE:
        c.categoria = None
        c.razon = 'No es comprobante de pago (factura/boleta)'
        return c

    # 2) Detracción manda sobre todo lo demás
    if c.tiene_detraccion:
        c.categoria = 'servicio_detraccion'
        c.razon = 'El XML declara detracción (PaymentTerms/Detraccion o leyenda 2006)'
        return c

    # 3) Heurísticas por descripción / unidad
    if any(k in txt for k in _KW_COMBUSTIBLE):
        c.categoria, c.razon = 'combustible_peaje', 'Descripción de combustible o peaje'
        return c
    if any(k in txt for k in _KW_SEGURO):
        c.categoria, c.razon = 'seguro', 'Descripción de seguro/póliza'
        return c
    unidades = {l.unidad for l in c.lineas}
    todo_servicio = bool(c.lineas) and unidades <= {'SERVICIO', ''}
    hits_serv = sum(1 for k in _KW_SERVICIO if k in txt)
    if todo_servicio or hits_serv >= 1 and unidades <= {'SERVICIO', '', 'UNIDAD'} and len(c.lineas) <= 3:
        c.categoria = 'servicio'
        c.razon = 'Ítems con unidad SERVICIO (ZZ) o descripción de servicio'
        return c
    if any(k in txt for k in _KW_RESTAURANTE) and len(c.lineas) <= 8:
        c.categoria, c.razon = 'restaurante_consumo', 'Descripción de platos/bebidas preparadas'
        return c
    c.categoria = 'bien'
    c.razon = 'Lista de productos con cantidad y precio unitario; sin detracción'
    if c.tiene_percepcion:
        c.razon += ' (con percepción)'
    return c


def afinar_con_claude(c: Comprobante, client, mi_ruc: str = '', model: str = 'claude-haiku-4-5-20251001') -> Comprobante:
    """Opcional: pide a Claude (solo texto, barato) que confirme la categoría en casos ambiguos
    (RUC no conocido y categoría 'bien' / 'servicio' / 'restaurante_consumo')."""
    if c.categoria not in ('bien', 'servicio', 'restaurante_consumo') or c.tiene_detraccion:
        return c
    if c.ruc_emisor in {**RUCS_BANCOS, **RUCS_COMBUSTIBLE, **RUCS_RESTAURANTES, **RUCS_SEGUROS, **RUCS_BIENES, **RUCS_SERVICIOS}:
        return c
    items = '\n'.join(f'- {fmt_qty(l.cantidad)} {l.unidad} {l.descripcion} ({c.simbolo} {fmt(l.valor_venta)})' for l in c.lineas[:30])
    prompt = f"""Eres un contador peruano. Clasifica esta factura de compra en UNA categoría:
- restaurante_consumo: consumo en restaurante/cafetería/bar/heladería/tienda de conveniencia (platos, bebidas preparadas, menú).
- bien: productos físicos para abastecer/revender/usar (abarrotes al por mayor, insumos, materiales, equipos).
- servicio: servicios sin detracción (alquiler, internet, consultoría, transporte, publicidad, mantenimiento, etc.).
- combustible_peaje, seguro: solo si es evidente.
Emisor: {c.nombre_emisor} (RUC {c.ruc_emisor}). Cliente RUC: {c.doc_cliente or mi_ruc}. Total: {c.simbolo} {fmt(c.importe_total)}.
Ítems:
{items}
Clasificación preliminar por reglas: {c.categoria}.
Responde SOLO JSON: {{"categoria": "...", "razon": "una línea"}}"""
    try:
        msg = client.messages.create(model=model, max_tokens=150, messages=[{'role': 'user', 'content': prompt}])
        raw = msg.content[0].text
        s, e = raw.find('{'), raw.rfind('}') + 1
        data = json.loads(raw[s:e])
        cat = data.get('categoria')
        if cat in ('restaurante_consumo', 'bien', 'servicio', 'combustible_peaje', 'seguro'):
            if cat != c.categoria:
                c.razon = f"IA: {data.get('razon', '')} (reglas decían {c.categoria})"
            c.categoria = cat
    except Exception as ex:  # nunca romper el flujo por la IA
        c.razon += f' [IA no disponible: {ex}]'
    return c


# ============================================================
# 3. CARPETA DESTINO (misma estructura que la versión anterior)
# ============================================================
def carpeta_destino(c: Comprobante, base: Path) -> Path:
    base = Path(base)
    cur = {'soles': base / 'Soles', 'dolares': base / 'Dolares'}.get(c.moneda, base / 'Moneda No Detectada')
    tipo, cat = c.tipo_documento, c.categoria
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
    if cat == 'servicio_detraccion' or c.tiene_detraccion:
        return cur / 'Servicios' / 'Con Detraccion'
    if cat == 'servicio':
        return cur / 'Servicios' / 'Sin Detraccion'
    return cur / 'Bienes'


def nombre_pdf(c: Comprobante) -> str:
    serie = re.sub(r'[^A-Za-z0-9\-]', '', c.serie_numero) or 'SIN-SERIE'
    return f"{c.ruc_emisor or 'SINRUC'}-{c.tipo_codigo or 'XX'}-{serie}.pdf"


# ============================================================
# 4. PDF TIPO FACTURA
# ============================================================
_styles = getSampleStyleSheet()
S_BASE = ParagraphStyle('base', parent=_styles['Normal'], fontName='Helvetica', fontSize=8, leading=10)
S_SMALL = ParagraphStyle('small', parent=S_BASE, fontSize=7, leading=8.5, textColor=colors.HexColor('#444444'))
S_BOLD = ParagraphStyle('bold', parent=S_BASE, fontName='Helvetica-Bold')
S_EMISOR = ParagraphStyle('emisor', parent=S_BASE, fontName='Helvetica-Bold', fontSize=11, leading=13)
S_RUC = ParagraphStyle('ruc', parent=S_BASE, fontName='Helvetica-Bold', fontSize=10, leading=12, alignment=TA_CENTER)
S_TIPO = ParagraphStyle('tipo', parent=S_BASE, fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=TA_CENTER)
S_SERIE = ParagraphStyle('serie', parent=S_BASE, fontName='Helvetica-Bold', fontSize=12, leading=14, alignment=TA_CENTER)
S_RIGHT = ParagraphStyle('right', parent=S_BASE, alignment=TA_RIGHT)
S_RIGHT_B = ParagraphStyle('rightb', parent=S_BOLD, alignment=TA_RIGHT)
S_CELL = ParagraphStyle('cell', parent=S_BASE, fontSize=7.5, leading=9)
S_CELL_R = ParagraphStyle('cellr', parent=S_CELL, alignment=TA_RIGHT)
S_HEAD = ParagraphStyle('head', parent=S_BASE, fontName='Helvetica-Bold', fontSize=7.5, leading=9, alignment=TA_CENTER, textColor=colors.white)
S_TAG = ParagraphStyle('tag', parent=S_BASE, fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#7a1f1f'))

_GRIS = colors.HexColor('#f2f2f2')
_AZUL = colors.HexColor('#1f3b5c')
_BORDE = colors.HexColor('#999999')


def _esc(s: str) -> str:
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _p(txt, style=S_BASE):
    return Paragraph(_esc(str(txt)), style)


def _box(data, col_widths, style_cmds=None, padding=3):
    t = Table(data, colWidths=col_widths)
    cmds = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), padding),
            ('RIGHTPADDING', (0, 0), (-1, -1), padding), ('TOPPADDING', (0, 0), (-1, -1), padding),
            ('BOTTOMPADDING', (0, 0), (-1, -1), padding)]
    if style_cmds:
        cmds += style_cmds
    t.setStyle(TableStyle(cmds))
    return t


def generar_pdf(c: Comprobante, destino: Path, carpeta_rel: str = '', mi_ruc: str = '') -> Path:
    """Genera la representación impresa del comprobante. Devuelve la ruta."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    W, H = A4
    margen = 12 * mm
    ancho = W - 2 * margen
    doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=margen, rightMargin=margen,
                            topMargin=10 * mm, bottomMargin=12 * mm,
                            title=f'{c.tipo_nombre} {c.serie_numero}', author=c.nombre_emisor,
                            subject=f'Representación interna generada desde XML — {c.archivo}')
    story = []

    # ---------- Cabecera: emisor | recuadro RUC ----------
    emisor_lineas = [_p(c.nombre_emisor, S_EMISOR)]
    if c.nombre_comercial:
        emisor_lineas.append(_p(c.nombre_comercial, S_BOLD))
    if c.direccion_emisor:
        emisor_lineas.append(_p(c.direccion_emisor, S_SMALL))
    recuadro = _box([[_p(f'RUC: {c.ruc_emisor}', S_RUC)], [_p(c.tipo_nombre, S_TIPO)], [_p(c.serie_numero, S_SERIE)]],
                    [62 * mm], [('BOX', (0, 0), (-1, -1), 1.2, _AZUL), ('LINEBELOW', (0, 0), (-1, 0), 0.5, _AZUL),
                                ('LINEBELOW', (0, 1), (-1, 1), 0.5, _AZUL), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')], padding=5)
    story.append(_box([[emisor_lineas, recuadro]], [ancho - 64 * mm, 64 * mm], [('VALIGN', (0, 0), (-1, -1), 'TOP')], padding=0))
    story.append(Spacer(1, 4 * mm))

    # ---------- Datos del comprobante / cliente ----------
    fecha_fmt = _fecha(c.fecha_emision)
    izq = [[_p('Fecha de emisión:', S_BOLD), _p(fecha_fmt)]]
    if c.fecha_vencimiento and c.fecha_vencimiento != c.fecha_emision:
        izq.append([_p('Fecha de vencimiento:', S_BOLD), _p(_fecha(c.fecha_vencimiento))])
    izq += [[_p(f'{c.tipo_doc_cliente} cliente:', S_BOLD), _p(c.doc_cliente)],
            [_p('Cliente:', S_BOLD), _p(c.nombre_cliente)]]
    if c.direccion_cliente:
        izq.append([_p('Dirección:', S_BOLD), _p(c.direccion_cliente, S_SMALL)])
    der = [[_p('Moneda:', S_BOLD), _p({'soles': 'SOLES', 'dolares': 'DÓLARES AMERICANOS', 'euros': 'EUROS'}.get(c.moneda, c.moneda_codigo))]]
    if c.forma_pago:
        der.append([_p('Forma de pago:', S_BOLD), _p(c.forma_pago.upper())])
    if c.orden_compra:
        der.append([_p('Orden de compra:', S_BOLD), _p(c.orden_compra)])
    if c.guias:
        der.append([_p('Guía(s) de remisión:', S_BOLD), _p(', '.join(c.guias))])
    if c.doc_referencia:
        der.append([_p('Documento afectado:', S_BOLD), _p(c.doc_referencia)])
    if c.motivo_referencia:
        der.append([_p('Motivo:', S_BOLD), _p(c.motivo_referencia)])
    if c.periodo:
        der.append([_p('Período:', S_BOLD), _p(c.periodo)])
    if c.tipo_operacion and len(c.tipo_operacion) == 4 and c.tipo_documento in ('factura', 'boleta'):
        der.append([_p('Tipo de operación:', S_BOLD), _p(_tipo_operacion(c.tipo_operacion))])
    col_izq = ancho * 0.55
    col_der = ancho - col_izq
    t_izq = _box(izq, [30 * mm, col_izq - 30 * mm], padding=1.5)
    t_der = _box(der, [32 * mm, col_der - 32 * mm], padding=1.5)
    story.append(_box([[t_izq, t_der]], [col_izq, col_der], [('BOX', (0, 0), (-1, -1), 0.6, _BORDE), ('BACKGROUND', (0, 0), (-1, -1), _GRIS)], padding=3))
    story.append(Spacer(1, 3 * mm))

    # ---------- Tabla de ítems ----------
    es_banco = c.tipo_codigo == '30'
    if es_banco:
        head = ['Nro', 'Código', 'Descripción', 'Importe']
        cw = [10 * mm, 22 * mm, ancho - 10 * mm - 22 * mm - 28 * mm, 28 * mm]
        rows = [[_p(l.nro, S_CELL), _p(l.codigo, S_CELL), _p(l.descripcion, S_CELL), _p(fmt(l.valor_venta), S_CELL_R)] for l in c.lineas]
    else:
        head = ['Nro', 'Cant.', 'Unidad', 'Código', 'Descripción', 'V. Unitario', 'P. Unitario', 'Dscto.', 'IGV', 'Valor Venta']
        cw = [8 * mm, 13 * mm, 16 * mm, 17 * mm, None, 17 * mm, 17 * mm, 13 * mm, 15 * mm, 19 * mm]
        cw[4] = ancho - sum(w for w in cw if w)
        rows = []
        for l in c.lineas:
            rows.append([_p(l.nro, S_CELL), _p(fmt_qty(l.cantidad), S_CELL_R), _p(l.unidad, S_CELL), _p(l.codigo, S_CELL),
                         _p(l.descripcion + (f' (ISC {fmt(l.isc)})' if l.isc else '') + _afect(l.afectacion), S_CELL),
                         _p(fmt(l.valor_unitario, 4 if l.valor_unitario and l.valor_unitario < 1 else 2), S_CELL_R),
                         _p(fmt(l.precio_unitario), S_CELL_R), _p(fmt(l.descuento) if l.descuento else '', S_CELL_R),
                         _p(fmt(l.igv), S_CELL_R), _p(fmt(l.valor_venta), S_CELL_R)])
    if not rows:
        rows = [[_p('— sin detalle de ítems en el XML —', S_CELL)] + [''] * (len(head) - 1)]
    items = Table([[_p(h, S_HEAD) for h in head]] + rows, colWidths=cw, repeatRows=1)
    cmds = [('BACKGROUND', (0, 0), (-1, 0), _AZUL), ('GRID', (0, 0), (-1, -1), 0.4, _BORDE), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 2.5), ('RIGHTPADDING', (0, 0), (-1, -1), 2.5), ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')])]
    items.setStyle(TableStyle(cmds))
    story.append(items)
    story.append(Spacer(1, 3 * mm))

    # ---------- Totales + monto en letras / leyendas ----------
    tot = []
    def fila(label, valor, bold=False, siempre=False):
        if siempre or (valor and valor != 0):
            tot.append([_p(label, S_RIGHT_B if bold else S_RIGHT), _p(f'{c.simbolo} {fmt(valor)}', S_RIGHT_B if bold else S_RIGHT)])
    fila('Op. Gravadas', c.gravadas, siempre=c.igv > 0)
    fila('Op. Exoneradas', c.exoneradas)
    fila('Op. Inafectas', c.inafectas)
    fila('Op. Gratuitas', c.gratuitas)
    fila('Exportación', c.exportacion)
    fila('Anticipos', c.anticipos)
    fila('Descuentos', c.descuentos)
    if not c.igv and not c.exoneradas and not c.inafectas:
        fila('Valor de venta', c.valor_venta, siempre=True)
    fila('ISC', c.isc)
    fila('IGV (18%)' if c.igv else 'IGV', c.igv, siempre=True)
    fila('ICBPER', c.icbper)
    fila('Otros cargos', c.otros_cargos)
    fila('Otros tributos', c.otros_tributos)
    fila('Redondeo', c.redondeo)
    fila('IMPORTE TOTAL', c.importe_total, bold=True, siempre=True)
    if c.tiene_percepcion and c.percepcion_monto:
        fila(f'Percepción{(" " + fmt(c.percepcion_porcentaje, 0) + "%") if c.percepcion_porcentaje else ""}', c.percepcion_monto)
        fila('TOTAL INCL. PERCEPCIÓN', c.percepcion_total, bold=True)
    t_tot = _box(tot, [42 * mm, 30 * mm], [('LINEABOVE', (0, -1 - (2 if c.tiene_percepcion and c.percepcion_monto else 0)), (-1, -1 - (2 if c.tiene_percepcion and c.percepcion_monto else 0)), 0.8, _AZUL),
                                          ('BOX', (0, 0), (-1, -1), 0.6, _BORDE), ('BACKGROUND', (0, 0), (-1, -1), _GRIS)], padding=2)

    izq_txt = []
    if c.monto_letras:
        izq_txt.append(_p(f'SON: {c.monto_letras.lstrip("*")}', S_BOLD))
    for ley in c.leyendas:
        izq_txt.append(_p(ley, S_SMALL))
    for ob in c.observaciones:
        izq_txt.append(_p(f'Observación: {ob}', S_SMALL))
    if c.cuotas:
        izq_txt.append(Spacer(1, 2 * mm))
        izq_txt.append(_p('Cuotas de pago:', S_BOLD))
        for q in c.cuotas:
            izq_txt.append(_p(f'{q["cuota"]}: {c.simbolo} {fmt(q["monto"])}' + (f'  vence {_fecha(q["vence"])}' if q['vence'] else ''), S_SMALL))
    if not izq_txt:
        izq_txt = [Spacer(1, 1)]
    story.append(_box([[izq_txt, t_tot]], [ancho - 74 * mm, 74 * mm], padding=1))
    story.append(Spacer(1, 3 * mm))

    # ---------- Detracción / Percepción / Retención ----------
    if c.tiene_detraccion:
        filas = [[_p('INFORMACIÓN DE LA DETRACCIÓN', S_TAG), '']]
        filas.append([_p('Leyenda:', S_BOLD), _p(LEYENDAS['2006'].capitalize(), S_SMALL)])
        if c.detraccion_codigo:
            filas.append([_p('Bien o servicio:', S_BOLD), _p(f'{c.detraccion_codigo} {BIENES_SERVICIOS_DETRACCION.get(c.detraccion_codigo, "")}')])
        if c.detraccion_medio:
            filas.append([_p('Medio de pago:', S_BOLD), _p(f'{c.detraccion_medio} {MEDIOS_PAGO_DETRACCION.get(c.detraccion_medio, "")}')])
        if c.detraccion_cuenta:
            filas.append([_p('Cta. Banco de la Nación:', S_BOLD), _p(c.detraccion_cuenta)])
        if c.detraccion_porcentaje or c.detraccion_monto:
            filas.append([_p('Porcentaje / Monto:', S_BOLD), _p(f'{fmt(c.detraccion_porcentaje)} %   —   S/ {fmt(c.detraccion_monto)}', S_BOLD)])
        story.append(KeepTogether(_box(filas, [40 * mm, ancho - 40 * mm], [('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#7a1f1f')), ('SPAN', (0, 0), (-1, 0)),
                                                                          ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fbeaea'))], padding=2.5)))
        story.append(Spacer(1, 2 * mm))
    if c.tiene_percepcion and c.percepcion_monto:
        story.append(_box([[_p('COMPROBANTE DE PERCEPCIÓN', S_TAG),
                            _p(f'Percepción {c.simbolo} {fmt(c.percepcion_monto)}  ·  Total a pagar incluida percepción {c.simbolo} {fmt(c.percepcion_total)}', S_BOLD)]],
                          [45 * mm, ancho - 45 * mm], [('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#7a1f1f')), ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fbeaea'))], padding=3))
        story.append(Spacer(1, 2 * mm))
    if c.tiene_retencion and c.retencion_monto:
        story.append(_box([[_p('RETENCIÓN', S_TAG), _p(f'{c.simbolo} {fmt(c.retencion_monto)}', S_BOLD)]], [45 * mm, ancho - 45 * mm],
                          [('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#7a1f1f'))], padding=3))
        story.append(Spacer(1, 2 * mm))

    # ---------- Pie: clasificación interna ----------
    cat_txt = {'banco': 'Bancos', 'combustible_peaje': 'Combustible y Peajes', 'restaurante_consumo': 'Restaurantes y Consumos', 'seguro': 'Seguros',
               'servicio_detraccion': 'Servicios con Detracción', 'servicio': 'Servicios sin Detracción', 'bien': 'Bienes'}.get(c.categoria or '', c.tipo_documento.replace('_', ' ').title())
    pie = [Paragraph(f'Clasificación interna: <b>{_esc(cat_txt)}</b>' + (f' → {_esc(carpeta_rel)}' if carpeta_rel else '') + (f'  |  {_esc(c.razon)}' if c.razon else ''), S_SMALL),
           _p(f'Representación impresa generada desde el XML "{c.archivo}" para uso interno — no reemplaza la representación oficial del emisor. '
              f'Generado el {datetime.now().strftime("%d/%m/%Y %H:%M")}.', S_SMALL)]
    story.append(_box([[pie]], [ancho], [('LINEABOVE', (0, 0), (-1, 0), 0.5, _BORDE)], padding=3))

    def _pie_pagina(canvas, d):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawRightString(W - margen, 6 * mm, f'{c.serie_numero}  ·  pág. {d.page}')
        canvas.drawString(margen, 6 * mm, f'{c.nombre_emisor[:70]}  ·  RUC {c.ruc_emisor}')
        canvas.restoreState()

    doc.build(story, onFirstPage=_pie_pagina, onLaterPages=_pie_pagina)
    return destino


def _fecha(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        return iso or ''


def _tipo_operacion(code: str) -> str:
    return {'0101': 'Venta interna', '0102': 'Exportación', '0103': 'No domiciliados', '0104': 'Venta interna – anticipos', '0105': 'Venta itinerante',
            '0106': 'Factura guía', '0107': 'Venta arroz pilado', '0108': 'Factura – comprobante de percepción', '0110': 'Factura – guía remitente',
            '0111': 'Factura – guía transportista', '0112': 'Venta interna – sustenta gastos deducibles', '0113': 'Venta interna – NRUS',
            '0200': 'Exportación de bienes', '0201': 'Exportación de servicios – hospedaje', '1001': 'Operación sujeta a detracción',
            '1002': 'Detracción – recursos hidrobiológicos', '1003': 'Detracción – transporte de pasajeros', '1004': 'Detracción – transporte de carga',
            '2001': 'Operación sujeta a percepción', '2100': 'Créditos a empresas', '2101': 'Créditos de consumo revolvente',
            '2102': 'Créditos de consumo no revolvente', '2103': 'Otras operaciones no gravadas', '2104': 'Otras operaciones gravadas'}.get(code, code)


def _afect(code: str) -> str:
    return {'20': ' [EXONERADO]', '30': ' [INAFECTO]', '40': ' [EXPORTACIÓN]', '11': ' [GRATUITO]', '12': ' [GRATUITO]', '13': ' [GRATUITO]',
            '14': ' [GRATUITO]', '15': ' [GRATUITO]', '16': ' [GRATUITO]', '17': ' [IVAP]', '21': ' [GRATUITO]', '31': ' [GRATUITO]',
            '32': ' [GRATUITO]', '33': ' [GRATUITO]', '34': ' [GRATUITO]', '35': ' [GRATUITO]', '36': ' [GRATUITO]', '37': ' [GRATUITO]'}.get(code or '', '')


# ============================================================
# RESUMEN PARA EXCEL
# ============================================================
def a_fila_resumen(c: Comprobante, carpeta: str, pdf: str, alerta_fecha: bool, duplicado: bool) -> dict:
    return {
        'Archivo XML': c.archivo, 'PDF generado': pdf, 'Emisor': c.nombre_emisor, 'RUC Emisor': c.ruc_emisor,
        'Tipo': c.tipo_documento, 'Categoria': c.categoria or '', 'Moneda': c.moneda, 'Serie / Numero': c.serie_numero,
        'Fecha Emision': c.fecha_emision, 'Fecha Vencimiento': c.fecha_vencimiento, 'Forma Pago': c.forma_pago,
        'Base Gravada': float(c.gravadas), 'Exonerado': float(c.exoneradas), 'Inafecto': float(c.inafectas),
        'IGV': float(c.igv), 'ISC': float(c.isc), 'ICBPER': float(c.icbper), 'Monto Total': float(c.importe_total),
        'Tiene IGV': c.tiene_igv, 'Tiene Detraccion': c.tiene_detraccion, '% Detraccion': float(c.detraccion_porcentaje), 'Monto Detraccion': float(c.detraccion_monto),
        'Tiene Percepcion': c.tiene_percepcion, 'Monto Percepcion': float(c.percepcion_monto), 'Total con Percepcion': float(c.percepcion_total) if c.tiene_percepcion else '',
        'Nro Items': len(c.lineas), 'Cliente RUC': c.doc_cliente, 'Cliente': c.nombre_cliente,
        'Carpeta': carpeta, 'Alerta Fecha': alerta_fecha, 'Duplicado': duplicado, 'Razon': c.razon, 'Error': c.error,
    }


# ============================================================
# CLI para pruebas rápidas
# ============================================================
if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Uso: python xml_facturas.py <carpeta_xml> <carpeta_salida>')
        sys.exit(1)
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    for f in sorted(src.rglob('*.xml')):
        c = parse_xml(f)
        if c.error:
            print('SKIP', f.name, c.error)
            continue
        clasificar(c)
        dest = carpeta_destino(c, out)
        pdf = generar_pdf(c, dest / nombre_pdf(c), str(dest.relative_to(out)))
        print(f'{f.name:45} → {dest.relative_to(out)}  {c.simbolo} {fmt(c.importe_total):>10}  [{c.razon}]')
