# ia_resumen_bancario_santafe.py
# Exclusivo NUEVO BANCO DE SANTA FE - AIE San Justo

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
    page_title="IA Resumen Bancario - Banco de Santa Fe",
    page_icon=str(FAVICON) if FAVICON.exists() else None,
    layout="wide",
)

if LOGO.exists():
    st.image(str(LOGO), width=200)

st.title("IA Resumen Bancario · Banco de Santa Fe")

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

# --- regex base (Santa Fe) ---
DATE_RE = re.compile(r"\b\d{1,2}/\d{2}/\d{2,4}\b")  # dd/mm/aa o dd/mm/aaaa

# Acepta importes con signo adelante o guion atrás
MONEY_RE = re.compile(
    r'(?<!\S)-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?(?!\S)'
)

LONG_INT_RE = re.compile(r"\b\d{6,}\b")
SF_SALDO_ULT_RE = re.compile(r"SALDO\s+U?LTIMO\s+RESUMEN", re.IGNORECASE)

# --- utils ---

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

def lines_from_text(page):
    txt = page.extract_text() or ""
    return [" ".join(l.split()) for l in txt.splitlines()]

def normalize_desc(desc: str) -> str:
    if not desc:
        return ""
    u = desc.upper()
    # Limpieza de prefijos de sucursal comunes
    for pref in ("SAN JUS ", "CASA RO ", "CENTRAL ", "GOBERNA ", "GOBERNADOR ",
                 "SANTA FE ", "ROSARIO "):
        if u.startswith(pref):
            u = u[len(pref):]
            break
    u = LONG_INT_RE.sub("", u)
    u = " ".join(u.split())
    return u

def extract_all_lines(file_like):
    out = []
    with pdfplumber.open(file_like) as pdf:
        for pi, p in enumerate(pdf.pages, start=1):
            for raw in (p.extract_text() or "").splitlines():
                line = " ".join(raw.split())
                if line.strip():
                    out.append((pi, line))
    return out

# --- saldos Santa Fe ---

def _only_one_amount(line: str) -> bool:
    return len(list(MONEY_RE.finditer(line))) == 1

def _first_amount_value(line: str) -> float:
    m = MONEY_RE.search(line)
    return normalize_money(m.group(0)) if m else np.nan

def find_saldo_anterior_santafe(file_like) -> float:
    """
    Busca SALDO ANTERIOR o SALDO ÚLTIMO RESUMEN (línea o siguiente).
    Esta lógica es la que te funcionaba bien.
    """
    lines = [l for _, l in extract_all_lines(file_like)]

    # 1) SALDO ANTERIOR en la misma línea
    for ln in lines:
        U = ln.upper()
        if "SALDO ANTERIOR" in U and _only_one_amount(ln):
            v = _first_amount_value(ln)
            if not np.isnan(v):
                return v

    # 2) SALDO ÚLTIMO EXTRACTO (variante vieja)
    for ln in lines:
        U = ln.upper()
        if "SALDO ULTIMO EXTRACTO" in U or "SALDO ÚLTIMO EXTRACTO" in U:
            if _only_one_amount(ln):
                v = _first_amount_value(ln)
                if not np.isnan(v):
                    return v

    # 3) Santa Fe — "SALDO ULTIMO RESUMEN" en línea o siguientes
    for i, ln in enumerate(lines):
        if SF_SALDO_ULT_RE.search(ln):
            # mismo renglón
            if _only_one_amount(ln):
                v = _first_amount_value(ln)
                if not np.isnan(v):
                    return v
            # o hasta dos líneas más abajo
            for j in (i+1, i+2):
                if 0 <= j < len(lines):
                    ln2 = lines[j]
                    if _only_one_amount(ln2):
                        v2 = _first_amount_value(ln2)
                        if not np.isnan(v2):
                            return v2
            break

    return np.nan

def find_saldo_final_santafe(file_like):
    """
    Para Santa Fe el final suele aparecer como 'Saldo al dd/mm/aaaa ...'.
    """
    with pdfplumber.open(file_like) as pdf:
        for page in reversed(pdf.pages):
            txt = page.extract_text() or ""
            for raw in txt.splitlines():
                line = " ".join(raw.split())
                U = line.upper()
                if "SALDO AL" in U or "SALDO FINAL" in U:
                    d = DATE_RE.search(line)
                    am = list(MONEY_RE.finditer(line))
                    if d and am:
                        fecha = pd.to_datetime(d.group(0), dayfirst=True, errors="coerce")
                        saldo = normalize_money(am[-1].group(0))
                        return fecha, saldo
    return pd.NaT, np.nan

# --- parser Santa Fe: cada movimiento = una fila, importe siempre presente ---

def parse_movimientos_santafe(file_like) -> pd.DataFrame:
    """
    - Si la línea tiene 1 importe -> importe = ese, saldo_pdf = NaN.
    - Si la línea tiene 2 o más importes -> saldo_pdf = último, importe = penúltimo.
    NO se inventan montos, se toma siempre lo que viene en la línea.
    """
    rows = []
    seq = 0
    with pdfplumber.open(file_like) as pdf:
        for pageno, page in enumerate(pdf.pages, start=1):
            for raw in (page.extract_text() or "").splitlines():
                line = " ".join(raw.split())
                if not line:
                    continue

                d = DATE_RE.search(line)
                if not d:
                    continue

                am = list(MONEY_RE.finditer(line))
                if not am:
                    continue

                if len(am) == 1:
                    importe = normalize_money(am[0].group(0))
                    saldo_pdf = np.nan
                else:
                    saldo_pdf = normalize_money(am[-1].group(0))
                    importe = normalize_money(am[-2].group(0))

                first_money = am[0]
                desc = line[d.end(): first_money.start()].strip()

                seq += 1
                rows.append({
                    "fecha": pd.to_datetime(d.group(0), dayfirst=True, errors="coerce"),
                    "descripcion": desc,
                    "desc_norm": normalize_desc(desc),
                    "importe": importe,
                    "saldo_pdf": saldo_pdf,
                    "pagina": pageno,
                    "orden": seq,
                })

    return pd.DataFrame(rows)

# --- Clasificación Santa Fe (solo por texto, sin depender del signo) ---

RE_PERCEP_RG2408 = re.compile(r"PERCEPCI[ÓO]N\s+IVA\s+RG\.?\s*2408", re.IGNORECASE)

def clasificar_sf(desc: str, desc_norm: str) -> str:
    u = (desc or "").upper()
    n = (desc_norm or "").upper()

    # Saldos (por si llegaran a entrar como movimiento)
    if "SALDO ANTERIOR" in u or "SALDO ANTERIOR" in n:
        return "SALDO ANTERIOR"

    # Impuesto a los débitos y créditos bancarios
    if ("LEY 25413" in u) or ("IMPTRANS" in u) or ("IMP.S/CREDS" in u) or ("IMPDBCR 25413" in u) or ("N/D DBCR 25413" in u) or \
       ("LEY 25413" in n) or ("IMPTRANS" in n) or ("IMP.S/CREDS" in n) or ("IMPDBCR 25413" in n) or ("N/D DBCR 25413" in n):
        return "LEY 25.413"

    # SIRCREB
    if "SIRCREB" in u or "SIRCREB" in n:
        return "SIRCREB"

    # Percepciones IVA: atajo RG 2408
    if RE_PERCEP_RG2408.search(u) or RE_PERCEP_RG2408.search(n):
        return "Percepciones de IVA"

    # Percepciones / Retenciones IVA (RG 3337 / RG 2408)
    if (
        ("IVA PERC" in u) or ("IVA PERCEP" in u) or ("RG3337" in u) or
        ("IVA PERC" in n) or ("IVA PERCEP" in n) or ("RG3337" in n) or
        (("RETEN" in u or "RETENC" in u) and (("I.V.A" in u) or ("IVA" in u)) and (("RG.2408" in u) or ("RG 2408" in u) or ("RG2408" in u))) or
        (("RETEN" in n or "RETENC" in n) and (("I.V.A" in n) or ("IVA" in n)) and (("RG.2408" in n) or ("RG 2408" in n) or ("RG2408" in n)))
    ):
        return "Percepciones de IVA"

    # Percepciones IVA genéricas
    if (
        ("RETENCION" in u and "IVA" in u and "PERCEP" in u) or
        ("RETENCION" in n and "IVA" in n and "PERCEP" in n) or
        ("RETEN" in u and "IVA" in u and "PERC" in u) or
        ("RETEN" in n and "IVA" in n and "PERC" in n)
    ):
        return "Percepciones de IVA"

    # IVA sobre comisiones
    if ("IVA GRAL" in u) or ("IVA GRAL" in n) or ("I.V.A. BASE" in u) or ("I.V.A. BASE" in n) or \
       ("DEBITO FISCAL IVA BASICO" in u) or ("DEBITO FISCAL IVA BASICO" in n) or \
       ("I.V.A" in u and "DÉBITO FISCAL" in u) or ("I.V.A" in n and "DEBITO FISCAL" in n):
        if "10,5" in u or "10,5" in n or "10.5" in u or "10.5" in n or "RINS" in u or "RINS" in n or "REDUC" in u or "REDUC" in n:
            return "IVA 10,5% (sobre comisiones)"
        return "IVA 21% (sobre comisiones)"

    # Plazo Fijo
    if ("PLAZO FIJO" in u) or ("PLAZO FIJO" in n) or ("P.FIJO" in u) or ("P.FIJO" in n) or \
       ("P FIJO" in u) or ("P FIJO" in n) or ("PFIJO" in u) or ("PFIJO" in n):
        return "Plazo Fijo"

    # Comisiones
    if ("COMIS.TRANSF" in u) or ("COMIS.TRANSF" in n) or ("COMIS TRANSF" in u) or ("COMIS TRANSF" in n) or \
       ("COMIS.COMPENSACION" in u) or ("COMIS.COMPENSACION" in n) or \
       ("COMIS COMPENSACION" in u) or ("COMIS COMPENSACION" in n):
        return "Gastos por comisiones"
    if ("MANTENIMIENTO MENSUAL PAQUETE" in u) or ("MANTENIMIENTO MENSUAL PAQUETE" in n) or \
       ("COMOPREM" in n) or ("COMVCAUT" in n) or ("COMTRSIT" in n) or ("COM.NEGO" in n) or ("CO.EXCESO" in n) or ("COM." in n):
        return "Gastos por comisiones"

    # Débitos automáticos / seguros
    if ("DB-SNP" in n) or ("DEB.AUT" in n) or ("DEB.AUTOM" in n) or ("SEGUROS" in n) or ("GTOS SEG" in n):
        return "Débito automático"
    if "DEBITO INMEDIATO" in u or "DEBIN" in u:
        return "Débito automático"

    # Varios fiscales
    if "DYC" in n:
        return "DyC"
    if "AFIP" in n or "ARCA" in n:
        return "Débitos ARCA"
    if "API" in n:
        return "API"

    # Préstamos
    if "DEB.CUOTA PRESTAMO" in n or ("PRESTAMO" in n and "DEB." in n):
        return "Cuota de préstamo"
    if ("CR.PREST" in n) or ("CREDITO PRESTAMOS" in n) or ("CRÉDITO PRÉSTAMOS" in n):
        return "Acreditación Préstamos"

    # Cheques
    if "CH 48 HS" in n or "CH.48 HS" in n:
        return "Cheques 48 hs"

    # Créditos específicos
    if ("PAGO COMERC" in n) or ("CR-CABAL" in n) or ("CR CABAL" in n) or ("CR TARJ" in n):
        return "Acreditaciones Tarjetas de Crédito/Débito"

    # Depósitos en efectivo
    if ("CR-DEPEF" in n) or ("CR DEPEF" in n) or ("DEPOSITO EFECTIVO" in n) or ("DEP.EFECTIVO" in n) or ("DEP EFECTIVO" in n):
        return "Depósito en Efectivo"

    # Transferencias
    if ("CR-TRSFE" in n) or ("TRANSF RECIB" in n) or ("TRANLINK" in n) or ("TRANSFERENCIAS RECIBIDAS" in u):
        return "Transferencia de terceros recibida"
    if ("DB-TRSFE" in n) or ("TRSFE-ET" in n) or ("TRSFE-IT" in n):
        return "Transferencia a terceros realizada"
    if ("DTNCTAPR" in n) or ("ENTRE CTA" in n) or ("CTA PROPIA" in n):
        return "Transferencia entre cuentas propias"

    # Negociados
    if ("NEG.CONT" in n) or ("NEGOCIADOS" in n):
        return "Acreditación de valores"

    # Fallbacks genéricos
    if "CREDITO" in u or "CR " in u or "CR-" in u:
        return "Crédito"
    if "DEBITO" in u or "DEB." in u or "DB-" in u:
        return "Débito"

    return "Otros"

# --- Signo (débito / crédito) por tipo + saldo cuando se pueda ---

DEBIT_CLASSES = {
    "LEY 25.413",
    "SIRCREB",
    "Gastos por comisiones",
    "Débito automático",
    "DyC",
    "Débitos ARCA",
    "API",
    "Cuota de préstamo",
    "Cheques 48 hs",
    "Transferencia a terceros realizada",
    "Transferencia entre cuentas propias",
    "Débito",
}

CREDIT_CLASSES = {
    "Acreditación Préstamos",
    "Acreditaciones Tarjetas de Crédito/Débito",
    "Depósito en Efectivo",
    "Transferencia de terceros recibida",
    "Acreditación de valores",
    "Acreditación Plazo Fijo",
    "Crédito",
}

def infer_signo(row, saldo_prev):
    """
    Devuelve 'debito' o 'credito'.
    1) Usa la clasificación (tipo de movimiento).
    2) Si no alcanza, y hay saldo_pdf actual y anterior, usa la variación.
    3) Fallback final: débito.
    """
    cls = row["Clasificación"]
    imp = abs(row["importe"])
    saldo_act = row.get("saldo_pdf", np.nan)

    # 1) Por tipo
    if cls in DEBIT_CLASSES:
        return "debito"
    if cls in CREDIT_CLASSES:
        return "credito"

    # 2) Por movimiento de saldo (cuando ambos están)
    if not np.isnan(saldo_act) and not np.isnan(saldo_prev):
        delta = saldo_act - saldo_prev
        if abs(abs(delta) - imp) < 0.05:  # coincide en módulo
            return "credito" if delta > 0 else "debito"

    # 3) Heurística simple por texto
    u = row["desc_norm"].upper()
    if "CR " in u or "CR-" in u or "ABONO" in u:
        return "credito"
    if "DB-" in u or "DEB." in u:
        return "debito"

    # 4) Fallback
    return "debito"

# --- UI principal --- #

uploaded = st.file_uploader("Subí un PDF del resumen del Banco de Santa Fe", type=["pdf"])
if uploaded is None:
    st.info("La app no almacena datos, toda la información está protegida.")
    st.stop()

data = uploaded.read()
buf = io.BytesIO(data)

with st.spinner("Procesando PDF de Banco de Santa Fe..."):
    df = parse_movimientos_santafe(io.BytesIO(data))

if df.empty:
    st.error("No se detectaron movimientos. Si el PDF tiene un formato distinto, pasame una línea ejemplo (fecha + descripción + importe [+ saldo]).")
    st.stop()

# Saldos
saldo_anterior = find_saldo_anterior_santafe(io.BytesIO(data))
fecha_cierre, saldo_final_pdf = find_saldo_final_santafe(io.BytesIO(data))

# Insertar SALDO ANTERIOR como primera fila, si existe
if not np.isnan(saldo_anterior):
    first_date = df["fecha"].dropna().min()
    fecha_apertura = (first_date - pd.Timedelta(days=1)).normalize() + pd.Timedelta(hours=23, minutes=59, seconds=59) \
                     if pd.notna(first_date) else pd.NaT
    apertura = pd.DataFrame([{
        "fecha": fecha_apertura,
        "descripcion": "SALDO ANTERIOR",
        "desc_norm": "SALDO ANTERIOR",
        "importe": 0.0,
        "saldo_pdf": float(saldo_anterior),
        "pagina": 0,
        "orden": 0
    }])
    df = pd.concat([apertura, df], ignore_index=True)

# Orden
df = df.sort_values(["fecha", "orden"]).reset_index(drop=True)

# Clasificación por texto
df["Clasificación"] = df.apply(
    lambda r: clasificar_sf(str(r.get("descripcion", "")), str(r.get("desc_norm", ""))),
    axis=1
)

# Inferir débito / crédito línea por línea SIN inventar montos
signos = []
saldo_prev = df.loc[0, "saldo_pdf"] if not np.isnan(df.loc[0, "saldo_pdf"]) else np.nan

for idx, row in df.iterrows():
    if idx == 0 and row["desc_norm"] == "SALDO ANTERIOR":
        signos.append("saldo")
        saldo_prev = row.get("saldo_pdf", np.nan)
        continue
    s = infer_signo(row, saldo_prev)
    signos.append(s)
    if not np.isnan(row.get("saldo_pdf", np.nan)):
        saldo_prev = row["saldo_pdf"]

df["signo"] = signos

# Construir débitos / créditos desde importe
df["debito"] = np.where(df["signo"] == "debito", df["importe"].abs(), 0.0)
df["credito"] = np.where(df["signo"] == "credito", df["importe"].abs(), 0.0)
df.loc[df["desc_norm"] == "SALDO ANTERIOR", ["debito", "credito"]] = 0.0

# Saldo calculado (desde saldo anterior)
if not np.isnan(saldo_anterior):
    saldos_calc = [saldo_anterior]
    for i in range(1, len(df)):
        prev = saldos_calc[-1]
        mov = df.loc[i, "credito"] - df.loc[i, "debito"]
        saldos_calc.append(prev + mov)
    df["saldo_calc"] = saldos_calc
else:
    # Si no hay saldo anterior, se arranca en 0
    saldos_calc = [0.0]
    for i in range(1, len(df)):
        prev = saldos_calc[-1]
        mov = df.loc[i, "credito"] - df.loc[i, "debito"]
        saldos_calc.append(prev + mov)
    df["saldo_calc"] = saldos_calc

df_sorted = df.copy()

# Totales / conciliación
saldo_inicial = float(df_sorted["saldo_calc"].iloc[0])
total_debitos = float(df_sorted["debito"].sum())
total_creditos = float(df_sorted["credito"].sum())
saldo_final_calculado = float(df_sorted["saldo_calc"].iloc[-1])

saldo_final_visto = saldo_final_pdf if not np.isnan(saldo_final_pdf) else saldo_final_calculado
diferencia = saldo_final_calculado - saldo_final_visto
cuadra = abs(diferencia) < 0.01

# --- RESUMEN OPERATIVO (ARRIBA) --- #
st.subheader("Resumen Operativo: Registración Módulo IVA")

iva21_mask = df_sorted["Clasificación"].eq("IVA 21% (sobre comisiones)")
iva105_mask = df_sorted["Clasificación"].eq("IVA 10,5% (sobre comisiones)")
iva21 = float(df_sorted.loc[iva21_mask, "debito"].sum())
iva105 = float(df_sorted.loc[iva105_mask, "debito"].sum())
net21 = round(iva21 / 0.21, 2) if iva21 else 0.0
net105 = round(iva105 / 0.105, 2) if iva105 else 0.0

percep_iva = float(df_sorted.loc[df_sorted["Clasificación"].eq("Percepciones de IVA"), "debito"].sum())
ley_25413 = float(df_sorted.loc[df_sorted["Clasificación"].eq("LEY 25.413"), "debito"].sum())
sircreb = float(df_sorted.loc[df_sorted["Clasificación"].eq("SIRCREB"), "debito"].sum())

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Neto Comisiones 21%", f"$ {fmt_ar(net21)}")
with m2:
    st.metric("IVA 21%", f"$ {fmt_ar(iva21)}")
with m3:
    st.metric("Bruto 21%", f"$ {fmt_ar(net21 + iva21)}")

n1, n2, n3 = st.columns(3)
with n1:
    st.metric("Neto Comisiones 10,5%", f"$ {fmt_ar(net105)}")
with n2:
    st.metric("IVA 10,5%", f"$ {fmt_ar(iva105)}")
with n3:
    st.metric("Bruto 10,5%", f"$ {fmt_ar(net105 + iva105)}")

o1, o2, o3 = st.columns(3)
with o1:
    st.metric("Percepciones de IVA", f"$ {fmt_ar(percep_iva)}")
with o2:
    st.metric("Ley 25.413", f"$ {fmt_ar(ley_25413)}")
with o3:
    st.metric("SIRCREB", f"$ {fmt_ar(sircreb)}")

total_operativo = net21 + iva21 + net105 + iva105 + percep_iva + ley_25413 + sircreb
st.metric("Total Resumen Operativo", f"$ {fmt_ar(total_operativo)}")

st.divider()

# --- Resumen del período --- #
st.subheader("Resumen del período")

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Saldo inicial", f"$ {fmt_ar(saldo_inicial)}")
with c2:
    st.metric("Total créditos (+)", f"$ {fmt_ar(total_creditos)}")
with c3:
    st.metric("Total débitos (–)", f"$ {fmt_ar(total_debitos)}")

c4, c5, c6 = st.columns(3)
with c4:
    st.metric("Saldo final (PDF)", f"$ {fmt_ar(saldo_final_visto)}")
with c5:
    st.metric("Saldo final calculado", f"$ {fmt_ar(saldo_final_calculado)}")
with c6:
    st.metric("Diferencia", f"$ {fmt_ar(diferencia)}")

if cuadra:
    st.success("Conciliado.")
else:
    st.error("No cuadra la conciliación (revisar signos/clasificación de algunos movimientos).")

if pd.notna(fecha_cierre):
    st.caption(f"Cierre según PDF: {fecha_cierre.strftime('%d/%m/%Y')}")

st.divider()

# --- Detalle de movimientos (grilla) --- #
st.subheader("Detalle de movimientos")

df_view = df_sorted.copy()
# Mostramos saldo calculado, y dejamos saldo_pdf (si lo hay) a modo informativo
df_view["saldo"] = df_view["saldo_calc"]
df_view = df_view.drop(columns=["saldo_calc"])

for c in ["debito", "credito", "importe", "saldo", "saldo_pdf"]:
    if c in df_view.columns:
        df_view[c] = df_view[c].map(fmt_ar)

styled = df_view.style
st.dataframe(styled, use_container_width=True)

# --- Descargas --- #
st.subheader("Descargar")

try:
    import xlsxwriter
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_sorted.to_excel(writer, index=False, sheet_name="Movimientos")
        wb = writer.book
        ws = writer.sheets["Movimientos"]
        money_fmt = wb.add_format({"num_format": "#,##0.00"})
        date_fmt = wb.add_format({"num_format": "dd/mm/yyyy"})

        for idx, col in enumerate(df_sorted.columns, start=0):
            col_values = df_sorted[col].astype(str)
            max_len = max(len(col), *(len(v) for v in col_values))
            ws.set_column(idx, idx, min(max_len + 2, 40))

        for c in ["debito", "credito", "importe", "saldo_pdf", "saldo_calc"]:
            if c in df_sorted.columns:
                j = df_sorted.columns.get_loc(c)
                ws.set_column(j, j, 16, money_fmt)

        if "fecha" in df_sorted.columns:
            j = df_sorted.columns.get_loc("fecha")
            ws.set_column(j, j, 14, date_fmt)

    st.download_button(
        "📥 Descargar Excel",
        data=output.getvalue(),
        file_name="resumen_bancario_santafe.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
except Exception:
    csv_bytes = df_sorted.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Descargar CSV (fallback)",
        data=csv_bytes,
        file_name="resumen_bancario_santafe.csv",
        mime="text/csv",
        use_container_width=True,
    )

# --- PDF Resumen Operativo --- #
if REPORTLAB_OK:
    try:
        pdf_buf = io.BytesIO()
        doc = SimpleDocTemplate(
            pdf_buf,
            pagesize=A4,
            title="Resumen Operativo - Registración Módulo IVA"
        )
        styles = getSampleStyleSheet()
        elems = []
        elems.append(Paragraph("Resumen Operativo: Registración Módulo IVA", styles["Title"]))
        elems.append(Spacer(1, 8))

        datos = [
            ["Concepto", "Importe"],
            ["Neto Comisiones 21%", fmt_ar(net21)],
            ["IVA 21%", fmt_ar(iva21)],
            ["Bruto 21%", fmt_ar(net21 + iva21)],
            ["Neto Comisiones 10,5%", fmt_ar(net105)],
            ["IVA 10,5%", fmt_ar(iva105)],
            ["Bruto 10,5%", fmt_ar(net105 + iva105)],
            ["Percepciones de IVA", fmt_ar(percep_iva)],
            ["Ley 25.413", fmt_ar(ley_25413)],
            ["SIRCREB", fmt_ar(sircreb)],
            ["TOTAL", fmt_ar(total_operativo)],
        ]

        tbl = Table(datos, colWidths=[300, 120])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ]))
        elems.append(tbl)
        elems.append(Spacer(1, 12))
        elems.append(Paragraph("Herramienta para uso interno - AIE San Justo", styles["Normal"]))

        doc.build(elems)

        st.download_button(
            "📄 Descargar PDF – Resumen Operativo (IVA)",
            data=pdf_buf.getvalue(),
            file_name="Resumen_Operativo_IVA_SantaFe.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.info(f"No se pudo generar el PDF del Resumen Operativo: {e}")
