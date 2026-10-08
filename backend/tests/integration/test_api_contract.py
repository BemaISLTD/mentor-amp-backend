"""Integration tests for the versioned public API contract."""

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.db.models.audit_log import AuditLog
from app.db.models.project import Project
from app.db.models.project_member import ProjectMember
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
    AuditLog.__table__.create(engine)
    # Creating a project records its creator as owner (project-scoped authorization).
    ProjectMember.__table__.create(engine)
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
        id="test-user",
        roles=[
            SimpleNamespace(
                name="admin",
                permissions=[
                    SimpleNamespace(name="projects:read"),
                    SimpleNamespace(name="projects:write"),
                ],
            )
        ],
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_project_create_update_and_read_use_v1_prefix(client, db_session):
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

    fetched = client.get(f"/v1/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["description"] == "Updated"

    entries = db_session.query(AuditLog).order_by(AuditLog.id).all()
    assert [entry.action for entry in entries] == [
        "project.created",
        "project.updated",
    ]
    assert all(entry.actor_user_id == "test-user" for entry in entries)
    assert entries[0].after_state["description"] == "Initial"
    assert entries[1].before_state["description"] == "Initial"
    assert entries[1].after_state["description"] == "Updated"


def test_project_delete_is_not_exposed(client, db_session):
    created = client.post("/v1/projects/", json={"name": "Protected"})
    project_id = created.json()["id"]

    response = client.delete(f"/v1/projects/{project_id}")

    assert response.status_code == 405
    assert (
        db_session.query(Project).filter(Project.id == project_id).first() is not None
    )
    entries = db_session.query(AuditLog).order_by(AuditLog.id).all()
    assert [entry.action for entry in entries] == ["project.created"]


def test_project_archive_is_audited_hidden_and_read_only(client, db_session):
    archived = client.post("/v1/projects/", json={"name": "Completed Valuation"})
    active = client.post("/v1/projects/", json={"name": "Current Valuation"})
    project_id = archived.json()["id"]

    response = client.post(
        f"/v1/projects/{project_id}/archive",
        json={"reason": "Retention period started after sign-off."},
    )

    assert response.status_code == 200
    assert response.json()["archived_by"] == "test-user"
    assert response.json()["archived_at"] is not None
    assert response.json()["archive_reason"] == "Retention period started after sign-off."

    visible = client.get("/v1/projects/").json()
    assert [project["id"] for project in visible["projects"]] == [active.json()["id"]]
    retained = client.get("/v1/projects/", params={"include_archived": True}).json()
    assert {project["id"] for project in retained["projects"]} == {
        project_id,
        active.json()["id"],
    }
    assert client.get(f"/v1/projects/{project_id}").status_code == 404
    assert client.get(
        f"/v1/projects/{project_id}", params={"include_archived": True}
    ).status_code == 200
    assert client.patch(
        f"/v1/projects/{project_id}", json={"description": "Disallowed"}
    ).status_code == 409
    assert client.post(
        f"/v1/projects/{project_id}/archive", json={"reason": "Again"}
    ).status_code == 409
    assert client.post(
        f"/v1/projects/{project_id}/members",
        json={"user_id": "test-user", "role": "viewer"},
    ).status_code == 409
    assert db_session.query(Project).filter(Project.id == project_id).count() == 1

    audit = db_session.query(AuditLog).filter(AuditLog.entity_id == project_id).all()
    assert [entry.action for entry in audit] == ["project.created", "project.archived"]
    assert audit[-1].after_state["archive_reason"] == "Retention period started after sign-off."


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
