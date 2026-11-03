"""Página pública /soporte: el cliente envía su mensaje y recibe solo una confirmación."""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.config import settings
from api.db import get_db
from api.schemas import PublicTicketInput
from api.services.routing import CUSTOMER_MESSAGE, create_ticket

router = APIRouter(prefix='/public')


class RateLimiter:
    """Límite simple en memoria por IP (suficiente para la simulación local)."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        current = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and current - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(current)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


@router.post('/tickets', status_code=status.HTTP_201_CREATED)
def submit_ticket(payload: PublicTicketInput, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    client = request.client.host if request.client else 'desconocido'
    if not limiter.allow(client, settings.rate_limit_per_minute):
        raise HTTPException(status_code=429, detail='Demasiadas consultas seguidas. Inténtalo de nuevo en un minuto.')
    ticket = create_ticket(conn, payload.nombre, payload.email, payload.mensaje)
    # No se revela el área, la probabilidad ni el resultado del modelo.
    return {'codigo': ticket['codigo'], 'mensaje': CUSTOMER_MESSAGE}
