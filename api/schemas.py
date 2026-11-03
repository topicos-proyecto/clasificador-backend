"""Modelos de entrada validados."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictStr, field_validator

from src.nlp.preprocessing import normalize

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
STATES = Literal['nuevo', 'revision_manual', 'asignado', 'en_progreso', 'resuelto', 'cerrado']


def _message(value: str) -> str:
    if not value.strip() or not normalize(value):
        raise ValueError('Escribe un mensaje con texto legible.')
    return value.strip()


def _email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not EMAIL.match(value):
        raise ValueError('Correo electrónico no válido.')
    return value


class PredictionInput(BaseModel):
    text: Annotated[StrictStr, Field(min_length=1, max_length=3000)]

    @field_validator('text')
    @classmethod
    def check_text(cls, value: str) -> str:
        return _message(value)


class PublicTicketInput(BaseModel):
    nombre: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    email: Annotated[StrictStr | None, Field(default=None, max_length=200)] = None
    mensaje: Annotated[StrictStr, Field(min_length=1, max_length=3000)]

    @field_validator('nombre')
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not normalize(value):
            raise ValueError('Escribe tu nombre.')
        return ' '.join(value.split())

    @field_validator('email')
    @classmethod
    def check_email(cls, value: str | None) -> str | None:
        return _email(value)

    @field_validator('mensaje')
    @classmethod
    def check_message(cls, value: str) -> str:
        return _message(value)


class LoginInput(BaseModel):
    password: Annotated[StrictStr, Field(min_length=1, max_length=200)]


class AreaInput(BaseModel):
    nombre: Annotated[StrictStr, Field(min_length=2, max_length=80)]
    descripcion: Annotated[StrictStr, Field(max_length=300)] = ''

    @field_validator('nombre')
    @classmethod
    def clean(cls, value: str) -> str:
        value = ' '.join(value.split())
        if len(value) < 2:
            raise ValueError('El nombre debe tener al menos 2 caracteres.')
        return value


class AreaUpdate(BaseModel):
    nombre: Annotated[StrictStr | None, Field(default=None, min_length=2, max_length=80)] = None
    descripcion: Annotated[StrictStr | None, Field(default=None, max_length=300)] = None
    activa: bool | None = None


class EmployeeInput(BaseModel):
    nombre: Annotated[StrictStr, Field(min_length=2, max_length=120)]
    email: Annotated[StrictStr, Field(min_length=3, max_length=200)]
    area_id: int
    cargo: Annotated[StrictStr, Field(max_length=120)] = ''

    @field_validator('email')
    @classmethod
    def check_email(cls, value: str) -> str:
        result = _email(value)
        if result is None:
            raise ValueError('El correo es obligatorio.')
        return result


class EmployeeUpdate(BaseModel):
    nombre: Annotated[StrictStr | None, Field(default=None, min_length=2, max_length=120)] = None
    email: Annotated[StrictStr | None, Field(default=None, max_length=200)] = None
    area_id: int | None = None
    cargo: Annotated[StrictStr | None, Field(default=None, max_length=120)] = None
    activo: bool | None = None

    @field_validator('email')
    @classmethod
    def check_email(cls, value: str | None) -> str | None:
        return _email(value)


class TicketUpdate(BaseModel):
    area_id: int | None = None
    empleado_id: int | None = None
    quitar_empleado: bool = False
    estado: STATES | None = None
    nota: Annotated[StrictStr | None, Field(default=None, max_length=500)] = None


class NotificationUpdate(BaseModel):
    leida: bool
