import io
import re
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import streamlit as st

from parsers.detect import detect_bank
from parsers.galicia import parse_galicia
from parsers.generico import parse_generico


# ---------- Utilidades comunes ----------
HERE = Path(__file__).parent
LOGO = HERE / "logo_aie.png"

st.set_page_config(page_title="IA Bancos Gestión", page_icon="💼", layout="wide")

if LOGO.exists():
    st.image(str(LOGO), width=180)
st.title("IA Bancos Gestión")

st.caption("Subí un PDF bancario. La app detecta el banco y aplica el parser correcto.")

up = st.file_uploader("Subí un PDF bancario", type=["pdf"])
with st.expander("Forzar identificación (opcional)", expanded=False):
    force_bank = st.selectbox(
        "Forzar identificación del banco",
        ["Auto (detectar)", "Banco Galicia", "Banco de la Nación Argentina", "Banco de Santa Fe", "Banco Macro", "Banco Santander"],
        index=0,
    )

def money_fmt(x: Decimal | float | int | None) -> str:
    if x is None:
        return "$ 0,00"
    if not isinstance(x, Decimal):
        x = Decimal(str(x))
    x = x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if x < 0:
        return f"$ -{str(s).replace('-', '')}"
    return f"$ {s}"

def show_header(res):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Saldo inicial", money_fmt(res["saldo_inicial"]))
    with col2:
        st.metric("Total créditos (+)", money_fmt(res["total_creditos"]))
    with col3:
        st.metric("Total débitos (−)", money_fmt(res["total_debitos"]))
    with col4:
        st.metric("Saldo final (PDF)", money_fmt(res["saldo_pdf"]))
    cuadra = (res["saldo_inicial"] + res["total_creditos"] - res["total_debitos"]).quantize(Decimal("0.01")) == res["saldo_pdf"].quantize(Decimal("0.01"))
    st.success("Conciliado.") if cuadra else st.error("No cuadra la conciliación.")
    return cuadra

def show_operativo(op):
    st.subheader("Resumen Operativo: Registración Módulo IVA")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.write("**Neto Comisiones 21%**")
        st.write(money_fmt(op.get("neto_21")))
        st.write("**Neto Comisiones 10,5%**")
        st.write(money_fmt(op.get("neto_105")))
        st.write("**Percepciones IVA (RG 3337 / RG 2408)**")
        st.write(money_fmt(op.get("perc_iva")))
    with c2:
        st.write("**IVA 21%**")
        st.write(money_fmt(op.get("iva_21")))
        st.write("**IVA 10,5%**")
        st.write(money_fmt(op.get("iva_105")))
        st.write("**Ley 25.413 (neto)**")
        st.write(money_fmt(op.get("ley_25413")))
    with c3:
        st.write("**Bruto 21%**")
        st.write(money_fmt(op.get("bruto_21")))
        st.write("**Bruto 10,5%**")
        st.write(money_fmt(op.get("bruto_105")))
        st.write("**SIRCREB**")
        st.write(money_fmt(op.get("sircreb")))
    total = sum([
        op.get("neto_21", Decimal(0)),
        op.get("neto_105", Decimal(0)),
        op.get("perc_iva", Decimal(0)),
        op.get("iva_21", Decimal(0)),
        op.get("iva_105", Decimal(0)),
        op.get("ley_25413", Decimal(0)),
        op.get("bruto_21", Decimal(0)),
        op.get("bruto_105", Decimal(0)),
        op.get("sircreb", Decimal(0)),
    ])
    st.write("**Total Resumen Operativo**")
    st.write(money_fmt(total))


if up is None:
    st.info("📄 Esperando un PDF…")
    st.stop()

data = up.read()
try:
    bank_auto = detect_bank(data)
except Exception:
    bank_auto = "Desconocido"

_bank_name = bank_auto if force_bank == "Auto (detectar)" else force_bank
st.success(f"Detectado: {_bank_name}") if _bank_name != "Desconocido" else st.warning("No se pudo detectar el banco")

# ---------- Ruteo por banco ----------
try:
    if _bank_name == "Banco Galicia":
        res = parse_galicia(data)
    else:
        res = parse_generico(data, banco=_bank_name)
except Exception as e:
    st.exception(e)
    st.stop()

st.subheader(f'CUENTA ({res["banco"]}) · {res.get("titulo","Parser")}')

# Header / conciliación
cuadra = show_header(res)

# Operativo
show_operativo(res.get("operativo", {}))

# Movimientos
st.subheader("Detalle de movimientos")
st.dataframe(res["movimientos"])
