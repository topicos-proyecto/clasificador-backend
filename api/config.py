"""Configuración por variables de entorno.

- MODEL_DIR: directorio del modelo dentro de clasificador-tickets-nlp (o ruta absoluta).
  Por defecto `artifacts/comparison_customers_v3`, la comparación de tres modelos. Para volver al
  modelo anterior: `MODEL_DIR=artifacts/comparison_customers`.
- DB_PATH: base SQLite de tickets (por defecto `data/tickets.db` en este repositorio).
- ADMIN_PASSWORD: contraseña del administrador. Sin ella el inicio de sesión responde 503.
- SECRET_KEY: clave para firmar los tokens. Sin ella se genera una al iniciar (los tokens
  dejan de valer al reiniciar la API).
- UMBRAL_REVISION: opcional (0-1). Si se define, los tickets con confianza menor pasan a revisión
  manual. Es provisional: las probabilidades no están calibradas.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from src.models.train import ROOT

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _resolve(value: str, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def _threshold() -> float | None:
    raw = os.environ.get('UMBRAL_REVISION', '').strip()
    if not raw:
        return None
    value = float(raw)
    if not 0 < value < 1:
        raise ValueError('UMBRAL_REVISION debe estar entre 0 y 1.')
    return value


@dataclass
class Settings:
    model_dir: Path = field(default_factory=lambda: _resolve(
        os.environ.get('MODEL_DIR', 'artifacts/comparison_customers_v3'), ROOT))
    db_path: Path = field(default_factory=lambda: _resolve(os.environ.get('DB_PATH', 'data/tickets.db'), BACKEND_ROOT))
    admin_password: str | None = field(default_factory=lambda: os.environ.get('ADMIN_PASSWORD') or None)
    secret_key: str = field(default_factory=lambda: os.environ.get('SECRET_KEY') or secrets.token_hex(32))
    review_threshold: float | None = field(default_factory=_threshold)
    token_ttl_seconds: int = 8 * 3600
    rate_limit_per_minute: int = 10
    human_manifest: Path = ROOT / 'data/raw/custom/human_evaluation_clients_manifest.json'

    @property
    def model_path(self) -> Path:
        return self.model_dir / 'pipeline.joblib'

    @property
    def report_path(self) -> Path:
        return self.model_dir / 'report.json'

    @property
    def human_path(self) -> Path:
        return self.model_dir / 'human_clients_report.json'

    @property
    def dataset_path(self) -> Path:
        return self.model_dir / 'dataset_stats.json'

    @property
    def test_path(self) -> Path:
        return self.model_dir / 'test_report.json'


settings = Settings()
