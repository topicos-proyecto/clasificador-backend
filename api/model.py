"""Carga del pipeline entrenado y clasificación (sin duplicar el NLP: se usa el pipeline guardado)."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import joblib
from fastapi import HTTPException

from api.config import settings

GREETINGS = {'hola', 'buenos días', 'buenas tardes', 'buenas noches', 'ayuda'}
MODEL_NAMES = {'MultinomialNB': 'naive_bayes', 'LogisticRegression': 'logistic_regression',
               'KNeighborsClassifier': 'knn'}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=2)
def load_model(path: Path):
    if not path.is_file():
        raise RuntimeError('Modelo no disponible. Ejecuta la comparación orientada a clientes.')
    try:
        model = joblib.load(path)
        tfidf, classifier = model.named_steps['tfidf'], model.named_steps['classifier']
        if not hasattr(tfidf.tokenizer, 'trace') or not hasattr(classifier, 'predict_proba'):
            raise ValueError('El artefacto no tiene el pipeline esperado.')
        return model, sha256(path)
    except Exception as exc:
        raise RuntimeError(f'No se pudo cargar el modelo: {type(exc).__name__}.') from exc


def current_model():
    """Devuelve (modelo, sha) o lanza RuntimeError si falta o cambió desde su carga."""
    path = settings.model_path
    model, loaded_sha = load_model(path)
    if not path.is_file() or sha256(path) != loaded_sha:
        raise RuntimeError('El modelo cambió desde su carga; reinicia la API.')
    return model, loaded_sha


def model_or_503():
    try:
        return current_model()[0]
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def model_sha256() -> str:
    return sha256(settings.model_path)


def classifier_identity(model) -> tuple[str, float]:
    """Nombre interno del clasificador y valor de su hiperparámetro principal."""
    classifier = model.named_steps['classifier']
    name = MODEL_NAMES[type(classifier).__name__]
    value = {'naive_bayes': lambda c: c.alpha, 'logistic_regression': lambda c: c.C,
             'knn': lambda c: c.n_neighbors}[name](classifier)
    return name, value


def classify(model, text: str) -> dict:
    """Misma lógica que /predict: saludo o sin términos conocidos → aclaración; si no, etiqueta."""
    tfidf = model.named_steps['tfidf']
    trace = tfidf.tokenizer.trace(text)
    if trace['normalized'] in GREETINGS:
        return {'label': None, 'probabilities': {}, 'requires_clarification': True,
                'reason': 'Describe tu solicitud con más detalle.', 'preprocessing': trace}
    features = tfidf.transform([text])
    if features.nnz == 0:
        return {'label': None, 'probabilities': {}, 'requires_clarification': True,
                'reason': 'No se reconocieron términos suficientes; agrega más detalles.', 'preprocessing': trace}
    classifier = model.named_steps['classifier']
    probs = classifier.predict_proba(features)[0]
    probabilities = {str(label): float(p) for label, p in zip(classifier.classes_, probs)}
    label = str(classifier.classes_[probs.argmax()])
    return {'label': label, 'probabilities': probabilities, 'requires_clarification': False,
            'reason': None, 'preprocessing': trace}
