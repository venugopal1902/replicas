from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZIGPOLL_", env_file=".env", extra="ignore")

    APP_NAME: str = "Zigpoll Engine"
    VERSION: str = "0.1.0"
    HOST: str = "127.0.0.1"
    PORT: int = 8630
    DEBUG: bool = False
    DATABASE_URL: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'zigpoll.db').as_posix()}"
    DATA_DIR: Path = BASE_DIR / "data"
    ADMIN_EMAIL: str = "admin@zigpoll.local"
    ADMIN_PASSWORD: str = Field(default="", repr=False)
    ADMIN_NAME: str = "Zigpoll Admin"
    SESSION_HOURS: int = 12


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
