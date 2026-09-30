from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_JWT_SECRET = "development-only-change-me-at-least-32-bytes"
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://localhost:8001"
)
AppEnvironment = Literal["local", "test", "development", "staging", "production"]
# The only environments where AUTH_MODE=disabled (demo mode, no login) may ever run.
DEMO_AUTH_ENVIRONMENTS = frozenset({"local", "test"})
PROTECTED_ENVIRONMENTS = frozenset({"staging", "production"})


class Settings(BaseSettings):
    """Application configuration loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    database_url: str
    app_name: str = "MentorAmp"
    # Where this process runs. Demo mode is refused outside local/test whatever DEBUG says.
    app_env: AppEnvironment = "development"
    debug: bool = False
    # Log every SQL statement (very noisy; for debugging database issues only).
    sql_echo: bool = False
    jwt_secret_key: str = DEVELOPMENT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # "disabled" runs every request as the demo user (no login): APP_ENV local/test + DEBUG only.
    auth_mode: Literal["jwt", "disabled"] = "jwt"
    demo_user_email: str = "demo-admin@mentoramp.local"
    demo_user_name: str = "Demo Admin"

    # Comma-separated list of browser origins allowed to call the API.
    cors_origins: str = DEFAULT_CORS_ORIGINS

    # Build identity recorded in every run package. CODE_VERSION is a release/build label;
    # SOURCE_COMMIT is the git commit SHA the process was built from (set it in CI/deployments).
    code_version: str = "development"
    source_commit: str | None = None

    # Upper bound on policies whose calculation trace is stored for one run (frozen per package).
    max_traced_policies: int = 25

    @model_validator(mode="after")
    def reject_unsafe_configuration(self):
        if not self.debug and self.jwt_secret_key == DEVELOPMENT_JWT_SECRET:
            raise ValueError("JWT_SECRET_KEY must be configured when DEBUG is false.")
        if self.auth_mode == "disabled":
            if self.app_env not in DEMO_AUTH_ENVIRONMENTS:
                raise ValueError(
                    f"AUTH_MODE=disabled is only allowed when APP_ENV is local or test "
                    f"(APP_ENV={self.app_env})."
                )
            if not self.debug:
                raise ValueError("AUTH_MODE=disabled is only allowed when DEBUG is true.")
        origins = self.cors_origin_list
        if "*" in origins:
            raise ValueError("CORS_ORIGINS may not contain '*' (credentials are allowed).")
        if self.app_env in PROTECTED_ENVIRONMENTS and self.cors_origins == DEFAULT_CORS_ORIGINS:
            raise ValueError(
                f"CORS_ORIGINS must be set explicitly when APP_ENV={self.app_env} "
                "(the default only lists localhost development servers)."
            )
        return self

    @property
    def demo_auth_permitted(self) -> bool:
        """Checked again on every request, not only at startup."""
        return (
            self.auth_mode == "disabled"
            and self.app_env in DEMO_AUTH_ENVIRONMENTS
            and self.debug
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
