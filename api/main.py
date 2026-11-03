"""API local del clasificador de tickets de clientes: clasificación, métricas y panel administrativo."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import init_db
from api.routers import admin, classifier, metrics, public


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title='Clasificador de tickets', version='0.2.0', lifespan=lifespan,
              description='Clasificación exploratoria de solicitudes de clientes, enrutamiento a áreas '
                          'y panel administrativo de tickets.')
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
                   allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'Authorization'])
app.include_router(classifier.router)
app.include_router(metrics.router)
app.include_router(public.router)
app.include_router(admin.router)
app.include_router(admin.protected)
