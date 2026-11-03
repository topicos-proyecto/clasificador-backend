import pytest

from api.config import settings


def submit(client, mensaje, nombre='María López', email=None):
    return client.post('/public/tickets', json={'nombre': nombre, 'email': email, 'mensaje': mensaje})


def area_id(client, auth, nombre):
    return next(a['id'] for a in client.get('/admin/areas', headers=auth).json() if a['nombre'] == nombre)


def latest(client, auth):
    item = client.get('/admin/tickets', headers=auth).json()['items'][0]
    return client.get(f"/admin/tickets/{item['id']}", headers=auth).json()


def test_public_ticket_is_routed_and_area_notified_without_revealing_label(client, auth):
    response = submit(client, 'No me llegó la factura de octubre y me cobraron dos veces')
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {'codigo', 'mensaje'} and body['codigo'].startswith('TK-')
    assert 'atendida' in body['mensaje']
    ticket = latest(client, auth)
    assert ticket['codigo'] == body['codigo'] and ticket['area_nombre'] == 'Finanzas'
    assert ticket['estado'] == 'nuevo' and not ticket['requiere_revision']
    notes = client.get('/admin/notificaciones', headers=auth, params={'area_id': ticket['area_id']}).json()
    assert notes['items'][0]['cuerpo'].startswith('Cliente: María López — Mensaje:')
    assert notes['no_leidas_total'] == 1
    assert [e['tipo'] for e in ticket['eventos']][:3] == ['creado', 'clasificacion', 'enrutado']


def test_greeting_goes_to_manual_review_without_notification(client, auth):
    assert submit(client, 'Hola').status_code == 201
    ticket = latest(client, auth)
    assert ticket['estado'] == 'revision_manual' and ticket['requiere_revision'] and ticket['area_id'] is None
    assert client.get('/admin/notificaciones', headers=auth).json()['items'] == []


def test_inactive_area_sends_ticket_to_manual_review(client, auth):
    finance = area_id(client, auth, 'Finanzas')
    assert client.patch(f'/admin/areas/{finance}', json={'activa': False}, headers=auth).status_code == 200
    submit(client, 'Necesito una copia de mi factura')
    assert latest(client, auth)['estado'] == 'revision_manual'


def test_public_validation_and_rate_limit(client, monkeypatch):
    assert submit(client, '').status_code == 422
    assert submit(client, 'a' * 3001).status_code == 422
    assert submit(client, 'Mi pedido no llegó', nombre='').status_code == 422
    assert submit(client, 'Mi pedido no llegó', email='no-es-correo').status_code == 422
    monkeypatch.setattr(settings, 'rate_limit_per_minute', 2)
    assert submit(client, 'Mi pedido no llegó').status_code == 201
    assert submit(client, 'Mi pedido no llegó').status_code == 201
    assert submit(client, 'Mi pedido no llegó').status_code == 429


def test_admin_requires_token_and_password(client):
    for path in ('/admin/dashboard', '/admin/areas', '/admin/empleados', '/admin/tickets', '/admin/notificaciones'):
        assert client.get(path).status_code == 401
    assert client.post('/admin/login', json={'password': 'incorrecta'}).status_code == 401


def test_login_without_configured_password_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, 'admin_password', None)
    assert client.post('/admin/login', json={'password': 'x'}).status_code == 503


def test_assignment_and_state_transitions(client, auth):
    submit(client, 'Mi paquete todavía no llegó')
    ticket = latest(client, auth)
    assert ticket['area_nombre'] == 'Logística'
    employees = client.get('/admin/empleados', headers=auth).json()
    other = next(e for e in employees if e['area_id'] != ticket['area_id'])
    same = next(e for e in employees if e['area_id'] == ticket['area_id'])
    url = f"/admin/tickets/{ticket['id']}"
    assert client.patch(url, json={'empleado_id': other['id']}, headers=auth).status_code == 422
    assert client.patch(url, json={'estado': 'en_progreso'}, headers=auth).status_code == 409
    assert client.patch(url, json={'estado': 'asignado'}, headers=auth).status_code == 422  # sin empleado
    assigned = client.patch(url, json={'empleado_id': same['id']}, headers=auth).json()
    assert assigned['estado'] == 'asignado' and assigned['empleado_nombre'] == same['nombre']
    assert client.patch(url, json={'estado': 'en_progreso'}, headers=auth).json()['estado'] == 'en_progreso'
    assert client.patch(url, json={'estado': 'nuevo'}, headers=auth).status_code == 409
    assert client.patch(url, json={'estado': 'resuelto'}, headers=auth).status_code == 200
    assert client.patch(url, json={'estado': 'cerrado'}, headers=auth).status_code == 200
    assert client.patch(url, json={'estado': 'en_progreso'}, headers=auth).status_code == 409
    dashboard = client.get('/admin/dashboard', headers=auth).json()
    assert dashboard['por_estado']['cerrado'] == 1 and dashboard['resueltos_hoy'] == 1


def test_reassignment_marks_correction_and_notifies_new_area(client, auth):
    submit(client, 'Mi paquete todavía no llegó')
    ticket = latest(client, auth)
    sales = area_id(client, auth, 'Ventas')
    updated = client.patch(f"/admin/tickets/{ticket['id']}", json={'area_id': sales}, headers=auth).json()
    assert updated['area_nombre'] == 'Ventas' and updated['corregido'] is True
    assert client.get('/admin/notificaciones', headers=auth, params={'area_id': sales}).json()['items']
    assert client.get('/admin/dashboard', headers=auth).json()['tasa_correccion'] == 1.0


def test_manual_review_ticket_can_be_routed_by_admin(client, auth):
    submit(client, 'Hola')
    ticket = latest(client, auth)
    legal = client.post('/admin/areas', json={'nombre': 'Legal', 'descripcion': 'Reclamos formales'}, headers=auth).json()
    updated = client.patch(f"/admin/tickets/{ticket['id']}", json={'area_id': legal['id']}, headers=auth).json()
    assert updated['estado'] == 'nuevo' and updated['requiere_revision'] is False and updated['corregido'] is False


def test_area_rules(client, auth):
    finance = area_id(client, auth, 'Finanzas')
    assert client.delete(f'/admin/areas/{finance}', headers=auth).status_code == 409
    created = client.post('/admin/areas', json={'nombre': 'Legal'}, headers=auth)
    assert created.status_code == 201 and created.json()['asignable_por_modelo'] is False
    assert client.post('/admin/areas', json={'nombre': 'legal'}, headers=auth).status_code == 409
    legal = created.json()['id']
    employee = client.post('/admin/empleados', json={'nombre': 'Ana Ruiz', 'email': 'ana@empresa.example',
                                                     'area_id': legal}, headers=auth)
    assert employee.status_code == 201
    assert client.delete(f'/admin/areas/{legal}', headers=auth).status_code == 409
    assert client.delete(f"/admin/empleados/{employee.json()['id']}", headers=auth).json()['eliminado'] is True
    assert client.delete(f'/admin/areas/{legal}', headers=auth).status_code == 200


def test_employee_with_open_tickets_cannot_be_deactivated(client, auth):
    submit(client, 'Mi paquete todavía no llegó')
    ticket = latest(client, auth)
    same = next(e for e in client.get('/admin/empleados', headers=auth, params={'area_id': ticket['area_id']}).json())
    client.patch(f"/admin/tickets/{ticket['id']}", json={'empleado_id': same['id']}, headers=auth)
    assert client.patch(f"/admin/empleados/{same['id']}", json={'activo': False}, headers=auth).status_code == 409
    assert client.post('/admin/empleados', json={'nombre': 'Duplicado', 'email': same['email'],
                                                 'area_id': ticket['area_id']}, headers=auth).status_code == 409


def test_notifications_can_be_marked_read(client, auth):
    submit(client, 'No puedo iniciar sesión en la aplicación')
    notes = client.get('/admin/notificaciones', headers=auth).json()
    note = notes['items'][0]
    assert client.patch(f"/admin/notificaciones/{note['id']}", json={'leida': True}, headers=auth).json()['leida']
    assert client.get('/admin/notificaciones', headers=auth).json()['no_leidas_total'] == 0


def test_ticket_filters_and_pagination(client, auth):
    for text in ('Mi paquete todavía no llegó', 'Hola', 'Necesito una copia de mi factura'):
        submit(client, text)
    assert client.get('/admin/tickets', headers=auth, params={'requiere_revision': True}).json()['total'] == 1
    assert client.get('/admin/tickets', headers=auth, params={'q': 'factura'}).json()['total'] == 1
    page = client.get('/admin/tickets', headers=auth, params={'page_size': 2}).json()
    assert page['total'] == 3 and len(page['items']) == 2


def test_ticket_survives_unavailable_model(client, auth, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'model_dir', tmp_path / 'sin_modelo')
    assert submit(client, 'Mi paquete todavía no llegó').status_code == 201
    ticket = latest(client, auth)
    assert ticket['estado'] == 'revision_manual' and 'Modelo no disponible' in ticket['motivo_revision']


def test_review_threshold_sends_low_confidence_to_review(client, auth, monkeypatch):
    monkeypatch.setattr(settings, 'review_threshold', 0.999999)
    submit(client, 'tengo un problema')
    assert latest(client, auth)['estado'] == 'revision_manual'
