import streamlit as st
import pdfplumber
import pandas as pd

st.set_page_config(page_title="IA Bancos Gestión", layout="wide")
st.title("IA Bancos Gestión – Smoke Test")

f = st.file_uploader("Subí un PDF (smoke test)", type=["pdf"])
if f is not None:
    with pdfplumber.open(f) as pdf:
        pages = len(pdf.pages)
    st.success(f"PDF abierto OK. Páginas: {pages}")
    df = pd.DataFrame({"ok": [True], "pages": [pages]})
    st.dataframe(df, use_container_width=True)

st.caption("Si este smoke test levanta, los requirements están bien. Luego metemos los parsers.")
