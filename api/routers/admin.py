"""Panel administrativo: dashboard, áreas, empleados, tickets y notificaciones."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import check_password, issue_token, require_admin
from api.db import OPEN_STATES, get_db, now
from api.schemas import (AreaInput, AreaUpdate, EmployeeInput, EmployeeUpdate, LoginInput, NotificationUpdate,
                         TicketUpdate)
from api.services.routing import TRANSITIONS, RoutingError, update_ticket

router = APIRouter(prefix='/admin')
protected = APIRouter(prefix='/admin', dependencies=[Depends(require_admin)])
OPEN = ','.join(f"'{s}'" for s in OPEN_STATES)
TICKET_SELECT = """
SELECT t.id, t.codigo, t.cliente_nombre, t.cliente_email, t.mensaje, t.etiqueta_predicha, t.confianza,
       t.probabilidades, t.area_id, a.nombre AS area_nombre, t.empleado_id, e.nombre AS empleado_nombre,
       t.estado, t.requiere_revision, t.motivo_revision, t.corregido, t.creado_en, t.actualizado_en
FROM tickets t LEFT JOIN areas a ON a.id = t.area_id LEFT JOIN empleados e ON e.id = t.empleado_id
"""


def ticket_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d['probabilidades'] = json.loads(d['probabilidades'] or '{}')
    d['requiere_revision'] = bool(d['requiere_revision'])
    d['corregido'] = bool(d['corregido'])
    d['transiciones'] = sorted(TRANSITIONS[d['estado']])
    return d


def get_or_404(conn, table: str, item_id: int, label: str) -> sqlite3.Row:
    found = conn.execute(f'SELECT * FROM {table} WHERE id = ?', (item_id,)).fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail=f'{label} no encontrado.')
    return found


# --- Sesión ---------------------------------------------------------------

@router.post('/login')
def login(payload: LoginInput):
    if not check_password(payload.password):
        raise HTTPException(status_code=401, detail='Contraseña incorrecta.')
    token, expires = issue_token()
    return {'token': token, 'expira': datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec='seconds')}


# --- Dashboard ------------------------------------------------------------

@protected.get('/dashboard')
def dashboard(conn: sqlite3.Connection = Depends(get_db)):
    states = {r['estado']: r['n'] for r in conn.execute('SELECT estado, COUNT(*) n FROM tickets GROUP BY estado')}
    by_area = [dict(r) for r in conn.execute(
        f"""SELECT a.id, a.nombre, a.activa, a.etiqueta_modelo,
                   SUM(CASE WHEN t.estado IN ({OPEN}) THEN 1 ELSE 0 END) abiertos, COUNT(t.id) total,
                   (SELECT COUNT(*) FROM notificaciones n WHERE n.area_id = a.id AND n.leida = 0) no_leidas
            FROM areas a LEFT JOIN tickets t ON t.area_id = a.id GROUP BY a.id ORDER BY a.nombre""")]
    predicted = conn.execute('SELECT COUNT(*) FROM tickets WHERE etiqueta_predicha IS NOT NULL').fetchone()[0]
    corrected = conn.execute('SELECT COUNT(*) FROM tickets WHERE corregido = 1').fetchone()[0]
    today = datetime.now(timezone.utc).date().isoformat()
    resolved_today = conn.execute("SELECT COUNT(*) FROM tickets WHERE estado IN ('resuelto','cerrado') "
                                  'AND substr(actualizado_en, 1, 10) = ?', (today,)).fetchone()[0]
    latest = [ticket_dict(r) for r in conn.execute(TICKET_SELECT + ' ORDER BY t.id DESC LIMIT 8')]
    return {'por_estado': {s: states.get(s, 0) for s in TRANSITIONS}, 'total': sum(states.values()),
            'por_area': by_area, 'revision_manual': states.get('revision_manual', 0),
            'resueltos_hoy': resolved_today,
            'clasificados_por_modelo': predicted, 'corregidos': corrected,
            'tasa_correccion': corrected / predicted if predicted else None,
            'ultimos': latest}


# --- Áreas ----------------------------------------------------------------

AREA_SELECT = f"""
SELECT a.*, (SELECT COUNT(*) FROM empleados e WHERE e.area_id = a.id AND e.activo = 1) empleados_activos,
       (SELECT COUNT(*) FROM tickets t WHERE t.area_id = a.id AND t.estado IN ({OPEN})) tickets_abiertos,
       (SELECT COUNT(*) FROM notificaciones n WHERE n.area_id = a.id AND n.leida = 0) no_leidas
FROM areas a
"""


def area_dict(r) -> dict:
    d = dict(r)
    d['activa'] = bool(d['activa'])
    d['asignable_por_modelo'] = d['etiqueta_modelo'] is not None
    return d


@protected.get('/areas')
def list_areas(conn: sqlite3.Connection = Depends(get_db)):
    return [area_dict(r) for r in conn.execute(AREA_SELECT + ' ORDER BY a.etiqueta_modelo IS NULL, a.nombre')]


@protected.post('/areas', status_code=201)
def create_area(payload: AreaInput, conn: sqlite3.Connection = Depends(get_db)):
    try:
        area_id = conn.execute('INSERT INTO areas (nombre, descripcion, etiqueta_modelo, activa, creada_en) '
                               'VALUES (?, ?, NULL, 1, ?)', (payload.nombre, payload.descripcion.strip(), now())).lastrowid
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail='Ya existe un área con ese nombre.') from exc
    return area_dict(conn.execute(AREA_SELECT + ' WHERE a.id = ?', (area_id,)).fetchone())


@protected.patch('/areas/{area_id}')
def update_area(area_id: int, payload: AreaUpdate, conn: sqlite3.Connection = Depends(get_db)):
    area = get_or_404(conn, 'areas', area_id, 'Área')
    name = ' '.join(payload.nombre.split()) if payload.nombre is not None else area['nombre']
    description = payload.descripcion.strip() if payload.descripcion is not None else area['descripcion']
    active = int(payload.activa) if payload.activa is not None else area['activa']
    try:
        conn.execute('UPDATE areas SET nombre = ?, descripcion = ?, activa = ? WHERE id = ?',
                     (name, description, active, area_id))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail='Ya existe un área con ese nombre.') from exc
    return area_dict(conn.execute(AREA_SELECT + ' WHERE a.id = ?', (area_id,)).fetchone())


@protected.delete('/areas/{area_id}')
def delete_area(area_id: int, conn: sqlite3.Connection = Depends(get_db)):
    area = get_or_404(conn, 'areas', area_id, 'Área')
    if area['etiqueta_modelo'] is not None:
        raise HTTPException(status_code=409, detail='Las áreas que conoce el modelo no se eliminan; desactívala.')
    used = conn.execute('SELECT (SELECT COUNT(*) FROM tickets WHERE area_id = ?) + '
                        '(SELECT COUNT(*) FROM empleados WHERE area_id = ?)', (area_id, area_id)).fetchone()[0]
    if used:
        raise HTTPException(status_code=409, detail='El área tiene tickets o empleados; desactívala en su lugar.')
    conn.execute('DELETE FROM notificaciones WHERE area_id = ?', (area_id,))
    conn.execute('DELETE FROM areas WHERE id = ?', (area_id,))
    conn.commit()
    return {'eliminada': True}


# --- Empleados ------------------------------------------------------------

EMPLOYEE_SELECT = f"""
SELECT e.*, a.nombre AS area_nombre,
       (SELECT COUNT(*) FROM tickets t WHERE t.empleado_id = e.id AND t.estado IN ({OPEN})) tickets_abiertos
FROM empleados e JOIN areas a ON a.id = e.area_id
"""


def employee_dict(r) -> dict:
    d = dict(r)
    d['activo'] = bool(d['activo'])
    return d


@protected.get('/empleados')
def list_employees(area_id: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    query, params = EMPLOYEE_SELECT, ()
    if area_id is not None:
        query, params = query + ' WHERE e.area_id = ?', (area_id,)
    return [employee_dict(r) for r in conn.execute(query + ' ORDER BY a.nombre, e.nombre', params)]


@protected.post('/empleados', status_code=201)
def create_employee(payload: EmployeeInput, conn: sqlite3.Connection = Depends(get_db)):
    get_or_404(conn, 'areas', payload.area_id, 'Área')
    try:
        employee_id = conn.execute('INSERT INTO empleados (nombre, email, area_id, cargo, activo, creado_en) '
                                   'VALUES (?, ?, ?, ?, 1, ?)',
                                   (' '.join(payload.nombre.split()), payload.email, payload.area_id,
                                    payload.cargo.strip(), now())).lastrowid
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail='Ya existe un empleado con ese correo.') from exc
    return employee_dict(conn.execute(EMPLOYEE_SELECT + ' WHERE e.id = ?', (employee_id,)).fetchone())


@protected.patch('/empleados/{employee_id}')
def update_employee(employee_id: int, payload: EmployeeUpdate, conn: sqlite3.Connection = Depends(get_db)):
    employee = get_or_404(conn, 'empleados', employee_id, 'Empleado')
    open_tickets = conn.execute(f'SELECT COUNT(*) FROM tickets WHERE empleado_id = ? AND estado IN ({OPEN})',
                                (employee_id,)).fetchone()[0]
    area_id = payload.area_id if payload.area_id is not None else employee['area_id']
    if area_id != employee['area_id']:
        get_or_404(conn, 'areas', area_id, 'Área')
        if open_tickets:
            raise HTTPException(status_code=409, detail='El empleado tiene tickets abiertos; reasígnalos antes de cambiar su área.')
    if payload.activo is False and open_tickets:
        raise HTTPException(status_code=409, detail='El empleado tiene tickets abiertos; reasígnalos antes de desactivarlo.')
    values = (' '.join(payload.nombre.split()) if payload.nombre else employee['nombre'],
              payload.email or employee['email'], area_id,
              payload.cargo.strip() if payload.cargo is not None else employee['cargo'],
              int(payload.activo) if payload.activo is not None else employee['activo'], employee_id)
    try:
        conn.execute('UPDATE empleados SET nombre = ?, email = ?, area_id = ?, cargo = ?, activo = ? WHERE id = ?', values)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail='Ya existe un empleado con ese correo.') from exc
    return employee_dict(conn.execute(EMPLOYEE_SELECT + ' WHERE e.id = ?', (employee_id,)).fetchone())


@protected.delete('/empleados/{employee_id}')
def delete_employee(employee_id: int, conn: sqlite3.Connection = Depends(get_db)):
    get_or_404(conn, 'empleados', employee_id, 'Empleado')
    total = conn.execute('SELECT COUNT(*) FROM tickets WHERE empleado_id = ?', (employee_id,)).fetchone()[0]
    if total:
        open_tickets = conn.execute(f'SELECT COUNT(*) FROM tickets WHERE empleado_id = ? AND estado IN ({OPEN})',
                                    (employee_id,)).fetchone()[0]
        if open_tickets:
            raise HTTPException(status_code=409, detail='El empleado tiene tickets abiertos; reasígnalos primero.')
        conn.execute('UPDATE empleados SET activo = 0 WHERE id = ?', (employee_id,))
        conn.commit()
        return {'eliminado': False, 'desactivado': True,
                'detalle': 'Tiene tickets históricos: se desactivó para conservar la bitácora.'}
    conn.execute('DELETE FROM empleados WHERE id = ?', (employee_id,))
    conn.commit()
    return {'eliminado': True, 'desactivado': False}


# --- Tickets --------------------------------------------------------------

@protected.get('/tickets')
def list_tickets(estado: str | None = None, area_id: int | None = None, empleado_id: int | None = None,
                 requiere_revision: bool | None = None, q: str | None = Query(default=None, max_length=200),
                 page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                 conn: sqlite3.Connection = Depends(get_db)):
    where, params = [], []
    if estado:
        if estado == 'abiertos':
            where.append(f't.estado IN ({OPEN})')
        else:
            where.append('t.estado = ?'); params.append(estado)
    if area_id is not None:
        where.append('t.area_id = ?'); params.append(area_id)
    if empleado_id is not None:
        where.append('t.empleado_id = ?'); params.append(empleado_id)
    if requiere_revision is not None:
        where.append('t.requiere_revision = ?'); params.append(int(requiere_revision))
    if q and q.strip():
        where.append('(t.mensaje LIKE ? OR t.cliente_nombre LIKE ? OR t.codigo LIKE ?)')
        params += [f'%{q.strip()}%'] * 3
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = conn.execute('SELECT COUNT(*) FROM tickets t' + clause, params).fetchone()[0]
    rows = conn.execute(TICKET_SELECT + clause + ' ORDER BY t.id DESC LIMIT ? OFFSET ?',
                        [*params, page_size, (page - 1) * page_size])
    return {'items': [ticket_dict(r) for r in rows], 'total': total, 'page': page, 'page_size': page_size}


def ticket_detail(conn, ticket_id: int) -> dict:
    found = conn.execute(TICKET_SELECT + ' WHERE t.id = ?', (ticket_id,)).fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail='Ticket no encontrado.')
    detail = ticket_dict(found)
    detail['eventos'] = [{**dict(r), 'detalle': json.loads(r['detalle'])} for r in conn.execute(
        'SELECT id, tipo, detalle, actor, creado_en FROM eventos_ticket WHERE ticket_id = ? ORDER BY id', (ticket_id,))]
    return detail


@protected.get('/tickets/{ticket_id}')
def get_ticket(ticket_id: int, conn: sqlite3.Connection = Depends(get_db)):
    return ticket_detail(conn, ticket_id)


@protected.patch('/tickets/{ticket_id}')
def patch_ticket(ticket_id: int, payload: TicketUpdate, conn: sqlite3.Connection = Depends(get_db)):
    try:
        update_ticket(conn, ticket_id, payload.model_dump())
    except RoutingError as exc:
        conn.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return ticket_detail(conn, ticket_id)


# --- Notificaciones -------------------------------------------------------

@protected.get('/notificaciones')
def list_notifications(area_id: int | None = None, solo_no_leidas: bool = False,
                       limit: int = Query(default=50, ge=1, le=200), conn: sqlite3.Connection = Depends(get_db)):
    where, params = [], []
    if area_id is not None:
        where.append('n.area_id = ?'); params.append(area_id)
    if solo_no_leidas:
        where.append('n.leida = 0')
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    rows = conn.execute('SELECT n.*, a.nombre AS area_nombre, t.codigo, t.estado AS ticket_estado '
                        'FROM notificaciones n JOIN areas a ON a.id = n.area_id JOIN tickets t ON t.id = n.ticket_id'
                        + clause + ' ORDER BY n.id DESC LIMIT ?', [*params, limit])
    unread = {r['area_id']: r['n'] for r in conn.execute(
        'SELECT area_id, COUNT(*) n FROM notificaciones WHERE leida = 0 GROUP BY area_id')}
    return {'items': [{**dict(r), 'leida': bool(r['leida'])} for r in rows], 'no_leidas_por_area': unread,
            'no_leidas_total': sum(unread.values())}


@protected.patch('/notificaciones/{notification_id}')
def mark_notification(notification_id: int, payload: NotificationUpdate, conn: sqlite3.Connection = Depends(get_db)):
    get_or_404(conn, 'notificaciones', notification_id, 'Notificación')
    conn.execute('UPDATE notificaciones SET leida = ? WHERE id = ?', (int(payload.leida), notification_id))
    conn.commit()
    return {'id': notification_id, 'leida': payload.leida}


@protected.post('/notificaciones/marcar-leidas')
def mark_all(area_id: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    if area_id is None:
        changed = conn.execute('UPDATE notificaciones SET leida = 1 WHERE leida = 0').rowcount
    else:
        changed = conn.execute('UPDATE notificaciones SET leida = 1 WHERE leida = 0 AND area_id = ?', (area_id,)).rowcount
    conn.commit()
    return {'marcadas': changed}
