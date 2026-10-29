from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BANNERBEAR_", env_file=".env", extra="ignore")

    APP_NAME: str = "Bannerbear Engine"
    VERSION: str = "0.1.0"
    DEBUG: bool = False
    HOST: str = "127.0.0.1"
    PORT: int = 8620
    DATABASE_URL: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'bannerbear.db').as_posix()}"
    DATA_DIR: Path = BASE_DIR / "data"
    ADMIN_EMAIL: str = "admin@bannerbear.local"
    ADMIN_PASSWORD: str = Field(default="bannerbear123")
    ADMIN_NAME: str = "Bannerbear Admin"
    RENDER_CREDITS: int = 1
    DEMO_PROJECT_CREDITS: int = 100


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "renders").mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "assets").mkdir(parents=True, exist_ok=True)
