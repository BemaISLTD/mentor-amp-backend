"""Work Package 1 — request authentication never provisions users; demo mode is re-checked per request."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.config import settings
from app.db.database import Base, get_db
from app.db.models.user import Role, User
from app.main import app
from app.services.bootstrap import ensure_demo_user


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.clear()
    engine.dispose()


def test_demo_mode_does_not_create_the_demo_user_during_a_request(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "disabled")
    with TestClient(app) as client:
        response = client.get("/v1/auth/me")
    assert response.status_code == 503
    assert "seed_demo" in response.json()["error"]["message"]
    with session_factory() as db:
        assert db.query(User).count() == 0 and db.query(Role).count() == 0


def test_demo_user_is_provisioned_by_explicit_setup_and_is_idempotent(session_factory, monkeypatch):
    with session_factory() as db:
        first = ensure_demo_user(db).id
    with session_factory() as db:
        assert ensure_demo_user(db).id == first
        assert db.query(User).count() == 1
    monkeypatch.setattr(settings, "auth_mode", "disabled")
    with TestClient(app) as client:
        me = client.get("/v1/auth/me")
    assert me.status_code == 200 and me.json()["id"] == first


def test_demo_mode_is_refused_per_request_outside_local_and_test(session_factory, monkeypatch):
    with session_factory() as db:
        ensure_demo_user(db)
    monkeypatch.setattr(settings, "auth_mode", "disabled")
    monkeypatch.setattr(settings, "app_env", "production")  # e.g. a mis-deployed process
    with TestClient(app) as client:
        response = client.get("/v1/auth/me")
    assert response.status_code == 503
    assert "not permitted" in response.json()["error"]["message"]
