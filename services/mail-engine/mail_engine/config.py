import os
from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    APP_NAME: str = "Mail Engine"
    VERSION: str = "0.1.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8600

    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/mail_engine.db"
    DATA_DIR: Path = BASE_DIR / "data"
    STATIC_DIR: Path = BASE_DIR / "static"

    ADMIN_EMAIL: str = "admin@mailengine.local"
    ADMIN_PASSWORD: str = "mailengine123"
    ADMIN_NAME: str = "Workspace Owner"

    # Sending mode: "simulate" never touches the network. "smtp" requires SMTP_* values.
    SEND_MODE: str = os.getenv("MAIL_SEND_MODE", "simulate")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASS: str = os.getenv("SMTP_PASS", "")

    # Public base URL used for tracking pixel / click / unsubscribe links.
    PUBLIC_BASE_URL: str = os.getenv("MAIL_PUBLIC_BASE_URL", "http://localhost:8600")
    UNSUBSCRIBE_SECRET: str = os.getenv("MAIL_UNSUB_SECRET", "dev-unsubscribe-secret")

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
