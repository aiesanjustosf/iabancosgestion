import pandas as pd
import numpy as np
import re
from .common import (
    MONEY_RE, DATE_RE, extract_all_lines, text_from_pdf, normalize_money,
    find_saldo_final_from_lines, normalize_desc, clasificar
)

GAL_SALDO_INICIAL_RE = re.compile(r"SALDO\s+INICIAL.*?(-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?)", re.I)
GAL_SALDO_FINAL_RE   = re.compile(r"SALDO\s+FINAL.*?(-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?)", re.I)

def galicia_header_saldos_from_text(txt: str) -> dict:
    ini = fin = np.nan
    m1 = GAL_SALDO_INICIAL_RE.search(txt or "")
    if m1: ini = normalize_money(m1.group(1))
    m2 = GAL_SALDO_FINAL_RE.search(txt or "")
    if m2: fin = normalize_money(m2.group(1))
    return {"saldo_inicial": ini, "saldo_final": fin}

def parse_pdf_galicia(file_like, pdf_txt: str):
    # 1) líneas
    lines_pairs = extract_all_lines(file_like)
    lines = [l for _, l in lines_pairs]

    # 2) parse filas: dd/mm/... + ... + penúltima = movimiento, última = saldo
    rows = []; seq = 0
    for ln in lines:
        s = ln.strip()
        if not s: continue
        am = list(MONEY_RE.finditer(s))
        if len(am) < 2: continue
        d = DATE_RE.search(s)
        if not d or d.end() >= am[0].start(): 
            continue

        saldo = normalize_money(am[-1].group(0))       # ultima col
        mov   = normalize_money(am[-2].group(0))       # penúltima col
        desc  = s[d.end(): am[0].start()].strip()

        seq += 1
        rows.append({
            "fecha": pd.to_datetime(d.group(0), dayfirst=True, errors="coerce"),
            "descripcion": desc,
            "origen": None,
            "desc_norm": normalize_desc(desc),
            "debito": (-mov) if mov < 0 else 0.0,
            "credito": mov if mov > 0 else 0.0,
            "importe": mov,
            "saldo": saldo,
            "orden": seq
        })
    df = pd.DataFrame(rows).sort_values(["fecha","orden"]).reset_index(drop=True)

    # 3) saldos de encabezado o reconstrucción
    header = galicia_header_saldos_from_text(pdf_txt)
    saldo_inicial = header.get("saldo_inicial", np.nan)
    fecha_cierre, saldo_final_pdf = find_saldo_final_from_lines(lines)
    if not np.isnan(header.get("saldo_final", np.nan)):
        saldo_final_pdf = float(header["saldo_final"])

    if np.isnan(saldo_inicial) and not df.empty:
        s0 = float(df.loc[0, "saldo"])
        m0 = float(df.loc[0, "importe"])
        saldo_inicial = s0 - m0 if m0 > 0 else s0 + (-m0)

    # 4) insertar SALDO ANTERIOR
    if not np.isnan(saldo_inicial):
        first_date = df["fecha"].dropna().min()
        apertura = pd.DataFrame([{
            "fecha": (first_date - pd.Timedelta(days=1)) if pd.notna(first_date) else pd.NaT,
            "descripcion": "SALDO ANTERIOR",
            "origen": None,
            "desc_norm": "SALDO ANTERIOR",
            "debito": 0.0, "credito": 0.0,
            "importe": 0.0, "saldo": float(saldo_inicial),
            "orden": 0
        }])
        df = pd.concat([apertura, df], ignore_index=True).sort_values(["fecha","orden"]).reset_index(drop=True)

    # 5) clasificación
    df["Clasificación"] = df.apply(
        lambda r: clasificar(str(r.get("descripcion","")), str(r.get("desc_norm","")), r.get("debito",0.0), r.get("credito",0.0)),
        axis=1
    )

    fecha_cierre_str = fecha_cierre.strftime('%d/%m/%Y') if pd.notna(fecha_cierre) else None
    return df.drop(columns=["orden"]), fecha_cierre_str
