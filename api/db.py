"""Persistencia SQLite (módulo estándar) para áreas, empleados, tickets, notificaciones y bitácora."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from api.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS areas (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
    descripcion TEXT NOT NULL DEFAULT '',
    etiqueta_modelo TEXT UNIQUE,
    activa INTEGER NOT NULL DEFAULT 1,
    creada_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS empleados (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    area_id INTEGER NOT NULL REFERENCES areas(id),
    cargo TEXT NOT NULL DEFAULT '',
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    codigo TEXT UNIQUE,
    cliente_nombre TEXT NOT NULL,
    cliente_email TEXT,
    mensaje TEXT NOT NULL,
    etiqueta_predicha TEXT,
    confianza REAL,
    probabilidades TEXT NOT NULL DEFAULT '{}',
    area_id INTEGER REFERENCES areas(id),
    empleado_id INTEGER REFERENCES empleados(id),
    estado TEXT NOT NULL,
    requiere_revision INTEGER NOT NULL DEFAULT 0,
    motivo_revision TEXT,
    corregido INTEGER NOT NULL DEFAULT 0,
    modelo_sha256 TEXT,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notificaciones (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    area_id INTEGER NOT NULL REFERENCES areas(id),
    titulo TEXT NOT NULL,
    cuerpo TEXT NOT NULL,
    leida INTEGER NOT NULL DEFAULT 0,
    creada_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eventos_ticket (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    tipo TEXT NOT NULL,
    detalle TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL,
    creado_en TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_estado ON tickets(estado);
CREATE INDEX IF NOT EXISTS idx_tickets_area ON tickets(area_id);
CREATE INDEX IF NOT EXISTS idx_notificaciones_area ON notificaciones(area_id, leida);
CREATE INDEX IF NOT EXISTS idx_eventos_ticket ON eventos_ticket(ticket_id);
"""

# Las seis áreas que conoce el modelo; su etiqueta no se puede cambiar ni eliminar.
SEED_AREAS = [
    ('Soporte TI', 'Fallos de aplicaciones, accesos o sistemas.', 'Soporte TI'),
    ('Finanzas', 'Cobros, reembolsos y facturas.', 'Finanzas'),
    ('Ventas', 'Contrataciones, precios y cotizaciones.', 'Ventas'),
    ('Marketing', 'Promociones, cupones y campañas.', 'Marketing'),
    ('Atención al Cliente', 'Consultas generales y orientación.', 'Atención al Cliente'),
    ('Logística', 'Entregas, pedidos y envíos.', 'Logística'),
]
# Personas ficticias para la simulación.
SEED_EMPLOYEES = {
    'Soporte TI': [('Lucía Fernández', 'Analista de soporte'), ('Diego Rojas', 'Técnico de sistemas'),
                   ('Valeria Quispe', 'Responsable de TI')],
    'Finanzas': [('Martín Gutiérrez', 'Analista de facturación'), ('Camila Torres', 'Responsable de cobranzas')],
    'Ventas': [('Andrés Mamani', 'Ejecutivo comercial'), ('Sofía Vargas', 'Ejecutiva de cuentas')],
    'Marketing': [('Paula Méndez', 'Especialista en campañas'), ('Javier Castro', 'Analista de promociones')],
    'Atención al Cliente': [('Carla Romero', 'Agente de atención'), ('Luis Herrera', 'Agente de atención'),
                            ('Natalia Flores', 'Supervisora')],
    'Logística': [('Ricardo Paredes', 'Coordinador de envíos'), ('Elena Suárez', 'Analista de despachos')],
}
OPEN_STATES = ('nuevo', 'revision_manual', 'asignado', 'en_progreso')


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def slug_email(name: str) -> str:
    import unicodedata
    plain = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    first, *rest = plain.split()
    return f"{first}.{rest[-1] if rest else 'equipo'}@empresa.example"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db(path: Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        if conn.execute('SELECT COUNT(*) FROM areas').fetchone()[0] == 0:
            stamp = now()
            for name, description, label in SEED_AREAS:
                area_id = conn.execute(
                    'INSERT INTO areas (nombre, descripcion, etiqueta_modelo, activa, creada_en) VALUES (?, ?, ?, 1, ?)',
                    (name, description, label, stamp)).lastrowid
                for person, role in SEED_EMPLOYEES[name]:
                    conn.execute('INSERT INTO empleados (nombre, email, area_id, cargo, activo, creado_en) '
                                 'VALUES (?, ?, ?, ?, 1, ?)', (person, slug_email(person), area_id, role, stamp))
        conn.commit()
    finally:
        conn.close()


def get_db():
    """Dependencia de FastAPI: una conexión por solicitud."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def log_event(conn: sqlite3.Connection, ticket_id: int, kind: str, detail: dict, actor: str) -> None:
    conn.execute('INSERT INTO eventos_ticket (ticket_id, tipo, detalle, actor, creado_en) VALUES (?, ?, ?, ?, ?)',
                 (ticket_id, kind, json.dumps(detail, ensure_ascii=False), actor, now()))


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None
