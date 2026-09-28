from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_JWT_SECRET = "development-only-change-me-at-least-32-bytes"


class Settings(BaseSettings):
    """Application configuration loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    database_url: str
    app_name: str = "MentorAmp"
    debug: bool = False
    jwt_secret_key: str = DEVELOPMENT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    @model_validator(mode="after")
    def reject_development_secret_in_production(self):
        if not self.debug and self.jwt_secret_key == DEVELOPMENT_JWT_SECRET:
            raise ValueError("JWT_SECRET_KEY must be configured when DEBUG is false.")
        return self


settings = Settings()
