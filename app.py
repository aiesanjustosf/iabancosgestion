import io, re
import streamlit as st
import pdfplumber

from parsers.dispatch import detect_bank, run_parser

st.set_page_config(page_title="IA Bancos Gestión", layout="wide")
st.title("IA Bancos Gestión – Smoke Test + Dispatcher")

pdf = st.file_uploader("Subí un PDF bancario", type=["pdf"])
force = st.selectbox(
    "Forzar identificación (opcional)",
    ["Auto (detectar)", "Banco Galicia", "Banco de la Nación Argentina", "Banco de Santa Fe", "Banco Macro", "Banco Santander"],
)

if pdf:
    data = pdf.read()

    # 1) abrir rápido para validar
    try:
        with pdfplumber.open(io.BytesIO(data)) as p:
            st.success(f"PDF abierto OK. Páginas: {len(p.pages)}")
    except Exception as e:
        st.error(f"Error abriendo PDF: {e}")
        st.stop()

    # 2) leer texto crudo para detección
    raw_text = []
    with pdfplumber.open(io.BytesIO(data)) as p:
        for i, page in enumerate(p.pages):
            try:
                raw_text.append(page.extract_text() or "")
            except Exception:
                raw_text.append("")
    full_text = "\n".join(raw_text)

    # 3) detectar
    bank = detect_bank(full_text) if force == "Auto (detectar)" else force
    st.info(f"Detectado: {bank}")

    # 4) parsear vía dispatcher
    try:
        run_parser(bank, data, full_text)   # los módulos pintan en pantallas (st.*)
    except Exception as e:
        st.error(f"Error en parser de {bank}: {e}")
else:
    st.info("Subí un PDF para continuar…")
