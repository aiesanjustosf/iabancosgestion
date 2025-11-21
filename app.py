# ia_resumen_bancario_santafe.py
# Herramienta para uso interno - AIE San Justo (Banco de Santa Fe)

import io
import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# ===========================
#   UI / ASSETS
# ===========================
HERE = Path(__file__).parent
ASSETS = HERE / "assets"
LOGO = ASSETS / "logo_aie.png"
FAVICON = ASSETS / "favicon-aie.ico"

st.set_page_config(
    page_title="IA Resumen Bancario – Banco de Santa Fe",
    page_icon=str(FAVICON) if FAVICON.exists() else None,
    layout="wide",
)

if LOGO.exists():
    st.image(str(LOGO), width=200)

st.title("IA Resumen Bancario – Banco de Santa Fe")

# ===========================
#   DEPENDENCIAS DIFERIDAS
# ===========================
try:
    import pdfplumber
except Exception as e:
    st.error(f"No se pudo importar pdfplumber: {e}\nRevisá requirements.txt")
    st.stop()

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

# ===========================
#   REGEX BÁSICOS
# ===========================
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")

# Montos estilo argentino: 1.234,56 / -1.234,56 / 1.234,56-
MONEY_RE = re.compile(
    r'(?<!\S)-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?(?!\S)'
)

LONG_INT_RE = re.compile(r"\b\d{6,}\b")


# ===========================
#   HELPERS GENERALES
# ===========================
def normalize_money(tok: str) -> float:
    """Convierte '1.234,56-' o '-1.234,56' en float con signo."""
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


def lines_from_words(page, ytol=2.0):
    words = page.extract_words(extra_attrs=["x0", "top"])
    if not words:
        return []
    words.sort(key=lambda w: (round(w["top"] / ytol), w["x0"]))
    lines, cur, band = [], [], None
    for w in words:
        b = round(w["top"] / ytol)
        if band is None or b == band:
            cur.append(w)
        else:
            lines.append(" ".join(x["text"] for x in cur))
            cur = [w]
        band = b
    if cur:
        lines.append(" ".join(x["text"] for x in cur))
    return [" ".join(l.split()) for l in lines]


def normalize_desc(desc: str) -> str:
    if not desc:
        return ""
    u = desc.upper()
    for pref in (
        "SAN JUS ",
        "CASA RO ",
        "CENTRAL ",
        "GOBERNA ",
        "GOBERNADOR ",
        "SANTA FE ",
        "ROSARIO ",
    ):
        if u.startswith(pref):
            u = u[len(pref) :]
            break
    u = LONG_INT_RE.sub("", u)
    u = " ".join(u.split())
    return u


def extract_all_lines(file_like):
    out = []
    with pdfplumber.open(file_like) as pdf:
        for pi, p in enumerate(pdf.pages, start=1):
            lt = lines_from_text(p)
            lw = lines_from_words(p, ytol=2.0)
            seen = set(lt)
            combined = lt + [l for l in lw if l not in seen]
            for l in combined:
                if l.strip():
                    out.append((pi, " ".join(l.split())))
    return out


# ===========================
#   SALDOS EN TEXTO
# ===========================
def find_saldo_anterior(lines):
    """
    Busca SALDO ANTERIOR o SALDO ULTIMO RESUMEN en cualquier formato de Santa Fe.
    """
    for _, ln in lines:
        u = ln.upper()
        if (
            "SALDO ANTERIOR" in u
            or "SALDO ULTIMO RESUMEN" in u
            or "SALDO ÚLTIMO RESUMEN" in u
        ):
            am = list(MONEY_RE.finditer(ln))
            if am:
                return normalize_money(am[-1].group(0))
    return np.nan


def find_saldo_final_pdf(lines):
    """
    Busca 'SALDO AL' o 'SALDO FINAL' en el texto.
    Si no lo encuentra, se devolverá NaN y usaremos el último saldo calculado.
    """
    for _, ln in reversed(lines):
        u = ln.upper()
        if "SALDO AL" in u or "SALDO FINAL" in u:
            am = list(MONEY_RE.finditer(ln))
            if am:
                return normalize_money(am[-1].group(0))
    return np.nan


# ===========================
#   DETECCIÓN DE SIGNO (solo formato SIN saldo por línea)
# ===========================
def detectar_signo_santafe(desc_norm: str) -> str:
    """
    Devuelve 'debito' o 'credito' usando reglas específicas de Banco de Santa Fe.
    IMPORTANTE: se usa solo cuando el PDF NO trae saldo en cada línea.
    """
    u = (desc_norm or "").upper()

    # Créditos claros (acreditaciones)
    if any(
        k in u
        for k in (
            "DTNPROVE",
            "CR-DEPEF",
            "CR DEPEF",
            "CRDEPEF",
            "DEPOSITO EFECTIVO",
            "DEP EFEC",
            "DEP.EFECT",
            "DEP EFECTIVO",
            "CR-DEPEF DEPOSITO",
        )
    ):
        return "credito"

    if any(
        k in u
        for k in (
            "CR-TRSFE",
            "CR TRSFE",
            "TRANSF RECIB",
            "TRANLINK",
            "TRANSFERENCIAS RECIBIDAS",
            "CR TRANSF",
        )
    ):
        return "credito"

    # Intereses a favor
    if any(k in u for k in ("INT CCSA", "INT CCA", "INT.CC", "INTERESES", "INTERES")):
        return "credito"

    # En Santa Fe casi todo lo demás es débito
    if u.startswith("CR ") or u.startswith("CR-"):
        return "credito"

    return "debito"


# ===========================
#   CLASIFICACIÓN CONCEPTOS
# ===========================
RE_PERCEP_RG2408 = re.compile(r"PERCEPCI[ÓO]N\s+IVA\s+RG\.?\s*2408", re.IGNORECASE)


def clasificar(desc: str, desc_norm: str, deb: float, cre: float) -> str:
    u = (desc or "").upper()
    n = (desc_norm or "").upper()

    # Saldos
    if "SALDO ANTERIOR" in u or "SALDO ANTERIOR" in n:
        return "SALDO ANTERIOR"

    # Ley 25.413 – impuesto al débito/crédito bancario
    if (
        ("LEY 25413" in u)
        or ("IMPTRANS" in u)
        or ("IMP.S/CREDS" in u)
        or ("IMPDBCR 25413" in u)
        or ("N/D DBCR 25413" in u)
        or ("LEY 25413" in n)
        or ("IMPTRANS" in n)
        or ("IMP.S/CREDS" in n)
        or ("IMPDBCR 25413" in n)
        or ("N/D DBCR 25413" in n)
    ):
        return "LEY 25.413"

    # SIRCREB
    if "SIRCREB" in u or "SIRCREB" in n:
        return "SIRCREB"

    # Percepciones IVA – RG 2408 explícita
    if RE_PERCEP_RG2408.search(u) or RE_PERCEP_RG2408.search(n):
        return "Percepciones de IVA"

    # Percepciones / retenciones IVA (RG 3337 / RG 2408)
    if (
        ("IVA PERC" in u)
        or ("IVA PERCEP" in u)
        or ("RG3337" in u)
        or ("IVA PERC" in n)
        or ("IVA PERCEP" in n)
        or ("RG3337" in n)
        or (
            ("RETEN" in u or "RETENC" in u)
            and ("I.V.A" in u or "IVA" in u)
            and ("RG 2408" in u or "RG.2408" in u or "RG2408" in u)
        )
        or (
            ("RETEN" in n or "RETENC" in n)
            and ("I.V.A" in n or "IVA" in n)
            and ("RG 2408" in n or "RG.2408" in n or "RG2408" in n)
        )
    ):
        return "Percepciones de IVA"

    # Otras percepciones IVA genéricas
    if (
        ("RETENCION" in u and "IVA" in u and "PERCEP" in u)
        or ("RETENCION" in n and "IVA" in n and "PERCEP" in n)
        or ("RETEN" in u and "IVA" in u and "PERC" in u)
        or ("RETEN" in n and "IVA" in n and "PERC" in n)
    ):
        return "Percepciones de IVA"

    # IVA sobre comisiones
    if ("IVA GRAL" in u) or ("IVA GRAL" in n):
        return "IVA 21% (sobre comisiones)"
    if ("IVA RINS" in u and "REDUC" in u) or ("IVA RINS" in n and "REDUC" in n):
        return "IVA 10,5% (sobre comisiones)"

    # Comisiones varias
    if (
        "COMIS.TRANSF" in u
        or "COMIS.TRANSF" in n
        or "COMIS TRANSF" in u
        or "COMIS TRANSF" in n
        or "COMIS.COMPENSACION" in u
        or "COMIS.COMPENSACION" in n
        or "COMIS COMPENSACION" in u
        or "COMIS COMPENSACION" in n
    ):
        return "Gastos por comisiones"

    if (
        "MANTENIMIENTO MENSUAL PAQUETE" in u
        or "MANTENIMIENTO MENSUAL PAQUETE" in n
        or "COMOPREM" in n
        or "COMVCAUT" in n
        or "COMTRSIT" in n
        or "COM.NEGO" in n
        or "CO.EXCESO" in n
        or "COM." in n
    ):
        return "Gastos por comisiones"

    # Débitos automáticos / seguros
    if (
        "DB-SNP" in n
        or "DEB.AUT" in n
        or "DEB.AUTOM" in n
        or "SEGUROS" in n
        or "GTOS SEG" in n
        or "DEBITO INMEDIATO" in u
        or "DEBITO INMEDIATO" in n
        or "DEBIN" in u
        or "DEBIN" in n
    ):
        return "Débito automático"

    # Varias fiscales
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

    # Cheques 48 hs
    if "CH 48 HS" in n or "CH.48 HS" in n:
        return "Cheques 48 hs"

    # Créditos específicos
    if ("PAGO COMERC" in n) or ("CR-CABAL" in n) or ("CR CABAL" in n) or ("CR TARJ" in n):
        return "Acreditaciones Tarjetas de Crédito/Débito"

    # Depósitos en efectivo
    if (
        "CR-DEPEF" in n
        or "CR DEPEF" in n
        or "DEPOSITO EFECTIVO" in n
        or "DEP.EFECTIVO" in n
        or "DEP EFECTIVO" in n
    ):
        return "Depósito en Efectivo"

    # Transferencias
    if (
        "CR-TRSFE" in n
        or "TRANSF RECIB" in n
        or "TRANLINK" in n
        or "TRANSFERENCIAS RECIBIDAS" in u
    ) and cre and cre != 0:
        return "Transferencia de terceros recibida"

    if ("DB-TRSFE" in n or "TRSFE-ET" in n or "TRSFE-IT" in n) and deb and deb != 0:
        return "Transferencia a terceros realizada"

    if "DTNCTAPR" in n or "ENTRE CTA" in n or "CTA PROPIA" in n:
        return "Transferencia entre cuentas propias"

    # Negociados
    if "NEG.CONT" in n or "NEGOCIADOS" in n:
        return "Acreditación de valores"

    # Fallback por signo
    if cre and cre != 0:
        return "Crédito"
    if deb and deb != 0:
        return "Débito"
    return "Otros"


# ===========================
#   PARSEO MOVIMIENTOS SANTA FE
# ===========================
def parse_movimientos_santafe(lines) -> pd.DataFrame:
    """
    Devuelve DF con:
    fecha, descripcion, desc_norm, importe_raw, saldo_pdf, mcount, pagina, orden
    (sin saldos ni débitos/créditos todavía)
    """
    rows = []
    orden = 0

    for pageno, ln in lines:
        u = ln.upper()

        # saltar encabezados / títulos
        if "FECHA MOVIMIENTO" in u or "MOVIMIENTOS DETALLADO" in u:
            continue
        if "CONCEPTO" in u and "DEBITO" in u and "CREDITO" in u:
            continue

        # ignorar líneas de SALDO ANTERIOR / SALDO ULTIMO RESUMEN,
        # se toman aparte
        if (
            "SALDO ANTERIOR" in u
            or "SALDO ULTIMO RESUMEN" in u
            or "SALDO ÚLTIMO RESUMEN" in u
        ):
            continue

        d = DATE_RE.search(ln)
        if not d:
            continue

        am = list(MONEY_RE.finditer(ln))
        if not am:
            continue

        mcount = len(am)
        if mcount >= 2:
            importe_str = am[-2].group(0)
            saldo_str = am[-1].group(0)
            saldo_pdf = normalize_money(saldo_str)
        else:
            importe_str = am[-1].group(0)
            saldo_pdf = np.nan

        importe = normalize_money(importe_str)
        first_money = am[0]
        desc = ln[d.end() : first_money.start()].strip()

        orden += 1
        rows.append(
            {
                "fecha": pd.to_datetime(
                    d.group(0), dayfirst=True, errors="coerce"
                ),
                "descripcion": desc,
                "desc_norm": normalize_desc(desc),
                "importe_raw": abs(importe),
                "saldo_pdf": saldo_pdf,
                "mcount": mcount,
                "pagina": pageno,
                "orden": orden,
            }
        )

    return pd.DataFrame(rows)


# ===========================
#   RENDER PRINCIPAL
# ===========================
uploaded = st.file_uploader(
    "Subí un PDF del resumen bancario (Banco de Santa Fe)", type=["pdf"]
)
if uploaded is None:
    st.info("La app no almacena datos, toda la información está protegida.")
    st.stop()

data = uploaded.read()

with st.spinner("Procesando PDF..."):
    lines = extract_all_lines(io.BytesIO(data))
    if not lines:
        st.error(
            "No se pudo leer texto del PDF. Verificá que no esté escaneado solo como imagen."
        )
        st.stop()

    df_raw = parse_movimientos_santafe(lines)

if df_raw.empty:
    st.error(
        "No se detectaron movimientos. "
        "Si el PDF tiene un formato muy distinto, pasame una línea ejemplo (fecha + descripción + importe)."
    )
    st.stop()

# Detectar formato: con saldo por línea vs sin saldo
tiene_saldo_por_linea = df_raw["mcount"].max() >= 2

if tiene_saldo_por_linea:
    formato_label = "DETALLADO (con saldo por línea)"
else:
    formato_label = "DETALLADO (sin saldo por línea)"

st.success(f"Formato detectado: {formato_label}")

# Saldos en texto
saldo_anterior = find_saldo_anterior(lines)
saldo_final_pdf = find_saldo_final_pdf(lines)

# ===========================
#   ARMAR DF COMPLETO
# ===========================
# Orden básico
df = df_raw.sort_values(["fecha", "pagina", "orden"]).reset_index(drop=True)

# Insertar SALDO ANTERIOR como primera fila si existe
apertura_rows = []
if not np.isnan(saldo_anterior):
    first_date = df["fecha"].dropna().min()
    fecha_apertura = (
        (first_date - pd.Timedelta(days=1)).normalize()
        + pd.Timedelta(hours=23, minutes=59, seconds=59)
        if pd.notna(first_date)
        else pd.NaT
    )
    apertura_rows.append(
        {
            "fecha": fecha_apertura,
            "descripcion": "SALDO ANTERIOR",
            "desc_norm": "SALDO ANTERIOR",
            "importe_raw": 0.0,
            "saldo_pdf": float(saldo_anterior) if tiene_saldo_por_linea else np.nan,
            "mcount": 0,
            "pagina": 0,
            "orden": 0,
        }
    )

if apertura_rows:
    df = pd.concat([pd.DataFrame(apertura_rows), df], ignore_index=True)

# Crear columnas definitivas
df["debito"] = 0.0
df["credito"] = 0.0
df["saldo"] = np.nan
df["signo"] = ""

# ---------- Caso 1: PDF con SALDO por línea ----------
if tiene_saldo_por_linea:
    # saldo: usar saldo_pdf + ffill (SALDO ANTERIOR ya viene con saldo)
    df["saldo"] = df["saldo_pdf"]
    df["saldo"] = df["saldo"].ffill()

    # delta respecto a la fila anterior → movimiento del día
    delta = df["saldo"] - df["saldo"].shift(1)

    df.loc[delta < 0, "debito"] = -delta[delta < 0]
    df.loc[delta > 0, "credito"] = delta[delta > 0]
    df["debito"] = df["debito"].fillna(0.0)
    df["credito"] = df["credito"].fillna(0.0)
    df.loc[0, ["debito", "credito"]] = 0.0  # SALDO ANTERIOR
    df["signo"] = np.where(df["debito"] > 0, "debito", "")
    df.loc[df["credito"] > 0, "signo"] = "credito"

# ---------- Caso 2: PDF SIN saldo por línea ----------
else:
    # Recorremos fila a fila construyendo saldo
    saldos = []
    debitos = []
    creditos = []
    signos = []

    # saldo inicial
    saldo = float(saldo_anterior) if not np.isnan(saldo_anterior) else 0.0

    for idx, row in df.iterrows():
        if idx == 0 and row["desc_norm"] == "SALDO ANTERIOR":
            # Fila de apertura
            saldos.append(saldo)
            debitos.append(0.0)
            creditos.append(0.0)
            signos.append("saldo")
            continue

        desc_norm = row["desc_norm"]
        importe = float(row["importe_raw"])

        signo = detectar_signo_santafe(desc_norm)
        if signo == "debito":
            saldo -= importe
            deb = importe
            cre = 0.0
        else:
            saldo += importe
            deb = 0.0
            cre = importe

        saldos.append(saldo)
        debitos.append(deb)
        creditos.append(cre)
        signos.append(signo)

    df["saldo"] = saldos
    df["debito"] = debitos
    df["credito"] = creditos
    df["signo"] = signos

# ===========================
#   CLASIFICACIÓN
# ===========================
df["Clasificación"] = df.apply(
    lambda r: clasificar(
        str(r.get("descripcion", "")),
        str(r.get("desc_norm", "")),
        float(r.get("debito", 0.0)),
        float(r.get("credito", 0.0)),
    ),
    axis=1,
)

# ===========================
#   RESUMEN / CONCILIACIÓN
# ===========================
df_sorted = df.reset_index(drop=True)

# Saldo inicial = primera fila
saldo_inicial = float(df_sorted["saldo"].iloc[0])

total_debitos = float(df_sorted["debito"].sum())
total_creditos = float(df_sorted["credito"].sum())

# Saldo final visto
if not np.isnan(saldo_final_pdf):
    saldo_final_visto = float(saldo_final_pdf)
else:
    # Si el PDF no trae saldo final explícito, usamos el último saldo calculado
    saldo_final_visto = float(df_sorted["saldo"].iloc[-1])

saldo_final_calculado = saldo_inicial + total_creditos - total_debitos
diferencia = saldo_final_calculado - saldo_final_visto
cuadra = abs(diferencia) < 0.01

# ===========================
#   CABECERA RESUMEN
# ===========================
st.subheader("Resumen del período")

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Saldo inicial", f"$ {fmt_ar(saldo_inicial)}")
with c2:
    st.metric("Créditos (+)", f"$ {fmt_ar(total_creditos)}")
with c3:
    st.metric("Débitos (–)", f"$ {fmt_ar(total_debitos)}")

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

if not np.isnan(saldo_final_pdf):
    # Intento de fecha de cierre (si la línea de saldo final tiene fecha).
    st.caption("Cierre según PDF: saldo final leído del documento.")

st.markdown("---")

# ===========================
#   RESUMEN OPERATIVO (ARRIBA DE LA GRILLA)
# ===========================
st.subheader("Resumen Operativo: Registración Módulo IVA")

iva21_mask = df_sorted["Clasificación"].eq("IVA 21% (sobre comisiones)")
iva105_mask = df_sorted["Clasificación"].eq("IVA 10,5% (sobre comisiones)")

iva21 = float(df_sorted.loc[iva21_mask, "debito"].sum())
iva105 = float(df_sorted.loc[iva105_mask, "debito"].sum())

net21 = round(iva21 / 0.21, 2) if iva21 else 0.0
net105 = round(iva105 / 0.105, 2) if iva105 else 0.0

percep_iva = float(
    df_sorted.loc[df_sorted["Clasificación"].eq("Percepciones de IVA"), "debito"].sum()
)
ley_25413 = float(
    df_sorted.loc[df_sorted["Clasificación"].eq("LEY 25.413"), "debito"].sum()
)
sircreb = float(
    df_sorted.loc[df_sorted["Clasificación"].eq("SIRCREB"), "debito"].sum()
)

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

st.markdown("---")

# ===========================
#   DETALLE DE MOVIMIENTOS
# ===========================
st.subheader("Detalle de movimientos")

df_view = df_sorted.copy()
for c in ("debito", "credito", "saldo"):
    df_view[c] = df_view[c].map(fmt_ar)

st.dataframe(df_view, use_container_width=True)

# ===========================
#   DESCARGAS
# ===========================
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

# ===========================
#   PDF RESUMEN OPERATIVO
# ===========================
if REPORTLAB_OK:
    try:
        pdf_buf = io.BytesIO()
        doc = SimpleDocTemplate(
            pdf_buf,
            pagesize=A4,
            title="Resumen Operativo - Registración Módulo IVA",
        )
        styles = getSampleStyleSheet()
        elems = [
            Paragraph(
                "Resumen Operativo: Registración Módulo IVA", styles["Title"]
            ),
            Spacer(1, 8),
        ]

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
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ]
            )
        )
        elems.append(tbl)
        elems.append(Spacer(1, 12))
        elems.append(
            Paragraph(
                "Herramienta para uso interno - AIE San Justo",
                styles["Normal"],
            )
        )

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
else:
    st.caption(
        "Para descargar el PDF del Resumen Operativo instalá reportlab en requirements.txt"
    )
