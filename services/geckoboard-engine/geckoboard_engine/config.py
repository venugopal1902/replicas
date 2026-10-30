from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "Geckoboard Engine"
    version: str = "0.1.0"
    host: str = os.environ.get("GECKOBOARD_HOST", "127.0.0.1")
    port: int = int(os.environ.get("GECKOBOARD_PORT", "8670"))
    db_path: Path = Path(os.environ.get("GECKOBOARD_DB_PATH", str(BASE_DIR / "data" / "geckoboard.db")))
    admin_email: str = os.environ.get("GECKOBOARD_ADMIN_EMAIL", "admin@geckoboard.local")
    admin_name: str = os.environ.get("GECKOBOARD_ADMIN_NAME", "Geckoboard Admin")
    admin_password: str = os.environ.get("GECKOBOARD_ADMIN_PASSWORD", "")
    dataset_api_key: str = os.environ.get("GECKOBOARD_DATASET_API_KEY", "")
    cookie_secure: bool = _bool_env("GECKOBOARD_COOKIE_SECURE", False)
    session_hours: int = int(os.environ.get("GECKOBOARD_SESSION_HOURS", "12"))
    seed_demo: bool = _bool_env("GECKOBOARD_SEED_DEMO", True)


settings = Settings()
