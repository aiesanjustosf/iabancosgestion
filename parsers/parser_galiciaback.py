
import re
from .utils import ar_to_float, normalize_whitespace, concilia, build_df

def parse_galicia(pages_text: list[str]):
    # Galicia: débitos como negativos a la izquierda en el propio extracto
    # Heurística: líneas con monto con "-" van a débito; resto a crédito si hay "+" o sin signo en columna crédito.
    rows = []
    total_debitos = 0.0
    total_creditos = 0.0
    saldo_inicial = 0.0
    saldo_pdf = 0.0

    full = "\n".join(pages_text)

    # saldo inicial / final (heurísticas simples)
    m0 = re.search(r"Saldo\s+inicial.*?\$?\s*([-\d\.\,]+)", full, re.I)
    if m0: saldo_inicial = ar_to_float(m0.group(1))
    m1 = re.search(r"Saldo\s+final.*?\$?\s*([-\d\.\,]+)", full, re.I)
    if m1: saldo_pdf = ar_to_float(m1.group(1))

    for page in pages_text:
        for raw in page.splitlines():
            s = normalize_whitespace(raw)
            # fecha dd/mm
            if not re.search(r"\b\d{2}/\d{2}\b", s):
                continue
            # montos al final
            mm = re.findall(r"[-]?\$?\s*\d{1,3}(?:\.\d{3})*(?:,\d{2})", s)
            if not mm: 
                continue
            monto_str = mm[-1]
            monto = ar_to_float(monto_str)
            is_debito = "-" in monto_str
            debito = abs(monto) if is_debito else 0.0
            credito = 0.0 if is_debito else abs(monto)
            total_debitos += debito
            total_creditos += credito
            rows.append([s[:5], s, debito, credito, credito-debito, monto, None])

    ok, calculado, diff = concilia(saldo_inicial, total_creditos, total_debitos, saldo_pdf)
    df = build_df(rows)
    resumen = {
        "saldo_inicial": saldo_inicial,
        "total_creditos": total_creditos,
        "total_debitos": total_debitos,
        "saldo_pdf": saldo_pdf,
        "cuadra": ok,
        "saldo_calc": calculado,
        "diferencia": diff,
        "parser": "galicia",
    }
    return resumen, df
