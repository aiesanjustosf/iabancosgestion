import re, numpy as np, pandas as pd, streamlit as st

DATE_RE  = re.compile(r"\b\d{1,2}/\d{2}/\d{2,4}\b")
MONEY_RE = re.compile(r'(?<!\S)-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?(?!\S)')

def money_to_float(tok: str) -> float:
    if not tok: return np.nan
    s = tok.strip()
    neg = s.endswith("-") or s.startswith("-")
    s = s.lstrip("-").rstrip("-")
    if "," not in s: return np.nan
    a,b = s.rsplit(",",1)
    try:
        val = float(a.replace(".","").replace(" ","") + "." + b)
        return -val if neg else val
    except: return np.nan

def fmt(n): 
    return "—" if n is None or (isinstance(n,float) and np.isnan(n)) else f"{n:,.2f}".replace(",", "§").replace(".", ",").replace("§",".")

def read_lines(file_like):
    try:
        import pdfplumber, io
        out=[]
        with pdfplumber.open(file_like) as pdf:
            for p in pdf.pages:
                t = p.extract_text() or ""
                out.extend([" ".join(l.split()) for l in t.splitlines() if l.strip()])
        return out
    except Exception:
        return []

def parse_movements_by_amount_lines(lines):
    rows=[]; seq=0
    for ln in lines:
        mny = list(MONEY_RE.finditer(ln))
        if len(mny) < 2: 
            continue
        d = DATE_RE.search(ln)
        if not d or d.end() >= mny[0].start():
            continue
        from datetime import datetime
        import pandas as pd
        saldo = money_to_float(mny[-1].group(0))
        monto = money_to_float(mny[-2].group(0))
        desc  = ln[d.end():mny[0].start()].strip()
        seq+=1
        rows.append({
            "fecha": pd.to_datetime(d.group(0), dayfirst=True, errors="coerce"),
            "desc": desc,
            "monto_pdf": monto,
            "saldo": saldo,
            "orden": seq
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["fecha","orden"]).reset_index(drop=True)
    return df

def inject_saldo_anterior(df, saldo_inicial):
    import pandas as pd, numpy as np
    if isinstance(saldo_inicial, (list, tuple)):
        saldo_inicial = saldo_inicial[0] if saldo_inicial else np.nan
    if saldo_inicial is None:
        saldo_inicial = np.nan
    if (isinstance(saldo_inicial,float) and np.isnan(saldo_inicial)) or df.empty:
        return df, np.nan
    first_date = df["fecha"].dropna().min()
    apertura = pd.DataFrame([{
        "fecha": (first_date - pd.Timedelta(days=1)).normalize() + pd.Timedelta(hours=23, minutes=59, seconds=59) if pd.notna(first_date) else pd.NaT,
        "desc": "SALDO ANTERIOR",
        "monto_pdf": 0.0,
        "saldo": float(saldo_inicial),
        "orden": -1
    }])
    out = pd.concat([apertura, df], ignore_index=True).sort_values(["fecha","orden"]).reset_index(drop=True)
    return out, float(saldo_inicial)

def debit_credit_from_delta(df):
    import numpy as np, pandas as pd
    if df.empty: 
        df["debito"]=df["credito"]=0.0
        return df
    df["delta"]=df["saldo"].diff()
    if pd.isna(df.loc[0,"delta"]) and len(df)>=2:
        df.loc[1,"delta"] = df.loc[1,"saldo"] - df.loc[0,"saldo"]
    df["debito"]  = np.where(df["delta"] < 0, -df["delta"], 0.0)
    df["credito"] = np.where(df["delta"] > 0,  df["delta"], 0.0)
    return df

def debit_credit_from_monto(df):
    import numpy as np
    if df.empty:
        df["debito"]=df["credito"]=0.0
        return df
    df["debito"]  = np.where(df["monto_pdf"] < 0, -df["monto_pdf"], 0.0)
    df["credito"] = np.where(df["monto_pdf"] > 0,  df["monto_pdf"], 0.0)
    return df

def resumen_operativo(df):
    import numpy as np
    if df.empty:
        return dict(net21=0.0, iva21=0.0, net105=0.0, iva105=0.0, percep_iva=0.0, ley25413=0.0, sircreb=0.0)
    u = df["desc"].fillna("").str.upper()
    from numpy import float64
    ley = df.loc[u.str.contains("25413|IMPTRANS|IMP\.\s?DBCR|IMP\.\s?S/CREDS"),["debito","credito"]]
    ley25413 = float(ley["debito"].sum() - ley["credito"].sum())
    sircreb  = float(df.loc[u.str.contains("SIRCREB"),"debito"].sum())
    percep_iva = float(df.loc[u.str.contains("PERCEP.*IVA|RG ?3337|RG ?2408"),"debito"].sum())
    iva_mask = u.str.contains(r"\bIVA\b") & (~u.str.contains("PERCEP"))
    iva21 = float(df.loc[iva_mask,"debito"].sum())
    net21 = round(iva21/0.21,2) if iva21 else 0.0
    return dict(net21=net21, iva21=iva21, net105=0.0, iva105=0.0, percep_iva=percep_iva, ley25413=ley25413, sircreb=sircreb)

def render_header(titulo, nro):
    st.markdown("---")
    st.subheader(f"{titulo} · Nro {nro if nro else 's/n'}")

def render_totales(saldo_ini, tot_cre, tot_deb, saldo_pdf, saldo_calc):
    diff = saldo_calc - saldo_pdf
    ok = abs(diff) < 0.01
    c1,c2,c3 = st.columns(3)
    with c1: st.markdown(f"**Saldo inicial**  \n$ {fmt(saldo_ini)}")
    with c2: st.markdown(f"**Total créditos (+)**  \n$ {fmt(tot_cre)}")
    with c3: st.markdown(f"**Total débitos (–)**  \n$ {fmt(tot_deb)}")
    c4,c5,c6 = st.columns(3)
    with c4: st.markdown(f"**Saldo final (PDF/tabla)**  \n$ {fmt(saldo_pdf)}")
    with c5: st.markdown(f"**Saldo final calculado**  \n$ {fmt(saldo_calc)}")
    with c6: st.markdown(f"**Diferencia**  \n$ {fmt(diff)}")
    st.success("Conciliado.") if ok else st.error("No cuadra la conciliación.")
    return ok
