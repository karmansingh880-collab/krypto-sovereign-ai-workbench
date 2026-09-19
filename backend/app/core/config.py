from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    MONGO_URL: str = "mongodb://localhost:27017/krypto"
    CORS_ORIGINS: str = "http://localhost:3001,http://127.0.0.1:3001,http://localhost:5173"

    # Login / sessions
    SESSION_DAYS: int = 7
    COOKIE_SECURE: bool = False  # set True when served over HTTPS

    # One pre-made account so the demo can skip sign-up. Set to false to disable it.
    DEMO_ACCOUNT_ENABLED: bool = True
    DEMO_EMAIL: str = "demo@krypto.local"
    DEMO_PASSWORD: str = "demo1234"


settings = Settings()
