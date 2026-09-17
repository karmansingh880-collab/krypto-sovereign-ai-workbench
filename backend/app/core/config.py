from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MONGO_URL: str = "mongodb://localhost:27017/krypto"


settings = Settings()
