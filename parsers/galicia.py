import re
from . import common as C

SALDO_INI_RE = re.compile(r"SALDO\s+INICIAL.*?(-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?)", re.I)
SALDO_FIN_RE = re.compile(r"SALDO\s+FINAL.*?(-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?)", re.I)

def _saldos_header(txt: str):
    ini = fin = None
    m1 = SALDO_INI_RE.search(txt or "")
    if m1: ini = C.normalize_money(m1.group(1))
    m2 = SALDO_FIN_RE.search(txt or "")
    if m2: fin = C.normalize_money(m2.group(1))
    return ini, fin

def render(data: bytes, full_text: str):
    lines = C.extract_all_lines(data)
    df = C.parse_generic_table(lines)  # Galicia trae montos signados; esta lógica funciona bien
    ini, fin = _saldos_header(full_text)
    C.render_summary(df, saldo_inicial=ini, saldo_final_pdf=fin, titulo="Cuenta Corriente (Galicia) · Nro s/n")
