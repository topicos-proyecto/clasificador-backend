"""API local del clasificador de tickets de clientes."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import joblib
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictStr, field_validator

from src.nlp.preprocessing import normalize
from src.models.train import ROOT
ARTIFACTS = ROOT / 'artifacts/comparison_customers'
MODEL = ARTIFACTS / 'pipeline.joblib'
COMPARISON = ARTIFACTS / 'report.json'
HUMAN = ARTIFACTS / 'human_clients_report.json'
HUMAN_MANIFEST = ROOT / 'data/raw/custom/human_evaluation_clients_manifest.json'
GREETINGS = {'hola', 'buenos días', 'buenas tardes', 'buenas noches', 'ayuda'}

app = FastAPI(title='Clasificador de tickets', version='0.1.0',
              description='Predicción exploratoria de área para solicitudes de clientes.')
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
                   allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])


class PredictionInput(BaseModel):
    text: Annotated[StrictStr, Field(min_length=1, max_length=3000)]

    @field_validator('text')
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or not normalize(value):
            raise ValueError('Escribe un mensaje con texto legible.')
        return value.strip()


@lru_cache(maxsize=1)
def load_model():
    if not MODEL.is_file():
        raise RuntimeError('Modelo no disponible. Ejecuta la comparación orientada a clientes.')
    try:
        model = joblib.load(MODEL)
        tfidf, classifier = model.named_steps['tfidf'], model.named_steps['classifier']
        if not hasattr(tfidf.tokenizer, 'trace') or not hasattr(classifier, 'predict_proba'):
            raise ValueError('El artefacto no tiene el pipeline esperado.')
        return model, model_sha256()
    except Exception as exc:
        raise RuntimeError(f'No se pudo cargar el modelo: {type(exc).__name__}.') from exc


def model_or_503():
    try:
        model, loaded_sha = load_model()
        if model_sha256() != loaded_sha:
            raise RuntimeError('El modelo cambió desde su carga; reinicia la API.')
        return model
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def model_sha256() -> str:
    return hashlib.sha256(MODEL.read_bytes()).hexdigest()


@app.get('/health')
def health():
    model_or_503()
    return {'status': 'ok', 'model': 'comparison_customers', 'model_sha256': model_sha256()}


@app.post('/predict')
def predict(request: PredictionInput):
    model = model_or_503()
    tfidf = model.named_steps['tfidf']
    try:
        trace = tfidf.tokenizer.trace(request.text)
        if trace['normalized'] in GREETINGS:
            return {'label': None, 'probabilities': {}, 'requires_clarification': True,
                    'reason': 'Describe tu solicitud con más detalle.', 'preprocessing': trace}
        features = tfidf.transform([request.text])
        if features.nnz == 0:
            return {'label': None, 'probabilities': {}, 'requires_clarification': True,
                    'reason': 'No se reconocieron términos suficientes; agrega más detalles.', 'preprocessing': trace}
        classifier = model.named_steps['classifier']
        probs = classifier.predict_proba(features)[0]
        probabilities = {str(label): float(p) for label, p in zip(classifier.classes_, probs)}
        label = str(classifier.classes_[probs.argmax()])
        return {'label': label, 'probabilities': probabilities, 'requires_clarification': False,
                'reason': None, 'preprocessing': trace}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'No se pudo procesar el mensaje: {type(exc).__name__}.') from exc


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Se esperaba un objeto JSON')
        return value
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f'Métricas no disponibles: {path.name}.') from exc


@app.get('/metrics')
def metrics():
    model = model_or_503()
    comparison = read_json(COMPARISON)
    try:
        selected = comparison['selected']
        tfidf = model.named_steps['tfidf']
        classifier = model.named_steps['classifier']
        name = {'MultinomialNB': 'naive_bayes', 'LogisticRegression': 'logistic_regression'}[type(classifier).__name__]
        value = classifier.alpha if name == 'naive_bayes' else classifier.C
        if (selected['model'] != name or selected['method'] != tfidf.tokenizer.method
            or selected['ngrams'] != tfidf.ngram_range[1] or selected['parameter_value'] != value):
            raise ValueError('El informe no corresponde a la configuración del modelo.')
        current_sha = model_sha256()
        recorded_sha = comparison.get('model_sha256')
        if recorded_sha is None:
            manifest = read_json(HUMAN_MANIFEST)
            if manifest.get('comparison_report_sha256') != hashlib.sha256(COMPARISON.read_bytes()).hexdigest():
                raise ValueError('El informe no coincide con el manifiesto de evaluación.')
            recorded_sha = manifest.get('model_sha256')
        if recorded_sha != current_sha:
            raise ValueError('El informe no corresponde al modelo cargado.')
        validation = comparison['validation']
        configurations = comparison.get('configurations', [])
        model_comparison = []
        for candidate_name in ('naive_bayes', 'logistic_regression'):
            candidates = [item for item in configurations if item.get('model') == candidate_name
                          and item.get('status') == 'ok' and isinstance(item.get('f1_macro_mean'), (int, float))]
            if candidates:
                best = max(candidates, key=lambda item: item['f1_macro_mean'])
                model_comparison.append({key: best[key] for key in
                                         ('model', 'method', 'ngrams', 'parameter_name', 'parameter_value',
                                          'f1_macro_mean', 'f1_macro_std')})
        response = {'model_sha256': current_sha,
                    'selection': {k: selected[k] for k in ('model', 'method', 'ngrams', 'parameter_name', 'parameter_value', 'f1_macro_mean', 'f1_macro_std')},
                    'validation': {k: validation[k] for k in ('f1_macro', 'classification_report', 'confusion_matrix_labels', 'confusion_matrix')},
                    'model_comparison': model_comparison,
                    'human_clients': None,
                    'note': 'Métricas exploratorias; no se evaluó el conjunto test.'}
        if HUMAN.is_file():
            human = read_json(HUMAN)
            if human.get('model_sha256') == response['model_sha256']:
                response['human_clients'] = {k: human[k] for k in ('records', 'f1_macro', 'classification_report', 'confusion_matrix_labels', 'confusion_matrix')}
        return response
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail='El informe de métricas no coincide con el modelo o está incompleto.') from exc
