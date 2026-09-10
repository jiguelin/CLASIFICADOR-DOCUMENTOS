# Clasificador de Facturas (imágenes, PDF y XML)

Misma app de siempre, ahora también acepta **XML de SUNAT**.

## Archivos (todos en la raíz del repo)
- `app.py`           → app Streamlit unificada
- `xml_facturas.py`  → motor XML: parser UBL 2.1 + clasificación por reglas + PDF tipo factura
- `requirements.txt` → streamlit, anthropic, PyMuPDF, pandas, openpyxl, lxml, reportlab

`streamlit run app.py`

## Cómo decide por archivo
| Archivo | Cómo se clasifica | Qué va al ZIP |
|---|---|---|
| JPG / PNG / PDF | Claude visión (flujo original, con API key) | el archivo tal cual |
| XML | Reglas leídas del XML: detracción, percepción, moneda, RUC, ítems (sin API) | PDF generado `RUC-TIPO-SERIE.pdf` (+ XML original, opcional) |

- Misma estructura de carpetas para los dos orígenes.
- Duplicados se detectan entre orígenes: `F672-00603536` (XML) y `F672-603536` (impresión) son el mismo.
- Si solo subes XML no hace falta API key.
- Excel: columna "Origen" y hojas extra: Alerta Cliente, Con Detraccion, Con Percepcion.

## Agregar RUCs conocidos
Solo en `xml_facturas.py`, sección "LISTAS DE RUCs CONOCIDOS". `app.py` las importa de ahí.
