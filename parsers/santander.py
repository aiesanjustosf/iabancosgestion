import numpy as np, pandas as pd, streamlit as st
from .common import read_lines, parse_movements_by_amount_lines, inject_saldo_anterior, debit_credit_from_delta, render_header, render_totales, fmt, resumen_operativo, MONEY_RE, money_to_float

def find_saldo_anterior(lines):
    for ln in lines:
        if "SALDO ANTERIOR" in ln.upper():
            m = list(MONEY_RE.finditer(ln))
            if m:
                return money_to_float(m[-1].group(0))
    return np.nan

def run(file_like, txt_full):
    render_header("Cuenta Corriente (Santander)","s/n")
    lines = read_lines(file_like)
    df = parse_movements_by_amount_lines(lines)
    saldo_ant = find_saldo_anterior(lines)
    df, saldo_inicial = inject_saldo_anterior(df, saldo_ant)
    df = df.sort_values(["fecha","orden"]).reset_index(drop=True)
    df = debit_credit_from_delta(df)
    tot_deb  = float(df["debito"].sum())
    tot_cre  = float(df["credito"].sum())
    saldo_pdf = float(df["saldo"].iloc[-1]) if not df.empty else 0.0
    saldo_calc = float(df.loc[0,"saldo"]) + tot_cre - tot_deb
    render_totales(saldo_inicial, tot_cre, tot_deb, saldo_pdf, saldo_calc)
    ro = resumen_operativo(df)
    st.caption("Resumen Operativo (simple)")
    c1,c2,c3 = st.columns(3)
    with c1: st.markdown(f"**Neto Comisiones 21%**  \n$ {fmt(ro['net21'])}")
    with c2: st.markdown(f"**IVA 21%**  \n$ {fmt(ro['iva21'])}")
    with c3: st.markdown(f"**Bruto 21%**  \n$ {fmt(ro['net21']+ro['iva21'])}")
    st.caption("Detalle de movimientos")
    df_show = df.rename(columns={"desc":"descripcion","monto_pdf":"importe"})
    st.dataframe(df_show[["fecha","descripcion","debito","credito","importe","saldo"]].style.format({
        "debito": "{:,.2f}".format, "credito":"{:,.2f}".format, "importe":"{:,.2f}".format, "saldo":"{:,.2f}".format
    }, na_rep="—"), use_container_width=True)
