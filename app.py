import streamlit as st
import pdfplumber

st.set_page_config(page_title="IA Bancos Gestión – Smoke Test", layout="wide")
st.title("IA Bancos Gestión – Smoke Test")

pdf = st.file_uploader("Subí un PDF bancario", type=["pdf"])
if pdf:
    try:
        with pdfplumber.open(pdf) as p:
            st.success(f"PDF abierto OK. Páginas: {len(p.pages)}")
    except Exception as e:
        st.error(f"Error abriendo PDF: {e}")
else:
    st.info("Subí un PDF para probar que el entorno funciona.")
