# Clasificador de Facturas XML → PDF

Nueva versión del clasificador: en vez de imágenes/PDF escaneados, recibe los **XML de SUNAT (UBL 2.1)**
y devuelve un **PDF legible tipo factura por comprobante**, ya clasificado en carpetas, más un Excel resumen.

## Archivos
- `app_xml.py`       → interfaz Streamlit (subir XML/ZIP, descargar ZIP y Excel)
- `xml_facturas.py`  → parser UBL + clasificación por reglas + generador PDF (reutilizable / CLI)
- `requirements.txt`

## Ejecutar
```
pip install -r requirements.txt
streamlit run app_xml.py
```
Prueba rápida sin interfaz:
```
python xml_facturas.py carpeta_con_xml/ salida/
```

## Qué hace con cada XML
1. Lee tipo (01/03/07/08/30), serie, fechas, moneda, emisor, cliente, ítems, IGV/ISC/ICBPER,
   detracción (PaymentTerms "Detraccion" o leyenda 2006), percepción (PaymentTerms "Percepcion"
   o cargo código 51), cuotas de crédito, leyendas y guías.
2. Clasifica **sin IA** (determinista):
   RUC conocido → detracción → combustible/seguro por descripción → unidad ZZ/servicio → consumo → bien.
   La estructura de carpetas es la misma del clasificador anterior.
3. Genera el PDF (A4, reportlab) y lo guarda como `RUC-TIPO-SERIE.pdf` en su carpeta.
   Opcional: copia el XML original al lado.
4. Excel con hojas: Todos, Duplicados, Alerta Fechas, Alerta Cliente, Con Detraccion, Con Percepcion,
   Por Carpeta, No Procesados.

## Opción IA
Si se activa en "Opciones", Claude Haiku revisa solo las facturas de emisores desconocidos
(texto de los ítems, sin imágenes) para afinar bien / servicio / consumo. Sin API key funciona igual, solo con reglas.

## Agregar RUCs conocidos
Editar los diccionarios `RUCS_*` al inicio de `xml_facturas.py`.
