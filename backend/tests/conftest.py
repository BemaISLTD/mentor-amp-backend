"""Shared pytest configuration."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _jwt_auth_mode_by_default(monkeypatch):
    """Run tests with real authentication unless a test opts into demo mode.

    A developer's .env may set AUTH_MODE=disabled for local demos; tests must not depend on it.
    """
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    # Tests are the "test" environment, where demo mode is permitted when a test opts in.
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "debug", True)
    yield
