import json
import shutil

from api.config import settings
from api.model import model_or_503


def test_prediction_matches_saved_pipeline_and_shows_trace(client, auth):
    text = 'La aplicación se cierra cuando intento abrirla'
    response = client.post('/predict', json={'text': text}, headers=auth)
    assert response.status_code == 200
    payload = response.json()
    model = model_or_503()
    assert payload['label'] == model.predict([text])[0]
    assert abs(sum(payload['probabilities'].values()) - 1) < 1e-8
    assert payload['requires_clarification'] is False
    assert payload['preprocessing'] == model.named_steps['tfidf'].tokenizer.trace(text)


def test_predict_and_metrics_require_admin(client):
    assert client.post('/predict', json={'text': 'Mi factura no llegó'}).status_code == 401
    for path in ('/metrics', '/metrics/models', '/metrics/dataset', '/metrics/test'):
        assert client.get(path).status_code == 401
    assert client.get('/metrics', headers={'Authorization': 'Bearer admin.9999999999.firmafalsa'}).status_code == 401


def test_invalid_input_and_clarification(client, auth):
    for body in ({'text': ''}, {'text': '   '}, {'text': '😊!!!'}, {'text': 'a' * 3001},
                 {'text': 123}, {}, {'text': None}):
        assert client.post('/predict', json=body, headers=auth).status_code == 422
    for text in ('Hola', 'zzzxxxqqqunseenword'):
        r = client.post('/predict', json={'text': text}, headers=auth)
        assert r.status_code == 200
        assert r.json()['requires_clarification'] is True
        assert r.json()['label'] is None


def test_health_and_metrics_without_exposing_example_text(client, auth):
    health = client.get('/health')
    assert health.status_code == 200 and health.json()['status'] == 'ok'
    response = client.get('/metrics', headers=auth)
    assert response.status_code == 200
    metrics = response.json()
    assert metrics['model_sha256'] == health.json()['model_sha256']
    assert len(metrics['validation']['confusion_matrix']) == 6
    assert {item['model'] for item in metrics['model_comparison']} == {'naive_bayes', 'logistic_regression', 'knn'}
    assert 'errors' not in json.dumps(metrics)


def test_models_dataset_and_test_metrics(client, auth):
    models = client.get('/metrics/models', headers=auth)
    assert models.status_code == 200
    data = models.json()
    assert set(data['finalists']) == {'naive_bayes', 'logistic_regression', 'knn'}
    assert sum(f['is_selected'] for f in data['finalists'].values()) == 1
    for finalist in data['finalists'].values():
        assert len(finalist['cv']['out_of_fold']['confusion_matrix']) == 6
        assert 'Marketing' in finalist['evaluations']['validation']['classification_report']
    assert {(p['a'], p['b']) for p in data['paired']} >= {('naive_bayes', 'knn')}
    dataset = client.get('/metrics/dataset', headers=auth).json()
    assert dataset['splits']['train']['records'] > 0 and 'words_histogram' in dataset['splits']['train']
    test = client.get('/metrics/test', headers=auth).json()
    assert test['estado'] in ('pendiente', 'evaluado')
    for payload in (data, dataset, test):
        assert '"text"' not in json.dumps(payload) and 'errors' not in json.dumps(payload)


def test_missing_model_and_changed_artifact_return_503(client, auth, tmp_path, monkeypatch):
    source = settings.model_dir
    monkeypatch.setattr(settings, 'model_dir', tmp_path / 'sin_modelo')
    assert client.get('/health').status_code == 503
    assert client.post('/predict', json={'text': 'Mi factura no llegó'}, headers=auth).status_code == 503
    copy = tmp_path / 'copia'
    copy.mkdir()
    shutil.copy(source / 'pipeline.joblib', copy / 'pipeline.joblib')
    monkeypatch.setattr(settings, 'model_dir', copy)
    assert client.get('/health').status_code == 200
    (copy / 'pipeline.joblib').write_bytes(b'changed')
    assert client.get('/health').status_code == 503


def test_missing_or_inconsistent_metrics_return_503(client, auth, tmp_path, monkeypatch):
    source = settings.model_dir
    copy = tmp_path / 'copia'
    copy.mkdir()
    shutil.copy(source / 'pipeline.joblib', copy / 'pipeline.joblib')
    monkeypatch.setattr(settings, 'model_dir', copy)
    assert client.get('/metrics', headers=auth).status_code == 503
    report = json.loads((source / 'report.json').read_text(encoding='utf-8'))
    report['selected']['method'] = 'stemming' if report['selected']['method'] == 'lemmatization' else 'lemmatization'
    (copy / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    assert client.get('/metrics', headers=auth).status_code == 503
    assert client.get('/metrics/models', headers=auth).status_code == 503
