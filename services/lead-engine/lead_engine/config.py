import os
from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    APP_NAME: str = "Lead Engine"
    VERSION: str = "0.1.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8500

    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/lead_engine.db"
    DATA_DIR: Path = BASE_DIR / "data"
    STATIC_DIR: Path = BASE_DIR / "static"

    # Seeded admin account
    ADMIN_EMAIL: str = "admin@leadengine.local"
    ADMIN_PASSWORD: str = "leadengine123"
    ADMIN_NAME: str = "Workspace Owner"

    # Data providers (optional; the engine works without them)
    HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "")
    APOLLO_API_KEY: str = os.getenv("APOLLO_API_KEY", "")
    PDL_API_KEY: str = os.getenv("PDL_API_KEY", "")

    # Verification behaviour
    SMTP_PROBE: bool = os.getenv("SMTP_PROBE", "false").lower() == "true"
    SMTP_TIMEOUT: float = 6.0
    SMTP_FROM: str = os.getenv("SMTP_FROM", "verify@leadengine.local")

    # Campaign sending: simulate by default so nothing is ever emailed without consent
    CAMPAIGN_SEND_MODE: str = os.getenv("CAMPAIGN_SEND_MODE", "simulate")  # simulate|smtp
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASS: str = os.getenv("SMTP_PASS", "")

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
