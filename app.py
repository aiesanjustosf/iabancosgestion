import pdfplumber

with pdfplumber.open("TUPDF.pdf") as pdf:
    for i, page in enumerate(pdf.pages, start=1):
        print(f"\n\n=== Página {i} ===\n\n")
        text = page.extract_text() or ""
        for line in text.splitlines():
            print(line)
