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

- `RUCS_MIXTO_GRIFO` es para emisores que venden combustible **y** comida (grifos con tienda,
  ej. Repsol Comercial, Coesti/Primax). Ahí no manda el RUC sino la descripción del ítem;
  el segundo valor de la tupla es la categoría por defecto si la descripción no es clara.

## Reglas generales de categoría (aplican a cualquier emisor, esté o no en las listas)
Están en `xml_facturas.py`, debajo de las listas de RUCs, y las usan tanto los XML como
las fotos/PDF:

1. **Detracción** declarada → Servicios con Detracción (manda sobre todo lo demás).
2. **Comida o bebida preparada** en la descripción (`_KW_RESTAURANTE`: combo, hamburguesa,
   pizza, borde queso, chaufa, tacos, helado, Coca Cola…) → Restaurantes y Consumos.
   Esto vence a "bien" y también a "servicio": muchos restaurantes (Fridays, cafeterías)
   facturan sus platos con unidad de medida ZZ = SERVICIO y antes caían en Servicios.
3. **Nombre del emisor** (`_MARCAS_RESTAURANTE`, `_PALABRAS_NOMBRE_RESTAURANTE`,
   `_PALABRAS_NOMBRE_SERVICIO`): cubre franquicias cuya razón social no dice nada
   (DELOSI = KFC, SAIDEL = Burger King, PINKDEL = Pinkberry, ARCOS DORADOS = McDonald's)
   y servicios que facturan como si fueran productos (una lavandería que factura
   "BLUSA DE SEDA" cobra el lavado → servicio, no bien).
4. Salvaguardas: `_KW_SERVICIO_FUERTE` (alquiler, consultoría, flete…) impide que una
   palabra de comida convierta un servicio empresarial en consumo; `_PALABRAS_NOMBRE_COMERCIO`
   (importadora, distribuidora, ferretería…) impide que una palabra genérica del nombre
   mande a restaurantes a un comercio de productos.

Las palabras admiten `*` al final como prefijo: `POLLERI*` cubre POLLERIA y POLLERIAS.
