# Clasificador de tickets — Backend

API FastAPI para clasificar solicitudes de clientes. Usa el pipeline y las métricas de `../clasificador-tickets-nlp`; las tres carpetas deben permanecer juntas en el workspace. No hay una copia de NLP ni de los datos en este directorio.

## Preparación

El proyecto NLP necesita Python 3.12, su entorno `.venv312`, las dependencias de su `requirements.txt` y el modelo español de spaCy indicados en su README. El artefacto `../clasificador-tickets-nlp/artifacts/comparison_customers/pipeline.joblib` y sus informes deben existir; si faltan, generarlos mediante las instrucciones del proyecto NLP.

Si ya existe el entorno NLP, desde esta carpeta:

```bash
PYTHONPATH=../clasificador-tickets-nlp ../clasificador-tickets-nlp/.venv312/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Para preparar un entorno propio del backend, usar Python 3.12, ejecutar `python3.12 -m venv .venv`, activar `.venv`, instalar `python -m pip install -r requirements.txt` e instalar `es_core_news_sm` 3.8.0 como indica el README NLP. Luego iniciar con `PYTHONPATH=../clasificador-tickets-nlp python -m uvicorn api.main:app --host 127.0.0.1 --port 8000`.

La documentación interactiva se encuentra en `http://127.0.0.1:8000/docs`. La API ofrece `POST /predict`, `GET /metrics` y `GET /health`. Las probabilidades son estimaciones no calibradas y la evaluación humana es exploratoria.

## Pruebas

```bash
PYTHONPATH=../clasificador-tickets-nlp ../clasificador-tickets-nlp/.venv312/bin/python -m pytest -q
```

Las pruebas de integración requieren los artefactos locales del proyecto NLP. Los informes históricos se verifican mediante sus huellas; la API no expone los textos de evaluación.
