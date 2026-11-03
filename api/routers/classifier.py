"""Estado, predicción con trazas NLP y métricas del modelo seleccionado."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_admin
from api.config import settings
from api.model import classifier_identity, classify, model_or_503, model_sha256
from api.schemas import PredictionInput

router = APIRouter()
SUMMARY_KEYS = ('model', 'method', 'ngrams', 'parameter_name', 'parameter_value', 'class_weight', 'weights',
                'f1_macro_mean', 'f1_macro_std')


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Se esperaba un objeto JSON')
        return value
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f'Métricas no disponibles: {path.name}.') from exc


def verified_report(model) -> tuple[dict, str]:
    """Lee report.json y comprueba que corresponde al modelo cargado (configuración y huella)."""
    report = read_json(settings.report_path)
    try:
        selected = report['selected']
        tfidf = model.named_steps['tfidf']
        name, value = classifier_identity(model)
        if (selected['model'] != name or selected['method'] != tfidf.tokenizer.method
                or selected['ngrams'] != tfidf.ngram_range[1] or selected['parameter_value'] != value):
            raise ValueError('El informe no corresponde a la configuración del modelo.')
        current = model_sha256()
        recorded = report.get('model_sha256')
        if recorded is None:
            manifest = read_json(settings.human_manifest)
            if manifest.get('comparison_report_sha256') != hashlib.sha256(settings.report_path.read_bytes()).hexdigest():
                raise ValueError('El informe no coincide con el manifiesto de evaluación.')
            recorded = manifest.get('model_sha256')
        if recorded != current:
            raise ValueError('El informe no corresponde al modelo cargado.')
        return report, current
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503,
                            detail='El informe de métricas no coincide con el modelo o está incompleto.') from exc


@router.get('/health')
def health():
    model_or_503()
    return {'status': 'ok', 'model': settings.model_dir.name, 'model_sha256': model_sha256()}


@router.post('/predict', dependencies=[Depends(require_admin)])
def predict(request: PredictionInput):
    model = model_or_503()
    try:
        return classify(model, request.text)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'No se pudo procesar el mensaje: {type(exc).__name__}.') from exc


@router.get('/metrics', dependencies=[Depends(require_admin)])
def metrics():
    model = model_or_503()
    report, current = verified_report(model)
    try:
        validation = report['validation']
        configurations = report.get('configurations', [])
        comparison = []
        for candidate in ('naive_bayes', 'logistic_regression', 'knn'):
            candidates = [c for c in configurations if c.get('model') == candidate and c.get('status') == 'ok'
                          and isinstance(c.get('f1_macro_mean'), (int, float))]
            if candidates:
                best = max(candidates, key=lambda c: c['f1_macro_mean'])
                comparison.append({k: best.get(k) for k in SUMMARY_KEYS})
        response = {'model_sha256': current,
                    'selection': {k: report['selected'].get(k) for k in SUMMARY_KEYS},
                    'validation': {k: validation.get(k) for k in ('f1_macro', 'accuracy', 'classification_report',
                                                                  'confusion_matrix_labels', 'confusion_matrix')},
                    'model_comparison': comparison, 'human_clients': None,
                    'note': 'Métricas exploratorias sobre datos mayormente sintéticos.'}
        if settings.human_path.is_file():
            human = read_json(settings.human_path)
            if human.get('model_sha256') == current:
                response['human_clients'] = {k: human[k] for k in ('records', 'f1_macro', 'classification_report',
                                                                   'confusion_matrix_labels', 'confusion_matrix')}
        return response
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail='El informe de métricas está incompleto.') from exc
