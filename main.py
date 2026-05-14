import os
import pandas as pd
from flask import Flask, render_template, request, send_file, session
from io import BytesIO
from fpdf import FPDF

app = Flask(__name__, template_folder='templates')
app.secret_key = 'clave_secreta_uab_2024'

# =========================
# NORMALIZACIÓN
# =========================
def normalizar_datos(df):
    df.columns = [str(c).strip().upper() for c in df.columns]

    col_prod = next((c for c in df.columns if "PRODUCTO" in c), None)
    col_unid = next((c for c in df.columns if "UNID" in c), None)

    # Priorizar "CANTIDAD SOLICITADA", si no cualquier CANT
    col_cant = next((c for c in df.columns if "SOLICITADA" in c), None)
    if not col_cant:
        col_cant = next((c for c in df.columns if "CANT" in c), None)

    if not col_prod or not col_cant:
        return pd.DataFrame()

    df = df.dropna(subset=[col_prod])
    df[col_prod] = df[col_prod].astype(str).str.strip().str.upper()

    # Ignorar filas que son encabezados de sección (ej: "CAFÉ", "FRUTAS")
    df = df[~df[col_prod].isin(['NAN', '', 'PRODUCTO'])]

    if col_unid:
        df[col_unid] = df[col_unid].fillna("S/U").astype(str).str.strip().str.upper()
    else:
        col_unid = 'UNID. DE MEDIDA'
        df[col_unid] = "S/U"

    df[col_cant] = (
        df[col_cant]
        .astype(str)
        .str.replace(',', '.', regex=False)
    )

    df['CANTIDAD'] = pd.to_numeric(df[col_cant], errors='coerce')
    df = df.dropna(subset=['CANTIDAD'])
    df = df[df['CANTIDAD'] > 0]

    return df.rename(columns={
        col_prod: 'PRODUCTO',
        col_unid: 'UNID. DE MEDIDA'
    })[['PRODUCTO', 'UNID. DE MEDIDA', 'CANTIDAD']]


def consolidar(all_data):
    """Une DataFrames, agrupa por producto y numera."""
    if not all_data:
        return None
    df_total = pd.concat(all_data, ignore_index=True)
    df_total['CANTIDAD'] = pd.to_numeric(df_total['CANTIDAD'], errors='coerce')
    df_total = df_total.dropna(subset=['CANTIDAD'])

    consolidado = (
        df_total
        .groupby(['PRODUCTO', 'UNID. DE MEDIDA'], as_index=False)
        .agg({'CANTIDAD': 'sum'})
        .sort_values(by='PRODUCTO')
        .reset_index(drop=True)
    )
    consolidado.insert(0, 'N°', range(1, len(consolidado) + 1))
    return consolidado


def normalizar_dia(dia):
    """Quita tildes y pone mayúsculas para comparar nombres de hojas."""
    return (dia.strip().upper()
               .replace('É', 'E').replace('Á', 'A')
               .replace('Ó', 'O').replace('Í', 'I').replace('Ú', 'U'))


# =========================
# RUTAS
# =========================

@app.route('/')
def index():
    return render_template(
        'index.html',
        tablas=session.get('datos_procesados'),
        dia_seleccionado=session.get('dia_seleccionado', ''),
    )


# ── Consolidado total (comportamiento original) ──
@app.route('/procesar', methods=['POST'])
def procesar():
    if 'archivos' not in request.files:
        return "Error: No se recibieron archivos.", 400

    files = request.files.getlist('archivos')
    all_data = []
    archivos_estado = []

    for file in files:
        if file.filename == '':
            continue
        ok = False
        try:
            dict_hojas = pd.read_excel(file, sheet_name=None, header=None)

            for nombre_hoja, df_raw in dict_hojas.items():
                fila_encabezado = None
                for i, row in df_raw.iterrows():
                    if any("PRODUCTO" in str(c).upper() for c in row.values):
                        fila_encabezado = i
                        break

                if fila_encabezado is not None:
                    file.seek(0)
                    df_real = pd.read_excel(file, sheet_name=nombre_hoja, skiprows=fila_encabezado)
                    df_limpio = normalizar_datos(df_real)
                    if not df_limpio.empty:
                        all_data.append(df_limpio)
                        ok = True

        except Exception as e:
            print(f"Error procesando {file.filename}: {e}")

        archivos_estado.append({
            'nombre': file.filename,
            'ok': ok,
            'mensaje': 'Analizado' if ok else 'Sin datos válidos'
        })

    consolidado = consolidar(all_data)

    if consolidado is None:
        return render_template(
            'index.html',
            tablas=None,
            dia_seleccionado='',
            archivos_estado=archivos_estado,
        ), 400

    session['datos_procesados'] = consolidado.to_dict(orient='records')
    session['dia_seleccionado'] = ''

    return render_template(
        'index.html',
        tablas=session['datos_procesados'],
        dia_seleccionado='',
        archivos_estado=archivos_estado,
    )


# ── Análisis por día (NUEVO) ──
@app.route('/procesar-dia', methods=['POST'])
def procesar_dia():
    if 'archivos' not in request.files:
        return "Error: No se recibieron archivos.", 400

    dia_raw = request.form.get('dia', '').strip()
    if not dia_raw:
        return "Error: No se seleccionó ningún día.", 400

    dia_norm = normalizar_dia(dia_raw)   # ej: "MIERCOLES", "SABADO"

    files = request.files.getlist('archivos')
    all_data = []
    archivos_estado = []

    for file in files:
        if file.filename == '':
            continue
        ok      = False
        mensaje = 'Sin hoja para ese día'

        try:
            dict_hojas = pd.read_excel(file, sheet_name=None, header=None)

            for nombre_hoja, df_raw in dict_hojas.items():
                # Comparar nombre de hoja normalizado con el día buscado
                hoja_norm = normalizar_dia(str(nombre_hoja))

                if hoja_norm != dia_norm:
                    continue    # No es la pestaña del día buscado

                # Encontrar la fila donde está el encabezado (PRODUCTO)
                fila_encabezado = None
                for i, row in df_raw.iterrows():
                    if any("PRODUCTO" in str(c).upper() for c in row.values):
                        fila_encabezado = i
                        break

                if fila_encabezado is not None:
                    file.seek(0)
                    df_real = pd.read_excel(
                        file,
                        sheet_name=nombre_hoja,
                        skiprows=fila_encabezado
                    )
                    df_limpio = normalizar_datos(df_real)

                    if not df_limpio.empty:
                        all_data.append(df_limpio)
                        ok      = True
                        mensaje = f'Hoja "{nombre_hoja}" leída'

        except Exception as e:
            mensaje = f'Error: {str(e)[:50]}'
            print(f"Error procesando {file.filename}: {e}")

        archivos_estado.append({
            'nombre':  file.filename,
            'ok':      ok,
            'estado':  'ok' if ok else 'noday',
            'mensaje': mensaje
        })

    consolidado = consolidar(all_data)

    # Etiqueta legible del día para mostrar en el título de resultados
    dias_labels = {
        'LUNES': 'Lunes', 'MARTES': 'Martes', 'MIERCOLES': 'Miércoles',
        'JUEVES': 'Jueves', 'VIERNES': 'Viernes',
        'SABADO': 'Sábado', 'DOMINGO': 'Domingo',
    }
    dia_etiqueta = dias_labels.get(dia_norm, dia_raw.capitalize())

    if consolidado is None:
        return render_template(
            'index.html',
            tablas=None,
            dia_seleccionado=dia_etiqueta,
            archivos_estado=archivos_estado,
        ), 400

    session['datos_procesados'] = consolidado.to_dict(orient='records')
    session['dia_seleccionado'] = dia_etiqueta

    return render_template(
        'index.html',
        tablas=session['datos_procesados'],
        dia_seleccionado=dia_etiqueta,
        archivos_estado=archivos_estado,
    )


# =========================
# DESCARGAS
# =========================

@app.route('/descargar/<formato>')
def descargar(formato):
    datos = session.get('datos_procesados')

    if not datos:
        return "No hay datos para descargar", 400

    df = pd.DataFrame(datos)
    output = BytesIO()

    # ── EXCEL ──
    if formato == 'excel':
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Consolidado')
        output.seek(0)
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="consolidado_uab.xlsx"
        )

    # ── PDF ──
    elif formato == 'pdf':
        pdf = FPDF()
        pdf.add_page()

        pdf.set_font("helvetica", "B", 14)
        pdf.cell(0, 10, "CONSOLIDADO DE PEDIDOS - UAB", ln=True, align="C")
        pdf.ln(5)

        pdf.set_fill_color(230, 230, 230)
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(15, 10, "N°",       1, 0, 'C', True)
        pdf.cell(90, 10, "PRODUCTO", 1, 0, 'L', True)
        pdf.cell(35, 10, "UNIDAD",   1, 0, 'C', True)
        pdf.cell(35, 10, "CANTIDAD", 1, 1, 'C', True)

        pdf.set_font("helvetica", "", 9)
        for d in datos:
            if pdf.get_y() > 270:
                pdf.add_page()
            prod = str(d['PRODUCTO']).encode('latin-1', 'replace').decode('latin-1')
            unid = str(d['UNID. DE MEDIDA']).encode('latin-1', 'replace').decode('latin-1')
            pdf.cell(15, 8, str(d['N°']),               1, 0, 'C')
            pdf.cell(90, 8, f" {prod[:50]}",            1)
            pdf.cell(35, 8, f" {unid}",                 1, 0, 'C')
            pdf.cell(35, 8, f"{float(d['CANTIDAD']):.2f}", 1, 1, 'R')

        pdf_bytes = pdf.output(dest='S').encode('latin-1')
        output.write(pdf_bytes)
        output.seek(0)
        return send_file(
            output,
            mimetype="application/pdf",
            as_attachment=True,
            download_name="consolidado_uab.pdf"
        )


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)