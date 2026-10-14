"""Work Package 1 — demo authentication can only run in local/test; CORS is never wide open."""

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_CORS_ORIGINS, Settings

PRODUCTION_SECRET = "a-real-production-secret-of-at-least-32-bytes!"


def make(**overrides) -> Settings:
    values = {"database_url": "sqlite://", "debug": True, "_env_file": None}
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("environment", ["production", "staging", "development"])
def test_demo_auth_is_refused_outside_local_and_test_even_with_debug(environment):
    with pytest.raises(ValidationError, match="AUTH_MODE=disabled"):
        make(app_env=environment, auth_mode="disabled", debug=True,
             cors_origins="https://app.example.com")


@pytest.mark.parametrize("environment", ["local", "test"])
def test_demo_auth_is_allowed_only_in_local_and_test_with_debug(environment):
    settings = make(app_env=environment, auth_mode="disabled", debug=True)
    assert settings.demo_auth_permitted is True
    with pytest.raises(ValidationError, match="DEBUG"):
        make(app_env=environment, auth_mode="disabled", debug=False, jwt_secret_key=PRODUCTION_SECRET)


BUILD = {"source_commit": "a" * 40, "build_fingerprint": "b" * 64}


def test_jwt_mode_production_needs_explicit_cors_and_a_real_secret():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        make(app_env="production", debug=False, jwt_secret_key=PRODUCTION_SECRET, **BUILD)
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        make(app_env="production", debug=False, cors_origins="https://app.example.com", **BUILD)
    settings = make(app_env="production", debug=False, jwt_secret_key=PRODUCTION_SECRET,
                    cors_origins="https://app.example.com", **BUILD)
    assert settings.demo_auth_permitted is False
    assert make(app_env="local").cors_origins == DEFAULT_CORS_ORIGINS


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize("missing", ["source_commit", "build_fingerprint"])
def test_protected_environments_require_an_injected_build_identity(environment, missing):
    build = {key: value for key, value in BUILD.items() if key != missing}
    with pytest.raises(ValidationError, match="BUILD_FINGERPRINT"):
        make(app_env=environment, debug=False, jwt_secret_key=PRODUCTION_SECRET,
             cors_origins="https://app.example.com", **build)


def test_wildcard_cors_is_always_refused():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        make(app_env="local", cors_origins="*")
