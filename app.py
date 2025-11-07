import io
import streamlit as st
from pathlib import Path

def pdf_text(file_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""

def detectar_banco(txt: str) -> str:
    U = (txt or "").upper()
    score = {
        "galicia":   sum(k in U for k in ["BANCO GALICIA","IMP. DEB./CRE. LEY 25413","SIRCREB","TRANSFERENCIA DE TERCEROS"]),
        "santafe":   sum(k in U for k in ["BANCO DE SANTA FE","NUEVO BANCO DE SANTA FE","SALDO ANTERIOR","IMPTRANS"]),
        "macro":     sum(k in U for k in ["BANCO MACRO","CUENTA CORRIENTE BANCARIA","SALDO ULTIMO EXTRACTO AL"]),
        "nacion":    sum(k in U for k in ["BANCO DE LA NACION ARGENTINA","PERIODO:","I.V.A. BASE"]),
        "santander": sum(k in U for k in ["BANCO SANTANDER","GIROS/TRANSFERENCIAS","SERVICIOS"]),
    }
    best = max(score.items(), key=lambda x: x[1])
    return best[0] if best[1] > 0 else "desconocido"

HERE = Path(__file__).parent
LOGO = HERE / "logo_aie.png"
FAVICON = HERE / "favicon-aie.ico"
st.set_page_config(page_title="IA Resumen Bancario", page_icon=str(FAVICON) if FAVICON.exists() else None)
if LOGO.exists():
    st.image(str(LOGO), width=200)
st.title("IA Resumen Bancario")

uploaded = st.file_uploader("Subí un PDF del resumen bancario", type=["pdf"])
if not uploaded:
    st.info("La app no almacena datos. Subí un PDF para procesar.")
    st.stop()

data = uploaded.read()
txt  = pdf_text(data)

with st.expander("Opciones avanzadas (detección de banco)", expanded=False):
    forced = st.selectbox(
        "Forzar identificación del banco",
        options=("Auto (detectar)","Banco de Santa Fe","Banco Macro","Banco de la Nación Argentina","Banco Santander","Banco Galicia"),
        index=0
    )

auto = detectar_banco(txt)
if forced != "Auto (detectar)":
    map_forced = {
        "Banco de Santa Fe":"santafe",
        "Banco Macro":"macro",
        "Banco de la Nación Argentina":"nacion",
        "Banco Santander":"santander",
        "Banco Galicia":"galicia",
    }
    banco = map_forced[forced]
else:
    banco = auto

tags = {
    "galicia":"Banco Galicia",
    "santafe":"Banco de Santa Fe",
    "macro":"Banco Macro",
    "nacion":"Banco de la Nación Argentina",
    "santander":"Banco Santander",
    "desconocido":"Banco no identificado",
}
status = "success" if banco in tags and banco!="desconocido" else "warning"
getattr(st, status)(f"Detectado: {tags[banco]}")

try:
    if banco == "galicia":
        import parsers.galicia as gal
        gal.run(io.BytesIO(data), txt)
    elif banco == "santafe":
        import parsers.santafe as sfe
        sfe.run(io.BytesIO(data), txt)
    elif banco == "macro":
        import parsers.macro as mac
        mac.run(io.BytesIO(data), txt)
    elif banco == "nacion":
        import parsers.nacion as bna
        bna.run(io.BytesIO(data), txt)
    elif banco == "santander":
        import parsers.santander as san
        san.run(io.BytesIO(data), txt)
    else:
        st.error("No pude identificar el banco. Probá forzar la selección arriba.")
except Exception as e:
    st.error(f"Fallo interno del módulo ({banco}). {e}")
