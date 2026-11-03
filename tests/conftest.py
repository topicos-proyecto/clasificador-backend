import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.main import app
from api.model import load_model
from api.routers.public import limiter


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'db_path', tmp_path / 'tickets.db')
    monkeypatch.setattr(settings, 'admin_password', 'secreto-de-prueba')
    monkeypatch.setattr(settings, 'secret_key', 'clave-de-prueba')
    monkeypatch.setattr(settings, 'review_threshold', None)
    limiter.reset()
    load_model.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    load_model.cache_clear()


@pytest.fixture
def auth(client):
    token = client.post('/admin/login', json={'password': 'secreto-de-prueba'}).json()['token']
    return {'Authorization': f'Bearer {token}'}
