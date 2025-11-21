# ia_resumen_bancario_santafe.py
# Herramienta para uso interno - AIE San Justo
# EXCLUSIVO BANCO DE SANTA FE – UNA CUENTA POR PDF

import io, re
from pathlib import Path
import numpy as np
import pandas as pd
import streamlit as st

# --- UI / assets ---
HERE = Path(__file__).parent
LOGO = HERE / "logo_aie.png"
FAVICON = HERE / "favicon-aie.ico"

st.set_page_config(
    page_title="IA Resumen Bancario – Banco de Santa Fe",
    page_icon=str(FAVICON) if FAVICON.exists() else None,
    layout="wide",
)

if LOGO.exists():
    st.image(str(LOGO), width=200)
st.title("IA Resumen Bancario – Banco de Santa Fe")

# --- deps diferidas ---
try:
    import pdfplumber
except Exception as e:
    st.error(f"No se pudo importar pdfplumber: {e}\nRevisá requirements.txt")
    st.stop()

# Para PDF del “Resumen Operativo: Registración Módulo IVA”
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

# ============================
# --- regex base / patrones ---
# ============================

# Fechas dd/mm/aa o dd/mm/aaaa
DATE_RE  = re.compile(r"\b\d{1,2}/\d{2}/\d{2,4}\b")

# ACEPTA IMPORTES CON SIGNO ADELANTE O GUION ATRÁS (ej: -2.114.972,30 o 2.114.972,30-)
MONEY_RE = re.compile(
    r'(?<!\S)-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?(?!\S)'
)

LONG_INT_RE = re.compile(r"\b\d{6,}\b")

# Santa Fe — "SALDO ULTIMO RESUMEN" sin fecha
SF_SALDO_ULT_RE = re.compile(r"SALDO\s+U?LTIMO\s+RESUMEN", re.IGNORECASE)

# Para clasificar percepciones RG 2408
RE_PERCEP_RG2408 = re.compile(r"PERCEPCI[ÓO]N\s+IVA\s+RG\.?\s*2408", re.IGNORECASE)

# ============================
# --- utils generales ---
# ============================

def normalize_money(tok: str) -> float:
    """
    Normaliza importes argentinos, aceptando:
    -2.114.972,30   ó   2.114.972,30-
    """
    if not tok:
        return np.nan
    tok = tok.strip().replace("−", "-")
    neg = tok.endswith("-") or tok.startswith("-")
    tok = tok.strip("-")
    if "," not in tok:
        return np.nan
    main, frac = tok.rsplit(",", 1)
    main = main.replace(".", "").replace(" ", "")
    try:
        val = float(f"{main}.{frac}")
        return -val if neg else val
    except Exception:
        return np.nan

def fmt_ar(n) -> str:
    if n is None or (isinstance(n, float) and np.isnan(n)):
        return "—"
    return f"{n:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")

def normalize_desc(desc: str) -> str:
    """Estandariza descripciones: quita prefijos de sucursal, enteros largos y normaliza espacios."""
    if not desc:
        return ""
    u = desc.upper()
    for pref in ("SAN JUS ", "CASA RO ", "CENTRAL ", "GOBERNA ", "GOBERNADOR ", "SANTA FE ", "ROSARIO "):
        if u.startswith(pref):
            u = u[len(pref):]
            break
    u = LONG_INT_RE.sub("", u)
    u = " ".join(u.split())
    return u

def _text_from_pdf(file_like) -> str:
    """Texto crudo, solo para buscar saldos."""
    try:
        with pdfplumber.open(file_like) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""

# ============================
# --- Saldos (desde líneas) ---
# ============================

def _only_one_amount(line: str) -> bool:
    return len(list(MONEY_RE.finditer(line))) == 1

def _first_amount_value(line: str) -> float:
    m = MONEY_RE.search(line)
    return normalize_money(m.group(0)) if m else np.nan

def extract_all_lines(file_like):
    """Combina extract_text y extract_words para tener líneas limpias."""
    out = []
    with pdfplumber.open(file_like) as pdf:
        for pi, p in enumerate(pdf.pages, start=1):
            txt = p.extract_text() or ""
            for raw in txt.splitlines():
                line = " ".join(raw.split())
                if line.strip():
                    out.append((pi, line))
    return out

def find_saldo_final_from_lines(lines):
    """
    Busca 'SALDO FINAL' (con o sin fecha) en el PDF.
    Para Santa Fe suele venir como 'SALDO FINAL AL ...' o similar.
    """
    # 1) Formato con fecha
    for ln in reversed(lines):
        U = ln.upper()
        if "SALDO FINAL" in U:
            d = DATE_RE.search(ln)
            am = list(MONEY_RE.finditer(ln))
            if d and am:
                fecha = pd.to_datetime(d.group(0), dayfirst=True, errors="coerce")
                saldo = normalize_money(am[-1].group(0))
                if pd.notna(fecha) and not np.isnan(saldo):
                    return fecha, saldo
    # 2) Solo "SALDO FINAL" con un único importe
    for ln in reversed(lines):
        U = ln.upper()
        if "SALDO FINAL" in U and _only_one_amount(ln):
            saldo = _first_amount_value(ln)
            if not np.isnan(saldo):
                return pd.NaT, saldo
    return pd.NaT, np.nan

def find_saldo_anterior_from_lines(lines):
    """
    PARA SANTA FE:
    - 'SALDO ANTERIOR ...'
    - 'SALDO ULTIMO RESUMEN' en misma línea o líneas siguientes.
    """
    # 1) SALDO ANTERIOR (misma línea)
    for ln in lines:
        U = ln.upper()
        if "SALDO ANTERIOR" in U and _only_one_amount(ln):
            saldo = _first_amount_value(ln)
            if not np.isnan(saldo):
                return saldo

    # 2) SALDO ULTIMO / ÚLTIMO RESUMEN
    for i, ln in enumerate(lines):
        U = ln.upper()
        if SF_SALDO_ULT_RE.search(ln) or "SALDO ÚLTIMO RESUMEN" in U:
            # mismo renglón
            if _only_one_amount(ln):
                v = _first_amount_value(ln)
                if not np.isnan(v):
                    return v
            # renglones siguientes (a veces saldo viene en la línea de abajo)
            for j in (i+1, i+2):
                if 0 <= j < len(lines):
                    ln2 = lines[j]
                    if _only_one_amount(ln2):
                        v2 = _first_amount_value(ln2)
                        if not np.isnan(v2):
                            return v2
            break

    return np.nan

# ============================
# --- Parser EXCLUSIVO Santa Fe ---
# ============================

def santafe_parse_movements(file_like) -> pd.DataFrame:
    """
    Parser específico Banco de Santa Fe.
    - Una sola cuenta por PDF (opción A).
    - Maneja:
        * PDFs con saldo por línea.
        * PDFs sin saldo por línea (solo al cierre de cada día).
    - Lógica:
        * Cada línea tiene un monto en DÉBITO o CRÉDITO (nunca ambos).
        * Si hay una tercera columna SALDO, se ignora para el cálculo,
          porque reconstruimos el saldo desde el saldo anterior + débitos/créditos.
    """
    rows = []
    orden = 0

    with pdfplumber.open(file_like) as pdf:
        for pageno, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(extra_attrs=["x0", "x1", "top"])
            if not words:
                continue

            # Agrupar palabras en líneas por banda Y
            ytol = 2.0
            bands = {}
            for w in words:
                band = round(w["top"] / ytol)
                bands.setdefault(band, []).append(w)

            bands_sorted = sorted(bands.items(), key=lambda x: x[0])

            # Detectar encabezado (FECHA / DÉBITO / CRÉDITO / SALDO)
            x_deb = x_cre = x_sal = None
            header_band_index = None

            for idx, (band, ws) in enumerate(bands_sorted):
                ws_sorted = sorted(ws, key=lambda w: w["x0"])
                text = " ".join(w["text"] for w in ws_sorted).upper()
                if "FECHA" in text and (("DEBITO" in text) or ("DÉBITO" in text) or ("DÉBIT" in text)) \
                   and (("CREDITO" in text) or ("CRÉDITO" in text) or ("CRÉDIT" in text)):
                    # Fijar centros de columnas
                    for w in ws_sorted:
                        t = w["text"].upper()
                        center = (w["x0"] + w["x1"]) / 2
                        if "DEBIT" in t or "DÉBIT" in t:
                            x_deb = center
                        elif "CREDIT" in t or "CRÉDIT" in t:
                            x_cre = center
                        elif "SALDO" in t:
                            x_sal = center
                    header_band_index = idx
                    break

            if header_band_index is None:
                # No se encontró encabezado estándar en esta página, la saltamos
                continue

            # Procesar líneas debajo del encabezado
            for band, ws in bands_sorted[header_band_index + 1:]:
                ws_sorted = sorted(ws, key=lambda w: w["x0"])
                raw_text = " ".join(w["text"] for w in ws_sorted)
                utext = raw_text.upper().strip()
                if not utext:
                    continue

                # Saltar líneas de totales generales o información que no son movimientos
                if "SALDO ANTERIOR" in utext or "SALDO ULTIMO RESUMEN" in utext or "SALDO ÚLTIMO RESUMEN" in utext:
                    continue
                if "TOTAL DIA" in utext or "TOTAL DÍA" in utext:
                    continue

                # Buscar fecha
                fecha = None
                date_idx = None
                for i, w in enumerate(ws_sorted):
                    m = DATE_RE.search(w["text"])
                    if m:
                        fecha = pd.to_datetime(m.group(0), dayfirst=True, errors="coerce")
                        date_idx = i
                        break
                if fecha is None or pd.isna(fecha):
                    continue

                # Detectar importes por columna (débito, crédito, saldo si lo hubiera)
                deb_val = 0.0
                cre_val = 0.0
                saldo_pdf = np.nan
                first_amount_idx = None

                for i, w in enumerate(ws_sorted):
                    txt = w["text"]
                    m = MONEY_RE.search(txt)
                    if not m:
                        continue
                    importe = normalize_money(m.group(0))
                    center = (w["x0"] + w["x1"]) / 2

                    # Elegir columna más cercana
                    candidates = []
                    if x_deb is not None:
                        candidates.append(("deb", abs(center - x_deb)))
                    if x_cre is not None:
                        candidates.append(("cre", abs(center - x_cre)))
                    if x_sal is not None:
                        candidates.append(("saldo", abs(center - x_sal)))

                    if not candidates:
                        continue

                    col = min(candidates, key=lambda t: t[1])[0]

                    if first_amount_idx is None:
                        first_amount_idx = i

                    if col == "deb":
                        deb_val += abs(importe)
                    elif col == "cre":
                        cre_val += abs(importe)
                    elif col == "saldo":
                        saldo_pdf = importe

                # Si no hay débitos ni créditos ni saldo, no es movimiento
                if deb_val == 0.0 and cre_val == 0.0 and np.isnan(saldo_pdf):
                    continue

                if first_amount_idx is None:
                    continue

                # Descripción: entre la fecha y el primer importe
                desc_words = ws_sorted[date_idx + 1:first_amount_idx]
                desc = " ".join(w["text"] for w in desc_words).strip()

                orden += 1
                rows.append({
                    "fecha": fecha,
                    "descripcion": desc,
                    "desc_norm": normalize_desc(desc),
                    "debito": float(deb_val),
                    "credito": float(cre_val),
                    "saldo_pdf": float(saldo_pdf) if not np.isnan(saldo_pdf) else np.nan,
                    "pagina": pageno,
                    "orden": orden,
                })

    return pd.DataFrame(rows)

# ============================
# --- Clasificación ---
# ============================

def clasificar(desc: str, desc_norm: str, deb: float, cre: float) -> str:
    u = (desc or "").upper()
    n = (desc_norm or "").upper()

    # Saldos
    if "SALDO ANTERIOR" in u or "SALDO ANTERIOR" in n:
        return "SALDO ANTERIOR"

    # Impuesto a los débitos y créditos bancarios (Ley 25.413 / IMPTRANS)
    if ("LEY 25413" in u) or ("IMPTRANS" in u) or ("IMP.S/CREDS" in u) or \
       ("LEY 25413" in n) or ("IMPTRANS" in n) or ("IMP.S/CREDS" in n):
        return "LEY 25.413"

    # SIRCREB
    if ("SIRCREB" in u) or ("SIRCREB" in n):
        return "SIRCREB"

    # Percepciones IVA: atajo RG 2408
    if RE_PERCEP_RG2408.search(u) or RE_PERCEP_RG2408.search(n):
        return "Percepciones de IVA"

    # Percepciones / Retenciones IVA (RG 3337 / RG 2408) y variantes
    if (
        ("IVA PERC" in u) or ("IVA PERCEP" in u) or ("RG3337" in u) or
        ("IVA PERC" in n) or ("IVA PERCEP" in n) or ("RG3337" in n) or
        (("RETEN" in u or "RETENC" in u) and (("I.V.A" in u) or ("IVA" in u))) or
        (("RETEN" in n or "RETENC" in n) and (("I.V.A" in n) or ("IVA" in n)))
    ):
        return "Percepciones de IVA"

    # IVA sobre comisiones Santa Fe:
    # - 'IVA GRAL' → 21%
    # - 'IVA RINS', 'IVA REDUC' → 10,5%
    if "IVA GRAL" in u or "IVA GRAL" in n:
        return "IVA 21% (sobre comisiones)"
    if "IVA RINS" in u or "IVA REDUC" in u or "IVA RINS" in n or "IVA REDUC" in n:
        return "IVA 10,5% (sobre comisiones)"

    # Comisiones varias
    if ("COMOPREM" in n) or ("COMVCAUT" in n) or ("COMTRSIT" in n) or ("COM.NEGO" in n) or ("CO.EXCESO" in n) or ("COM." in n):
        return "Gastos por comisiones"

    # Débitos automáticos / Seguros
    if ("DB-SNP" in n) or ("DEB.AUT" in n) or ("DEB.AUTOM" in n) or ("SEGUROS" in n) or ("GTOS SEG" in n):
        return "Débito automático"

    # DyC / ARCA / API
    if "DYC" in n:
        return "DyC"
    if ("AFIP" in n or "ARCA" in n) and deb and deb != 0:
        return "Débitos ARCA"
    if "API" in n:
        return "API"

    # Préstamos
    if "DEB.CUOTA PRESTAMO" in n or ("PRESTAMO" in n and "DEB." in n):
        return "Cuota de préstamo"
    if ("CR.PREST" in n) or ("CREDITO PRESTAMOS" in n) or ("CRÉDITO PRÉSTAMOS" in n):
        return "Acreditación Préstamos"

    # Cheques 48hs
    if "CH 48 HS" in n or "CH.48 HS" in n:
        return "Cheques 48 hs"

    # Créditos específicos
    if ("PAGO COMERC" in n) or ("CR-CABAL" in n) or ("CR CABAL" in n) or ("CR TARJ" in n):
        return "Acreditaciones Tarjetas de Crédito/Débito"

    # Depósitos en efectivo
    if ("CR-DEPEF" in n) or ("CR DEPEF" in n) or ("DEPOSITO EFECTIVO" in n) or ("DEP.EFECTIVO" in n) or ("DEP EFECTIVO" in n):
        return "Depósito en Efectivo"

    # Transferencias
    if (("CR-TRSFE" in n) or ("TRANSF RECIB" in n) or ("TRANLINK" in n)) and cre and cre != 0:
        return "Transferencia de terceros recibida"
    if (("DB-TRSFE" in n) or ("TRSFE-ET" in n) or ("TRSFE-IT" in n)) and deb and deb != 0:
        return "Transferencia a terceros realizada"
    if ("DTNCTAPR" in n) or ("ENTRE CTA" in n) or ("CTA PROPIA" in n):
        return "Transferencia entre cuentas propias"

    # Negociados / acreditaciones de valores
    if ("NEG.CONT" in n) or ("NEGOCIADOS" in n):
        return "Acreditación de valores"

    # Fallback por signo (nunca débito y crédito en la misma fila)
    if cre and cre != 0:
        return "Crédito"
    if deb and deb != 0:
        return "Débito"
    return "Otros"

# ============================
# --- UI principal Santa Fe ---
# ============================

uploaded = st.file_uploader("Subí un PDF del resumen bancario (Banco de Santa Fe)", type=["pdf"])
if uploaded is None:
    st.info("La app no almacena datos, toda la información está protegida.")
    st.stop()

data = uploaded.read()

# Texto completo para saldos
all_lines = [l for _, l in extract_all_lines(io.BytesIO(data))]
txt_full  = _text_from_pdf(io.BytesIO(data))

if not txt_full.strip():
    st.error(
        "No se pudo leer texto del PDF. "
        "Este resumen parece estar escaneado (solo imagen). "
        "La herramienta solo funciona con PDFs descargados del home banking, "
        "donde el texto sea seleccionable."
    )
    st.stop()

with st.spinner("Procesando PDF del Banco de Santa Fe..."):
    df = santafe_parse_movements(io.BytesIO(data))

if df.empty:
    st.error("No se detectaron movimientos. Si el PDF tiene un formato distinto, pasame una línea ejemplo (fecha + descripción + importe).")
    st.stop()

# Saldos
fecha_cierre, saldo_final_pdf = find_saldo_final_from_lines(all_lines)
saldo_anterior = find_saldo_anterior_from_lines(all_lines)

# Insertar SALDO ANTERIOR como primera fila (solo visual / saldo)
if not np.isnan(saldo_anterior):
    first_date = df["fecha"].dropna().min()
    if pd.notna(first_date):
        fecha_apertura = (first_date - pd.Timedelta(days=1)).normalize() + pd.Timedelta(hours=23, minutes=59, seconds=59)
    else:
        fecha_apertura = pd.NaT

    apertura = pd.DataFrame([{
        "fecha": fecha_apertura,
        "descripcion": "SALDO ANTERIOR",
        "desc_norm": "SALDO ANTERIOR",
        "debito": 0.0,
        "credito": 0.0,
        "saldo_pdf": saldo_anterior,
        "pagina": 0,
        "orden": 0
    }])
    df = pd.concat([apertura, df], ignore_index=True)

# Orden correcto
df = df.sort_values(["fecha", "orden", "pagina"]).reset_index(drop=True)

# Reconstruir saldo SIN inventar importes:
# saldo_n = saldo_{n-1} + crédito - débito
if np.isnan(saldo_anterior):
    # Si por algún motivo no hay saldo anterior, arrancamos en 0
    saldo_base = 0.0
else:
    saldo_base = float(saldo_anterior)

saldos_calc = []
saldo = saldo_base
for idx, row in df.iterrows():
    if str(row.get("descripcion", "")).upper().strip() == "SALDO ANTERIOR":
        # Para la fila de saldo anterior, mostramos el saldo base
        saldo = saldo_base
        saldos_calc.append(saldo)
        continue
    deb = float(row.get("debito", 0.0) or 0.0)
    cre = float(row.get("credito", 0.0) or 0.0)
    saldo = saldo + cre - deb
    saldos_calc.append(saldo)

df["saldo"] = saldos_calc

# Clasificación por fila
df["Clasificación"] = df.apply(
    lambda r: clasificar(
        str(r.get("descripcion", "")),
        str(r.get("desc_norm", "")),
        r.get("debito", 0.0),
        r.get("credito", 0.0),
    ),
    axis=1,
)

# Totales / conciliación
df_sorted = df.drop(columns=["orden"]).reset_index(drop=True)

saldo_inicial = float(df_sorted["saldo"].iloc[0])
total_debitos = float(df_sorted["debito"].sum())
total_creditos = float(df_sorted["credito"].sum())

saldo_final_calc = float(df_sorted["saldo"].iloc[-1])
saldo_final_visto = float(saldo_final_pdf) if not np.isnan(saldo_final_pdf) else saldo_final_calc

diferencia = saldo_final_calc - saldo_final_visto
cuadra = abs(diferencia) < 0.01

# ============================
# --- Resumen Operativo (arriba) ---
# ============================

st.subheader("Resumen Operativo: Registración Módulo IVA")

iva21_mask  = df_sorted["Clasificación"].eq("IVA 21% (sobre comisiones)")
iva105_mask = df_sorted["Clasificación"].eq("IVA 10,5% (sobre comisiones)")

iva21  = float(df_sorted.loc[iva21_mask,  "debito"].sum())
iva105 = float(df_sorted.loc[iva105_mask, "debito"].sum())

net21  = round(iva21  / 0.21,  2) if iva21  else 0.0
net105 = round(iva105 / 0.105, 2) if iva105 else 0.0

percep_iva = float(df_sorted.loc[df_sorted["Clasificación"].eq("Percepciones de IVA"), "debito"].sum())
ley_25413  = float(df_sorted.loc[df_sorted["Clasificación"].eq("LEY 25.413"),          "debito"].sum())
sircreb    = float(df_sorted.loc[df_sorted["Clasificación"].eq("SIRCREB"),            "debito"].sum())

# Métricas IVA
m1, m2, m3 = st.columns(3)
with m1: st.metric("Neto Comisiones 21%", f"$ {fmt_ar(net21)}")
with m2: st.metric("IVA 21%", f"$ {fmt_ar(iva21)}")
with m3: st.metric("Bruto 21%", f"$ {fmt_ar(net21 + iva21)}")

n1, n2, n3 = st.columns(3)
with n1: st.metric("Neto Comisiones 10,5%", f"$ {fmt_ar(net105)}")
with n2: st.metric("IVA 10,5%", f"$ {fmt_ar(iva105)}")
with n3: st.metric("Bruto 10,5%", f"$ {fmt_ar(net105 + iva105)}")

o1, o2, o3 = st.columns(3)
with o1: st.metric("Percepciones de IVA", f"$ {fmt_ar(percep_iva)}")
with o2: st.metric("Ley 25.413", f"$ {fmt_ar(ley_25413)}")
with o3: st.metric("SIRCREB", f"$ {fmt_ar(sircreb)}")

total_operativo = net21 + iva21 + net105 + iva105 + percep_iva + ley_25413 + sircreb
st.metric("Total Resumen Operativo", f"$ {fmt_ar(total_operativo)}")

st.markdown("---")

# ============================
# --- Resumen del período ---
# ============================

st.subheader("Resumen del período")

c1, c2, c3 = st.columns(3)
with c1: st.metric("Saldo inicial", f"$ {fmt_ar(saldo_inicial)}")
with c2: st.metric("Total créditos (+)", f"$ {fmt_ar(total_creditos)}")
with c3: st.metric("Total débitos (–)", f"$ {fmt_ar(total_debitos)}")

c4, c5, c6 = st.columns(3)
with c4: st.metric("Saldo final (PDF)", f"$ {fmt_ar(saldo_final_visto)}")
with c5: st.metric("Saldo final calculado", f"$ {fmt_ar(saldo_final_calc)}")
with c6: st.metric("Diferencia", f"$ {fmt_ar(diferencia)}")

if cuadra:
    st.success("Conciliado.")
else:
    st.error("No cuadra la conciliación.")

if pd.notna(fecha_cierre):
    st.caption(f"Cierre según PDF: {fecha_cierre.strftime('%d/%m/%Y')}")

# ============================
# --- Detalle de movimientos ---
# ============================

st.markdown("---")
st.subheader("Detalle de movimientos")

df_view = df_sorted.copy()
for c in ["debito", "credito", "saldo"]:
    if c in df_view.columns:
        df_view[c] = df_view[c].map(fmt_ar)

st.dataframe(df_view, use_container_width=True)

# ============================
# --- Descargas ---
# ============================

st.markdown("---")
st.subheader("Descargar")

date_suffix = f"_{fecha_cierre.strftime('%Y%m%d')}" if pd.notna(fecha_cierre) else ""

try:
    import xlsxwriter
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_sorted.to_excel(writer, index=False, sheet_name="Movimientos")
        wb  = writer.book
        ws  = writer.sheets["Movimientos"]
        money_fmt = wb.add_format({"num_format": "#,##0.00"})
        date_fmt  = wb.add_format({"num_format": "dd/mm/yyyy"})
        for idx, col in enumerate(df_sorted.columns, start=0):
            col_values = df_sorted[col].astype(str)
            max_len = max(len(col), *(len(v) for v in col_values))
            ws.set_column(idx, idx, min(max_len + 2, 40))
        for c in ["debito", "credito", "saldo"]:
            if c in df_sorted.columns:
                j = df_sorted.columns.get_loc(c)
                ws.set_column(j, j, 16, money_fmt)
        if "fecha" in df_sorted.columns:
            j = df_sorted.columns.get_loc("fecha")
            ws.set_column(j, j, 14, date_fmt)

    st.download_button(
        "📥 Descargar Excel",
        data=output.getvalue(),
        file_name=f"resumen_bancario_santafe{date_suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
except Exception:
    csv_bytes = df_sorted.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Descargar CSV (fallback)",
        data=csv_bytes,
        file_name=f"resumen_bancario_santafe{date_suffix}.csv",
        mime="text/csv",
        use_container_width=True,
    )

# --- PDF del Resumen Operativo (si reportlab disponible) ---
if REPORTLAB_OK:
    try:
        pdf_buf = io.BytesIO()
        doc = SimpleDocTemplate(
            pdf_buf,
            pagesize=A4,
            title="Resumen Operativo - Registración Módulo IVA (Banco de Santa Fe)",
        )
        styles = getSampleStyleSheet()
        elems = []
        elems.append(Paragraph("Resumen Operativo: Registración Módulo IVA", styles["Title"]))
        elems.append(Spacer(1, 8))
        datos = [
            ["Concepto", "Importe"],
            ["Neto Comisiones 21%",  fmt_ar(net21)],
            ["IVA 21%",               fmt_ar(iva21)],
            ["Bruto 21%",             fmt_ar(net21 + iva21)],
            ["Neto Comisiones 10,5%", fmt_ar(net105)],
            ["IVA 10,5%",             fmt_ar(iva105)],
            ["Bruto 10,5%",           fmt_ar(net105 + iva105)],
            ["Percepciones de IVA",   fmt_ar(percep_iva)],
            ["Ley 25.413",            fmt_ar(ley_25413)],
            ["SIRCREB",               fmt_ar(sircreb)],
            ["TOTAL",                 fmt_ar(total_operativo)],
        ]
        tbl = Table(datos, colWidths=[300, 120])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.black),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.grey),
            ("ALIGN",      (1,1), (1,-1), "RIGHT"),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTNAME",   (0,-1), (-1,-1), "Helvetica-Bold"),
        ]))
        elems.append(tbl)
        elems.append(Spacer(1, 12))
        elems.append(Paragraph("Herramienta para uso interno - AIE San Justo", styles["Normal"]))
        doc.build(elems)

        st.download_button(
            "📄 Descargar PDF – Resumen Operativo (IVA)",
            data=pdf_buf.getvalue(),
            file_name=f"Resumen_Operativo_IVA_SantaFe{date_suffix}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.info(f"No se pudo generar el PDF del Resumen Operativo: {e}")
else:
    st.caption("Para descargar el PDF del Resumen Operativo instalá reportlab en requirements.txt")
