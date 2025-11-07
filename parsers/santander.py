from . import common as C

def render(data: bytes, full_text: str):
    lines = C.extract_all_lines(data)
    df = C.parse_generic_table(lines)
    C.render_summary(df, titulo="Cuenta Corriente (Santander) · Nro s/n")
