"""Autenticación simple de un único administrador con token firmado (HMAC-SHA256, biblioteca estándar).

Es deliberadamente simple para la simulación académica: no hay cuentas por empleado.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from api.config import settings


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(now: float | None = None) -> tuple[str, int]:
    expires = int((now or time.time()) + settings.token_ttl_seconds)
    payload = f'admin.{expires}'
    return f'{payload}.{_sign(payload)}', expires


def verify_token(token: str, now: float | None = None) -> bool:
    try:
        role, expires, signature = token.split('.')
    except ValueError:
        return False
    payload = f'{role}.{expires}'
    if role != 'admin' or not hmac.compare_digest(signature, _sign(payload)):
        return False
    return expires.isdigit() and int(expires) > (now or time.time())


def check_password(password: str) -> bool:
    if not settings.admin_password:
        raise HTTPException(status_code=503, detail='Inicio de sesión no configurado: define ADMIN_PASSWORD.')
    return hmac.compare_digest(password.encode(), settings.admin_password.encode())


def require_admin(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith('Bearer ') or not verify_token(authorization[7:]):
        raise HTTPException(status_code=401, detail='Sesión no válida o expirada. Inicia sesión nuevamente.',
                            headers={'WWW-Authenticate': 'Bearer'})
    return 'admin'
