import json

import pytest
from fastapi.testclient import TestClient

import api.main as api


@pytest.fixture
def client():
    api.load_model.cache_clear()
    yield TestClient(api.app)
    api.load_model.cache_clear()


def test_prediction_matches_saved_pipeline_and_shows_trace(client):
    text = 'La aplicación se cierra cuando intento abrirla'
    response = client.post('/predict', json={'text': text})
    assert response.status_code == 200
    payload = response.json()
    model = api.model_or_503()
    assert payload['label'] == model.predict([text])[0]
    assert abs(sum(payload['probabilities'].values()) - 1) < 1e-8
    assert payload['requires_clarification'] is False
    assert payload['preprocessing'] == model.named_steps['tfidf'].tokenizer.trace(text)


def test_invalid_input_and_clarification(client):
    for body in ({'text': ''}, {'text': '   '}, {'text': '😊!!!'}, {'text': 'a' * 3001},
                 {'text': 123}, {}, {'text': None}):
        assert client.post('/predict', json=body).status_code == 422
    for text in ('Hola', 'zzzxxxqqqunseenword'):
        r = client.post('/predict', json={'text': text})
        assert r.status_code == 200
        assert r.json()['requires_clarification'] is True
        assert r.json()['label'] is None


def test_health_and_metrics_without_exposing_example_text(client):
    health = client.get('/health')
    assert health.status_code == 200 and health.json()['status'] == 'ok'
    response = client.get('/metrics')
    assert response.status_code == 200
    metrics = response.json()
    assert metrics['model_sha256'] == health.json()['model_sha256']
    assert len(metrics['validation']['confusion_matrix']) == 6
    assert {item['model'] for item in metrics['model_comparison']} == {'naive_bayes', 'logistic_regression'}
    assert 'errors' not in json.dumps(metrics)
    assert metrics['human_clients']['records'] == 30


def test_missing_model_and_changed_artifact_return_503(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'MODEL', tmp_path / 'missing.joblib')
    assert client.get('/health').status_code == 503
    assert client.post('/predict', json={'text': 'Mi factura no llegó'}).status_code == 503
    path = tmp_path / 'model.joblib'
    path.write_bytes(api.ROOT.joinpath('artifacts/comparison_customers/pipeline.joblib').read_bytes())
    monkeypatch.setattr(api, 'MODEL', path)
    assert client.get('/health').status_code == 200
    path.write_bytes(b'changed')
    assert client.get('/health').status_code == 503


def test_missing_or_inconsistent_metrics_return_503(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'COMPARISON', tmp_path / 'missing.json')
    assert client.get('/metrics').status_code == 503
    report = json.loads((api.ARTIFACTS / 'report.json').read_text(encoding='utf-8'))
    report['selected']['method'] = 'stemming'
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    monkeypatch.setattr(api, 'COMPARISON', path)
    assert client.get('/metrics').status_code == 503
