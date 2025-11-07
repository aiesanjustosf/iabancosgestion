import io
from pathlib import Path
import streamlit as st
import pandas as pd

from parsers.detect import detect_bank_from_text, BANK_SLUG
from parsers.common import text_from_pdf, extract_all_lines, fmt_ar
from parsers.galicia import parse_pdf_galicia
from parsers.generico import parse_pdf_generico, santander_cut_before_detalle

HERE = Path(__file__).parent
LOGO = HERE / "logo_aie.png"
FAVICON = HERE / "favicon-aie.ico"

st.set_page_config(page_title="IA Resumen Bancario", page_icon=str(FAVICON) if FAVICON.exists() else None)
if LOGO.exists():
    st.image(str(LOGO), width=200)
st.title("IA Resumen Bancario")

uploaded = st.file_uploader("Subí un PDF del resumen bancario", type=["pdf"])
if not uploaded:
    st.info("La app no almacena datos, toda la información está protegida.")
    st.stop()

data = uploaded.read()
pdf_txt = text_from_pdf(io.BytesIO(data))
auto_name = detect_bank_from_text(pdf_txt)

with st.expander("Opciones avanzadas (detección de banco)", expanded=False):
    forced = st.selectbox(
        "Forzar identificación del banco",
        options=("Auto (detectar)", "Banco de la Nación Argentina", "Banco de Santa Fe", "Banco Macro", "Banco Santander", "Banco Galicia"),
        index=0,
        help="Solo cambia la etiqueta informativa y el nombre de archivo."
    )

bank_name = forced if forced != "Auto (detectar)" else auto_name
slug = BANK_SLUG.get(bank_name, "generico")

if bank_name == "Banco Galicia":
    st.success(f"Detectado: {bank_name}")
else:
    st.info(f"Detectado: {bank_name}")

# --------- Ejecución por banco ----------
def render(df_sorted: pd.DataFrame, titulo: str, archivo_slug: str, fecha_cierre_str: str | None):
    # Resumen de período
    st.caption("Resumen del período")
    saldo_inicial = float(df_sorted["saldo"].iloc[0]) if not df_sorted.empty else 0.0
    total_debitos = float(df_sorted["debito"].sum())
    total_creditos = float(df_sorted["credito"].sum())
    saldo_final_visto = float(df_sorted["saldo"].iloc[-1]) if not df_sorted.empty else 0.0
    saldo_final_calculado = saldo_inicial + total_creditos - total_debitos
    diferencia = saldo_final_calculado - saldo_final_visto
    cuadra = abs(diferencia) < 0.01

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("Saldo inicial", f"$ {fmt_ar(saldo_inicial)}")
    with c2: st.metric("Total créditos (+)", f"$ {fmt_ar(total_creditos)}")
    with c3: st.metric("Total débitos (–)", f"$ {fmt_ar(total_debitos)}")
    c4, c5, c6 = st.columns(3)
    with c4: st.metric("Saldo final (PDF)", f"$ {fmt_ar(saldo_final_visto)}")
    with c5: st.metric("Saldo final calculado", f"$ {fmt_ar(saldo_final_calculado)}")
    with c6: st.metric("Diferencia", f"$ {fmt_ar(diferencia)}")
    if cuadra:
        st.success("Conciliado.")
    else:
        st.error("No cuadra la conciliación.")

    if fecha_cierre_str:
        st.caption(f"Cierre según PDF: {fecha_cierre_str}")

    # Resumen Operativo
    st.caption("Resumen Operativo: Registración Módulo IVA")
    iva21_mask  = df_sorted["Clasificación"].eq("IVA 21% (sobre comisiones)")
    iva105_mask = df_sorted["Clasificación"].eq("IVA 10,5% (sobre comisiones)")

    iva21  = float(df_sorted.loc[iva21_mask,  "debito"].sum())
    iva105 = float(df_sorted.loc[iva105_mask, "debito"].sum())
    net21  = round(iva21  / 0.21,  2) if iva21  else 0.0
    net105 = round(iva105 / 0.105, 2) if iva105 else 0.0

    percep_iva = float(df_sorted.loc[df_sorted["Clasificación"].eq("Percepciones de IVA"), "debito"].sum())
    ley_deb = float(df_sorted.loc[df_sorted["Clasificación"].eq("LEY 25.413"), "debito"].sum())
    ley_cre = float(df_sorted.loc[df_sorted["Clasificación"].eq("LEY 25.413"), "credito"].sum())
    ley_25413 = ley_deb - ley_cre
    sircreb   = float(df_sorted.loc[df_sorted["Clasificación"].eq("SIRCREB"), "debito"].sum())

    m1, m2, m3 = st.columns(3)
    with m1: st.metric("Neto Comisiones 21%", f"$ {fmt_ar(net21)}")
    with m2: st.metric("IVA 21%", f"$ {fmt_ar(iva21)}")
    with m3: st.metric("Bruto 21%", f"$ {fmt_ar(net21 + iva21)}")

    n1, n2, n3 = st.columns(3)
    with n1: st.metric("Neto Comisiones 10,5%", f"$ {fmt_ar(net105)}")
    with n2: st.metric("IVA 10,5%", f"$ {fmt_ar(iva105)}")
    with n3: st.metric("Bruto 10,5%", f"$ {fmt_ar(net105 + iva105)}")

    o1, o2, o3 = st.columns(3)
    with o1: st.metric("Percepciones de IVA (RG 3337 / RG 2408)", f"$ {fmt_ar(percep_iva)}")
    with o2: st.metric("Ley 25.413 (neto)", f"$ {fmt_ar(ley_25413)}")
    with o3: st.metric("SIRCREB", f"$ {fmt_ar(sircreb)}")

    # Detalle
    st.caption("Detalle de movimientos")
    show_cols = [c for c in ["fecha","descripcion","origen","debito","credito","importe","saldo","Clasificación"] if c in df_sorted.columns]
    st.dataframe(df_sorted[show_cols].style.format(
        {"debito": fmt_ar, "credito": fmt_ar, "importe": fmt_ar, "saldo": fmt_ar}
    ), use_container_width=True)

    # Descargas
    st.caption("Descargar")
    try:
        import xlsxwriter, io
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as w:
            df_sorted.to_excel(w, index=False, sheet_name="Movimientos")
            wb = w.book; ws = w.sheets["Movimientos"]
            money_fmt = wb.add_format({"num_format": "#,##0.00"})
            date_fmt  = wb.add_format({"num_format": "dd/mm/yyyy"})
            for idx, col in enumerate(df_sorted.columns, start=0):
                ws.set_column(idx, idx, min( max(len(str(col)), 14), 50))
            for c in ("debito","credito","importe","saldo"):
                if c in df_sorted.columns:
                    j = df_sorted.columns.get_loc(c)
                    ws.set_column(j, j, 16, money_fmt)
            if "fecha" in df_sorted.columns:
                j = df_sorted.columns.get_loc("fecha")
                ws.set_column(j, j, 14, date_fmt)
        st.download_button(
            "📥 Descargar Excel",
            data=out.getvalue(),
            file_name=f"resumen_bancario_{archivo_slug}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key=f"dl_xlsx_{archivo_slug}",
        )
    except Exception:
        csv_bytes = df_sorted.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Descargar CSV (fallback)",
            data=csv_bytes,
            file_name=f"resumen_bancario_{archivo_slug}.csv",
            mime="text/csv",
            use_container_width=True,
            key=f"dl_csv_{archivo_slug}",
        )

# Ruta Galicia vs resto (aislado)
if bank_name == "Banco Galicia":
    df_sorted, fecha_cierre_str = parse_pdf_galicia(io.BytesIO(data), pdf_txt)
    render(df_sorted, "Cuenta Corriente (Galicia)", "galicia", fecha_cierre_str)
else:
    # Santander: recorte de “DETALLE IMPOSITIVO” antes de parsear
    lines = [l for _, l in extract_all_lines(io.BytesIO(data))]
    if bank_name == "Banco Santander":
        lines = santander_cut_before_detalle(lines)
    df_sorted, fecha_cierre_str = parse_pdf_generico(bank_name, io.BytesIO(data), lines)
    render(df_sorted, f"Cuenta ({bank_name})", BANK_SLUG.get(bank_name, "generico"), fecha_cierre_str)
