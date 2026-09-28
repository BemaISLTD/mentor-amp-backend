"""Integration tests for the versioned public API contract."""

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.db.models.project import Project
from app.api.dependencies import get_current_user
from app.main import app


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="test-user", roles=[SimpleNamespace(name="admin")]
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_projects_crud_uses_v1_prefix(client):
    created = client.post(
        "/v1/projects/",
        json={"name": "Valuation", "description": "Initial"},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    updated = client.patch(
        f"/v1/projects/{project_id}",
        json={"description": "Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Valuation"
    assert updated.json()["description"] == "Updated"

    deleted = client.delete(f"/v1/projects/{project_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""

    missing = client.get(f"/v1/projects/{project_id}")
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": f"Project with id '{project_id}' not found.",
        }
    }


def test_validation_errors_use_standard_envelope(client):
    response = client.post("/v1/projects/", json={"name": ""})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "Request validation failed."
    assert body["error"]["details"]


def test_unversioned_project_route_is_not_exposed(client):
    response = client.get("/projects/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
