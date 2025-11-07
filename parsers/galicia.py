import re, numpy as np, pandas as pd, streamlit as st, io
from .common import read_lines, parse_movements_by_amount_lines, debit_credit_from_monto, inject_saldo_anterior, resumen_operativo, render_header, render_totales, money_to_float, fmt

GAL_SALDO_INICIAL_RE = re.compile(r"SALDO\s+INICIAL.*?(-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?)", re.I)
GAL_SALDO_FINAL_RE   = re.compile(r"SALDO\s+FINAL.*?(-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?)", re.I)

def header_saldos_from_text(txt: str):
    ini = fin = np.nan
    m1 = GAL_SALDO_INICIAL_RE.search(txt or "");  m2 = GAL_SALDO_FINAL_RE.search(txt or "")
    if m1: ini = money_to_float(m1.group(1))
    if m2: fin = money_to_float(m2.group(1))
    return ini, fin

def run(file_like, txt_full):
    render_header("Cuenta Corriente (Galicia)", "s/n")
    lines = read_lines(file_like)
    df    = parse_movements_by_amount_lines(lines)
    ini_hdr, fin_hdr = header_saldos_from_text(txt_full)
    df = debit_credit_from_monto(df)
    saldo_inicial = ini_hdr
    if (np.isnan(saldo_inicial) and not df.empty and pd.notna(df.loc[0,"saldo"]) and pd.notna(df.loc[0,"monto_pdf"])):
        m0 = float(df.loc[0,"monto_pdf"]); s0 = float(df.loc[0,"saldo"])
        saldo_inicial = s0 - m0
    df, saldo_inicial = inject_saldo_anterior(df, saldo_inicial)
    tot_deb  = float(df["debito"].sum())
    tot_cre  = float(df["credito"].sum())
    saldo_pdf = float(fin_hdr) if not np.isnan(fin_hdr) else (float(df["saldo"].iloc[-1]) if not df.empty else 0.0)
    saldo_calc = float(df.loc[0,"saldo"]) + tot_cre - tot_deb
    render_totales(saldo_inicial, tot_cre, tot_deb, saldo_pdf, saldo_calc)
    ro = resumen_operativo(df)
    c1,c2,c3 = st.columns(3)
    with c1: st.markdown(f"**Neto Comisiones 21%**  \n$ {fmt(ro['net21'])}")
    with c2: st.markdown(f"**IVA 21%**  \n$ {fmt(ro['iva21'])}")
    with c3: st.markdown(f"**Bruto 21%**  \n$ {fmt(ro['net21']+ro['iva21'])}")
    c4,c5,c6 = st.columns(3)
    with c4: st.markdown(f"**Percepciones de IVA (RG 3337 / RG 2408)**  \n$ {fmt(ro['percep_iva'])}")
    with c5: st.markdown(f"**Ley 25.413 (neto)**  \n$ {fmt(ro['ley25413'])}")
    with c6: st.markdown(f"**SIRCREB**  \n$ {fmt(ro['sircreb'])}")
    st.caption("Detalle de movimientos")
    df_show = df.rename(columns={"desc":"descripcion","monto_pdf":"importe"})
    st.dataframe(df_show[["fecha","descripcion","debito","credito","importe","saldo"]].style.format({
        "debito": "{:,.2f}".format, "credito":"{:,.2f}".format, "importe":"{:,.2f}".format, "saldo":"{:,.2f}".format
    }, na_rep="—"), use_container_width=True)
