from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_JWT_SECRET = "development-only-change-me-at-least-32-bytes"
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://localhost:8001"
)


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
    # Log every SQL statement (very noisy; for debugging database issues only).
    sql_echo: bool = False
    jwt_secret_key: str = DEVELOPMENT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # "disabled" runs every request as the demo user (no login). Allowed only with DEBUG=true.
    auth_mode: Literal["jwt", "disabled"] = "jwt"
    demo_user_email: str = "demo-admin@mentoramp.local"
    demo_user_name: str = "Demo Admin"

    # Comma-separated list of browser origins allowed to call the API.
    cors_origins: str = DEFAULT_CORS_ORIGINS

    # Recorded in run manifests (set to the git commit in CI or deployments).
    code_version: str = "development"

    # Upper bound on policies whose calculation trace is stored for one run.
    max_traced_policies: int = 25

    @model_validator(mode="after")
    def reject_unsafe_configuration(self):
        if not self.debug and self.jwt_secret_key == DEVELOPMENT_JWT_SECRET:
            raise ValueError("JWT_SECRET_KEY must be configured when DEBUG is false.")
        if self.auth_mode == "disabled" and not self.debug:
            raise ValueError("AUTH_MODE=disabled is only allowed when DEBUG is true.")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
