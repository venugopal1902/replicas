import os
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_master_key() -> str:
    """Resolve the admin key: env override, else the file written on first boot, else generate one.

    The key used to be a literal in this file, so every clone of this repo shared one known admin
    key. It is now generated per installation, stored 0600, and never printed to the console.
    """
    from_env = os.getenv("BLOTATO_MASTER_KEY", "").strip()
    if from_env:
        return from_env

    key_file = BASE_DIR / "data" / "master_api_key.txt"
    if key_file.exists():
        stored = key_file.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    key = "blt_" + secrets.token_urlsafe(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    print(f"[blotato] generated a master API key at {key_file} (the value is not printed)")
    return key

class Settings(BaseSettings):
    APP_NAME: str = "Blotato Self-Hosted Engine"
    VERSION: str = "2.0.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8400
    
    # SQLite async database
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/blotato.db"
    
    # Master Admin Key for Venu / Chief Bot
    MASTER_API_KEY: str = _resolve_master_key()
    
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
