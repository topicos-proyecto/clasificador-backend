"""Flujo de un ticket: clasificar → decidir área → notificar al área completa → gestionar estados.

El bot no conversa con el cliente: solo clasifica y notifica. Al cliente se le responde siempre
con la misma confirmación, sin revelar el área ni el resultado del modelo.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from api.config import settings
from api.db import log_event, now
from api.model import classify, current_model

log = logging.getLogger(__name__)

CUSTOMER_MESSAGE = 'Su consulta será atendida a la brevedad. Guarde su código de seguimiento.'
# Transiciones de estado permitidas (el resto responde 409).
TRANSITIONS = {
    'nuevo': {'asignado', 'revision_manual', 'cerrado'},
    'revision_manual': {'nuevo', 'asignado', 'cerrado'},
    'asignado': {'nuevo', 'en_progreso', 'resuelto', 'revision_manual', 'cerrado'},
    'en_progreso': {'asignado', 'resuelto', 'revision_manual'},
    'resuelto': {'en_progreso', 'cerrado'},
    'cerrado': set(),
}
NEEDS_EMPLOYEE = {'asignado', 'en_progreso'}
NEEDS_AREA = {'nuevo', 'asignado', 'en_progreso', 'resuelto'}


class RoutingError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def excerpt(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def notify_area(conn: sqlite3.Connection, ticket: dict, area: dict) -> None:
    """Una notificación por área: es la bandeja compartida que ven todos sus empleados."""
    title = f"Nuevo ticket {ticket['codigo']} · {area['nombre']}"
    body = f"Cliente: {ticket['cliente_nombre']} — Mensaje: “{excerpt(ticket['mensaje'])}”"
    conn.execute('INSERT INTO notificaciones (ticket_id, area_id, titulo, cuerpo, leida, creada_en) '
                 'VALUES (?, ?, ?, ?, 0, ?)', (ticket['id'], area['id'], title, body, now()))
    log_event(conn, ticket['id'], 'notificacion', {'area': area['nombre']}, 'bot')
    notify_external(ticket, area)


def notify_external(ticket: dict, area: dict) -> None:
    """Punto de extensión para correo o webhook. No se implementa: ALCANCE.md excluye integraciones reales."""


def create_ticket(conn: sqlite3.Connection, name: str, email: str | None, message: str) -> dict:
    stamp = now()
    ticket_id = conn.execute(
        'INSERT INTO tickets (cliente_nombre, cliente_email, mensaje, estado, creado_en, actualizado_en) '
        "VALUES (?, ?, ?, 'nuevo', ?, ?)", (name, email, message, stamp, stamp)).lastrowid
    code = f'TK-{stamp[:4]}-{ticket_id:06d}'
    conn.execute('UPDATE tickets SET codigo = ? WHERE id = ?', (code, ticket_id))
    log_event(conn, ticket_id, 'creado', {'canal': 'web /soporte'}, 'cliente')

    label, confidence, probabilities, model_sha, reason = None, None, {}, None, None
    try:
        model, model_sha = current_model()
        result = classify(model, message)
        if result['requires_clarification']:
            reason = result['reason']
        else:
            label, probabilities = result['label'], result['probabilities']
            confidence = probabilities[label]
    except Exception as exc:  # el ticket se conserva aunque el modelo no esté disponible
        log.warning('Clasificación no disponible: %s', exc)
        reason = 'Modelo no disponible; requiere clasificación manual.'
    log_event(conn, ticket_id, 'clasificacion',
              {'etiqueta': label, 'confianza': confidence, 'motivo': reason}, 'bot')

    area = None
    if label is not None:
        area = conn.execute('SELECT * FROM areas WHERE etiqueta_modelo = ?', (label,)).fetchone()
        if area is None or not area['activa']:
            reason, area = f'El área «{label}» está inactiva o no existe.', None
        elif settings.review_threshold is not None and confidence < settings.review_threshold:
            reason, area = f'Confianza {confidence:.2f} menor que el umbral provisional.', None
    state = 'nuevo' if area is not None else 'revision_manual'
    conn.execute('UPDATE tickets SET etiqueta_predicha = ?, confianza = ?, probabilidades = ?, area_id = ?, '
                 'estado = ?, requiere_revision = ?, motivo_revision = ?, modelo_sha256 = ? WHERE id = ?',
                 (label, confidence, json.dumps(probabilities), area['id'] if area else None, state,
                  int(area is None), reason, model_sha, ticket_id))
    ticket = dict(conn.execute('SELECT * FROM tickets WHERE id = ?', (ticket_id,)).fetchone())
    if area is not None:
        log_event(conn, ticket_id, 'enrutado', {'area': area['nombre']}, 'bot')
        notify_area(conn, ticket, dict(area))
    else:
        log_event(conn, ticket_id, 'revision_manual', {'motivo': reason}, 'bot')
    conn.commit()
    return ticket


def update_ticket(conn: sqlite3.Connection, ticket_id: int, changes: dict, actor: str = 'admin') -> dict:
    ticket = conn.execute('SELECT * FROM tickets WHERE id = ?', (ticket_id,)).fetchone()
    if ticket is None:
        raise RoutingError(404, 'Ticket no encontrado.')
    t = dict(ticket)
    if t['estado'] == 'cerrado':
        raise RoutingError(409, 'El ticket está cerrado.')
    original_state = t['estado']

    if changes.get('area_id') is not None and changes['area_id'] != t['area_id']:
        area = conn.execute('SELECT * FROM areas WHERE id = ?', (changes['area_id'],)).fetchone()
        if area is None:
            raise RoutingError(422, 'El área no existe.')
        if not area['activa']:
            raise RoutingError(422, 'El área está inactiva.')
        previous = t['area_id']
        t['area_id'] = area['id']
        if t['etiqueta_predicha'] is not None:
            t['corregido'] = int(area['etiqueta_modelo'] != t['etiqueta_predicha'])
        t['requiere_revision'], t['motivo_revision'] = 0, None
        if t['empleado_id'] is not None:
            employee = conn.execute('SELECT area_id FROM empleados WHERE id = ?', (t['empleado_id'],)).fetchone()
            if employee is None or employee['area_id'] != area['id']:
                t['empleado_id'] = None
                if t['estado'] in NEEDS_EMPLOYEE:
                    t['estado'] = 'nuevo'
        if t['estado'] == 'revision_manual':
            t['estado'] = 'nuevo'
        log_event(conn, ticket_id, 'reasignacion_area',
                  {'desde': previous, 'hacia': area['nombre'], 'correccion_modelo': bool(t['corregido'])}, actor)
        notify_area(conn, t, dict(area))

    if changes.get('quitar_empleado') and t['empleado_id'] is not None:
        t['empleado_id'] = None
        if t['estado'] in NEEDS_EMPLOYEE:
            t['estado'] = 'nuevo'
        log_event(conn, ticket_id, 'desasignacion', {}, actor)
    elif changes.get('empleado_id') is not None and changes['empleado_id'] != t['empleado_id']:
        employee = conn.execute('SELECT * FROM empleados WHERE id = ?', (changes['empleado_id'],)).fetchone()
        if employee is None or not employee['activo']:
            raise RoutingError(422, 'El empleado no existe o está inactivo.')
        if t['area_id'] is None or employee['area_id'] != t['area_id']:
            raise RoutingError(422, 'El empleado no pertenece al área del ticket.')
        t['empleado_id'] = employee['id']
        if t['estado'] in ('nuevo', 'revision_manual'):
            t['estado'] = 'asignado'
        log_event(conn, ticket_id, 'asignacion', {'empleado': employee['nombre']}, actor)

    target = changes.get('estado')
    if target is not None and target != t['estado']:
        if target not in TRANSITIONS[t['estado']]:
            raise RoutingError(409, f"No se puede pasar de «{t['estado']}» a «{target}».")
        t['estado'] = target
    if t['estado'] in NEEDS_EMPLOYEE and t['empleado_id'] is None:
        raise RoutingError(422, 'Asigna un empleado antes de pasar a ese estado.')
    if t['estado'] in NEEDS_AREA and t['area_id'] is None:
        raise RoutingError(422, 'Asigna un área antes de pasar a ese estado.')
    if t['estado'] == 'revision_manual':
        t['requiere_revision'] = 1
    if t['estado'] != original_state:
        log_event(conn, ticket_id, 'estado', {'desde': original_state, 'hacia': t['estado']}, actor)
    if changes.get('nota'):
        log_event(conn, ticket_id, 'nota', {'texto': changes['nota']}, actor)

    t['actualizado_en'] = now()
    conn.execute('UPDATE tickets SET area_id = ?, empleado_id = ?, estado = ?, requiere_revision = ?, '
                 'motivo_revision = ?, corregido = ?, actualizado_en = ? WHERE id = ?',
                 (t['area_id'], t['empleado_id'], t['estado'], t['requiere_revision'], t['motivo_revision'],
                  t['corregido'], t['actualizado_en'], ticket_id))
    conn.commit()
    return t
