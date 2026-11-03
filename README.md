# Clasificador de tickets — Backend

API FastAPI con tres funciones:

1. Clasificar solicitudes de clientes con el pipeline NLP entrenado en `../clasificador-tickets-nlp`. La lógica de NLP no se duplica: se carga `pipeline.joblib`.
2. Simular el flujo de soporte: ticket público → clasificación → notificación al área completa → asignación a un empleado.
3. Servir las métricas que justifican la elección del modelo.

Las tres carpetas deben estar juntas en el workspace.

## Preparación

Usar el entorno `.venv312` del proyecto NLP (Python 3.12, dependencias y modelo `es_core_news_sm`; ver su README). El modelo por defecto es `../clasificador-tickets-nlp/artifacts/comparison_customers_v3/`, generado con la comparación de tres modelos (`src.models.compare`, `src.data.dataset_stats` y `src.models.evaluate_test`).

Desde esta carpeta:

```bash
ADMIN_PASSWORD='elige-una-contraseña' SECRET_KEY='una-clave-larga' \
PYTHONPATH=../clasificador-tickets-nlp ../clasificador-tickets-nlp/.venv312/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

| Variable | Uso |
|---|---|
| `ADMIN_PASSWORD` | Contraseña del panel. Sin ella, `/admin/login` responde 503. |
| `SECRET_KEY` | Firma de tokens. Si falta, se genera una al iniciar y las sesiones se pierden al reiniciar. |
| `MODEL_DIR` | Directorio del modelo, relativo al proyecto NLP. Por defecto `artifacts/comparison_customers_v3`. Para usar el anterior: `artifacts/comparison_customers`. |
| `DB_PATH` | Base SQLite. Por defecto `data/tickets.db` (no versionada). Se crea con seis áreas y empleados ficticios. |
| `UMBRAL_REVISION` | Opcional, entre 0 y 1: los tickets con menor confianza van a revisión manual. Es provisional porque las probabilidades no están calibradas. |

La documentación interactiva está en `http://127.0.0.1:8000/docs`.

## Endpoints

| Ruta | Acceso | Descripción |
|---|---|---|
| `GET /health` | público | Estado del modelo y su SHA-256 |
| `POST /public/tickets` | público | `{nombre, email?, mensaje}` → `{codigo, mensaje}`; límite de 10 por minuto e IP |
| `POST /admin/login` | público | `{password}` → token (8 h) |
| `POST /predict` | admin | Clase, probabilidades y trazas NLP |
| `GET /metrics` | admin | Métricas del modelo seleccionado |
| `GET /metrics/models` | admin | Finalistas NB, LR y KNN: CV, matrices de confusión, métricas por clase, costo y sensibilidad |
| `GET /metrics/dataset` | admin | Distribución de clases, tamaños de partición y longitudes (solo agregados) |
| `GET /metrics/test` | admin | Evaluación única en `test`, o `{"estado": "pendiente"}` |
| `GET /admin/dashboard` | admin | Conteos por estado y área, tasa de corrección, últimos tickets |
| `GET/POST/PATCH/DELETE /admin/areas` | admin | Las seis áreas del modelo no se eliminan, solo se desactivan |
| `GET/POST/PATCH/DELETE /admin/empleados` | admin | Con tickets históricos se desactivan en lugar de borrarse |
| `GET /admin/tickets`, `GET/PATCH /admin/tickets/{id}` | admin | Filtros y paginación; asignar empleado, reasignar área, cambiar estado, notas |
| `GET /admin/notificaciones`, `PATCH /admin/notificaciones/{id}`, `POST /admin/notificaciones/marcar-leidas` | admin | Bandeja por área |

**Flujo de un ticket:**

1. El ticket se crea en estado `nuevo` y se clasifica.
2. Si el modelo devuelve un área activa, se guarda **una notificación para el área completa**: «Cliente: … — Mensaje: …».
3. Los saludos, los mensajes sin términos conocidos, las áreas inactivas y los casos en que el modelo no está disponible van a `revision_manual`, sin notificación de área.
4. Estados: `nuevo → asignado → en_progreso → resuelto → cerrado`. Las transiciones no válidas responden 409. Asignar un empleado de otra área responde 422.
5. Si el administrador reasigna el área y la nueva no es la predicha, el ticket queda marcado como corrección del modelo.

Autenticación: un único administrador con token HMAC de la biblioteca estándar. Es una simplificación deliberada para la simulación académica: no hay cuentas por empleado ni canales externos (correo, WhatsApp). `api/services/routing.py` deja un punto de extensión (`notify_external`) para añadirlos.

## Estructura

`api/main.py` (aplicación), `config.py`, `model.py` (carga y clasificación), `db.py` (SQLite y datos semilla), `auth.py`, `schemas.py`, `routers/` (classifier, metrics, public, admin) y `services/routing.py` (enrutamiento y estados).

## Pruebas

```bash
PYTHONPATH=../clasificador-tickets-nlp ../clasificador-tickets-nlp/.venv312/bin/python -m pytest -q
```

Cada prueba usa una base SQLite temporal. Las pruebas de métricas requieren los artefactos v3 locales del proyecto NLP. Las respuestas de métricas nunca incluyen textos de mensajes de evaluación.
