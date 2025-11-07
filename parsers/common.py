import re, io
import numpy as np
import pandas as pd
import pdfplumber

# Regex
DATE_RE  = re.compile(r"\b\d{1,2}/\d{2}/\d{2,4}\b")  # dd/mm/aa o dd/mm/aaaa
MONEY_RE = re.compile(r'(?<!\S)-?(?:\d{1,3}(?:\.\d{3})*|\d+)\s?,\s?\d{2}-?(?!\S)')
LONG_INT_RE = re.compile(r"\b\d{6,}\b")

SALDO_ANT_PREFIX   = re.compile(r"^SALDO\s+U?LTIMO\s+EXTRACTO\s+AL", re.IGNORECASE)
SALDO_FINAL_PREFIX = re.compile(r"^SALDO\s+FINAL\s+AL\s+D[ÍI]A",     re.IGNORECASE)
SF_SALDO_ULT_RE = re.compile(r"SALDO\s+U?LTIMO\s+RESUMEN", re.IGNORECASE)

HEADER_ROW_PAT = re.compile(r"^(FECHA\s+DESCRIPC(?:I[ÓO]N|ION)|FECHA\s+CONCEPTO|FECHA\s+DETALLE).*(SALDO|D[ÉE]BITO|CR[ÉE]DITO)", re.IGNORECASE)
NON_MOV_PAT    = re.compile(r"(INFORMACI[ÓO]N\s+DE\s+SU/S\s+CUENTA/S|TOTAL\s+RESUMEN\s+OPERATIVO|RESUMEN\s+DEL\s+PER[IÍ]ODO)", re.IGNORECASE)

def upper_safe(s: str) -> str:
    return (s or "").upper()

def normalize_money(tok: str) -> float:
    if not tok: return np.nan
    tok = tok.strip()
    neg = tok.endswith("-") or tok.startswith("-")
    tok = tok.lstrip("-").rstrip("-")
    if "," not in tok: return np.nan
    main, frac = tok.rsplit(",", 1)
    main = main.replace(".", "").replace(" ", "")
    try:
        val = float(f"{main}.{frac}")
        return -val if neg else val
    except Exception:
        return np.nan

def fmt_ar(n) -> str:
    if n is None or (isinstance(n, float) and np.isnan(n)): return "—"
    return f"{n:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")

def text_from_pdf(file_like) -> str:
    try:
        with pdfplumber.open(file_like) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""

def lines_from_text(page):
    txt = page.extract_text() or ""
    return [" ".join(l.split()) for l in txt.splitlines()]

def lines_from_words(page, ytol=2.0):
    words = page.extract_words(extra_attrs=["x0", "top"])
    if not words: return []
    words.sort(key=lambda w: (round(w["top"]/ytol), w["x0"]))
    lines, cur, band = [], [], None
    for w in words:
        b = round(w["top"]/ytol)
        if band is None or b == band:
            cur.append(w)
        else:
            lines.append(" ".join(x["text"] for x in cur)); cur = [w]
        band = b
    if cur: lines.append(" ".join(x["text"] for x in cur))
    return [" ".join(l.split()) for l in lines]

def extract_all_lines(file_like):
    out = []
    with pdfplumber.open(file_like) as pdf:
        for pi, p in enumerate(pdf.pages, start=1):
            lt = lines_from_text(p)
            lw = lines_from_words(p, ytol=2.0)
            seen = set(lt)
            combined = lt + [l for l in lw if l not in seen]
            out.extend([(pi, l) for l in combined if l and l.strip()])
    return out

def _only_one_amount(line: str) -> bool:
    return len(list(MONEY_RE.finditer(line))) == 1

def _first_amount_value(line: str) -> float:
    m = MONEY_RE.search(line)
    return normalize_money(m.group(0)) if m else np.nan

def find_saldo_final_from_lines(lines):
    for ln in reversed(lines):
        if SALDO_FINAL_PREFIX.match(ln):
            d = DATE_RE.search(ln)
            if d and _only_one_amount(ln):
                fecha = pd.to_datetime(d.group(0), dayfirst=True, errors="coerce")
                saldo = _first_amount_value(ln)
                if pd.notna(fecha) and not np.isnan(saldo): 
                    return fecha, saldo
    for ln in reversed(lines):
        if "SALDO FINAL" in upper_safe(ln) and _only_one_amount(ln):
            saldo = _first_amount_value(ln)
            if not np.isnan(saldo): 
                return pd.NaT, saldo
    return pd.NaT, np.nan

def find_saldo_anterior_from_lines(lines):
    for ln in lines:
        if SALDO_ANT_PREFIX.match(ln):
            d = DATE_RE.search(ln)
            if d and _only_one_amount(ln):
                saldo = _first_amount_value(ln)
                if not np.isnan(saldo): return saldo
    for ln in lines:
        U = upper_safe(ln)
        if "SALDO ANTERIOR" in U and _only_one_amount(ln):
            saldo = _first_amount_value(ln)
            if not np.isnan(saldo): return saldo
    for ln in lines:
        U = upper_safe(ln)
        if "SALDO ULTIMO EXTRACTO" in U or "SALDO ÚLTIMO EXTRACTO" in U:
            d = DATE_RE.search(ln)
            if d and _only_one_amount(ln):
                saldo = _first_amount_value(ln)
                if not np.isnan(saldo): return saldo
    for i, ln in enumerate(lines):
        if SF_SALDO_ULT_RE.search(ln):
            if _only_one_amount(ln):
                v = _first_amount_value(ln)
                if not np.isnan(v): return v
            for j in (i+1, i+2):
                if 0 <= j < len(lines):
                    ln2 = lines[j]
                    if _only_one_amount(ln2):
                        v2 = _first_amount_value(ln2)
                        if not np.isnan(v2): return v2
            break
    return np.nan

def normalize_desc(desc: str) -> str:
    if not desc: return ""
    u = desc.upper()
    for pref in ("SAN JUS ","CASA RO ","CENTRAL ","GOBERNA ","GOBERNADOR ","SANTA FE ","ROSARIO "):
        if u.startswith(pref):
            u = u[len(pref):]; break
    u = LONG_INT_RE.sub("", u)
    u = " ".join(u.split())
    return u

# Clasificación (común)
import re as _re
RE_SIRCREB = _re.compile(r"\bSIRCREB\b|ING\.\s*BRUTOS.*S/?\s*CRED", _re.I)
RE_LEY25413 = _re.compile(r"\b(?:IMP\.?\s*DEB\.?/CRE\.?\s*LEY\s*25\.?413|LEY\s*25\.?413|IMPDBCR\s*25413|N/?D\s*DBCR\s*25413|IMPTRANS|IMP\.?\s*S/CREDS)\b", _re.I)
RE_PERCEP_IVA = _re.compile(r"(IVA\s*PERC|IVA\s*PERCEP|RG\.?\s*3337|RG\.?\s*2408|RETEN.*I\.?V\.?A)", _re.I)
RE_IVA_21 = _re.compile(r"(I\.?V\.?A\.?\s*BASE|IVA\s*GRAL|DEBITO\s*FISCAL\s*IVA\s*BASICO)", _re.I)
RE_IVA_105 = _re.compile(r"(IVA\s*10[,\.]5|IVA\s*REDUC|IVA\s*RINS)", _re.I)

def clasificar(desc: str, desc_norm: str, deb: float, cre: float) -> str:
    u = upper_safe(desc); n = upper_safe(desc_norm)
    if "SALDO ANTERIOR" in u or "SALDO ANTERIOR" in n: return "SALDO ANTERIOR"
    if RE_SIRCREB.search(u) or RE_SIRCREB.search(n):  return "SIRCREB"
    if RE_LEY25413.search(u) or RE_LEY25413.search(n):return "LEY 25.413"
    if RE_PERCEP_IVA.search(u) or RE_PERCEP_IVA.search(n): return "Percepciones de IVA"
    if RE_IVA_105.search(u) or RE_IVA_105.search(n): return "IVA 10,5% (sobre comisiones)"
    if RE_IVA_21.search(u) or RE_IVA_21.search(n):   return "IVA 21% (sobre comisiones)"

    # Transferencias / préstamos / otros (resumen)
    if ("TRANSFERENCIA DE TERCEROS" in u or "TRANSF RECIB" in n or "CR-TRSFE" in n or "TRANLINK" in n) and cre: return "Transferencia de terceros recibida"
    if ("DB-TRSFE" in n or "TRSFE-ET" in n or "TRSFE-IT" in n) and deb: return "Transferencia a terceros realizada"
    if ("DTNCTAPR" in n or "ENTRE CTA" in n or "CTA PROPIA" in n): return "Transferencia entre cuentas propias"
    if ("CUOTA PRÉSTAMO" in u or "CUOTA PRESTAMO" in u or "DEB.CUOTA PRESTAMO" in n): return "Cuota de préstamo"
    if ("CR.PREST" in n or "CREDITO PRESTAMOS" in n): return "Acreditación Préstamos"

    if cre and cre != 0: return "Crédito"
    if deb and deb != 0: return "Débito"
    return "Otros"
