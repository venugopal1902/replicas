import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    APP_NAME: str = "Blotato Self-Hosted Engine"
    VERSION: str = "2.0.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8400
    
    # SQLite async database
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/blotato.db"
    
    # Master Admin Key for Venu / Chief Bot
    MASTER_API_KEY: str = os.getenv("BLOTATO_MASTER_KEY", "blt_master_venu_secret_2026")
    
    # Storage dir for uploaded media
    MEDIA_DIR: Path = BASE_DIR / "data" / "media"
    
    # Social Platform default credentials (can also be loaded dynamically per account)
    META_ACCESS_TOKEN: str = os.getenv("META_ACCESS_TOKEN", "")
    TWITTER_BEARER_TOKEN: str = os.getenv("TWITTER_BEARER_TOKEN", "")
    LINKEDIN_ACCESS_TOKEN: str = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    COMPOSIO_API_KEY: str = os.getenv("COMPOSIO_API_KEY", "")
    
    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()
settings.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
