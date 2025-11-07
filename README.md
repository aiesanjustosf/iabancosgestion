# IA Resumen Bancario (router por banco)

App de Streamlit con router: detecta el banco y delega el parseo a un módulo por banco.

## Ejecutar

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

1) Subí a GitHub.  
2) En Streamlit Cloud: New app -> repo -> `app.py`.
