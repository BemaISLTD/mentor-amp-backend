"""Integration tests for authentication and role authorization."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.db.models.audit_log import AuditLog
from app.db.models.project import Project
from app.db.models.user import Permission, Role, User, role_permissions, user_roles
from app.main import app


@pytest.fixture()
def auth_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (User.__table__, Role.__table__, Permission.__table__):
        table.create(engine)
    user_roles.create(engine)
    role_permissions.create(engine)
    AuditLog.__table__.create(engine)
    Project.__table__.create(engine)
    session = sessionmaker(bind=engine)()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            client.test_session = session
            yield client
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def _bootstrap_and_login(client: TestClient) -> str:
    created = client.post(
        "/v1/auth/bootstrap",
        json={
            "email": "admin@example.com",
            "full_name": "Initial Admin",
            "password": "correct-horse-battery-staple",
        },
    )
    assert created.status_code == 201
    assert created.json()["roles"] == ["admin"]

    token = client.post(
        "/v1/auth/token",
        data={
            "username": "admin@example.com",
            "password": "correct-horse-battery-staple",
        },
    )
    assert token.status_code == 200
    return token.json()["access_token"]


def test_bootstrap_login_and_me(auth_client):
    token = _bootstrap_and_login(auth_client)

    response = auth_client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"

    second_bootstrap = auth_client.post(
        "/v1/auth/bootstrap",
        json={
            "email": "other@example.com",
            "full_name": "Other Admin",
            "password": "another-secure-password",
        },
    )
    assert second_bootstrap.status_code == 409
    assert second_bootstrap.json()["error"]["code"] == "CONFLICT"


def test_protected_routes_require_a_token(auth_client):
    response = auth_client.get("/v1/projects/")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_admin_can_create_and_list_actuary(auth_client):
    token = _bootstrap_and_login(auth_client)
    headers = {"Authorization": f"Bearer {token}"}

    created = auth_client.post(
        "/v1/users/",
        headers=headers,
        json={
            "email": "actuary@example.com",
            "full_name": "Test Actuary",
            "password": "actuary-secure-password",
            "roles": ["actuary"],
        },
    )
    assert created.status_code == 201
    assert created.json()["roles"] == ["actuary"]

    users = auth_client.get("/v1/users/", headers=headers)
    assert users.status_code == 200
    assert {user["email"] for user in users.json()} == {
        "admin@example.com",
        "actuary@example.com",
    }

    audit_response = auth_client.get("/v1/audit-logs/", headers=headers)
    assert audit_response.status_code == 200
    audit_body = audit_response.json()
    assert audit_body["total"] == 2
    assert {entry["action"] for entry in audit_body["entries"]} == {"user.created"}
    assert {entry["context"]["source"] for entry in audit_body["entries"]} == {
        "bootstrap",
        "admin",
    }


def test_bootstrap_creates_the_complete_role_permission_matrix(auth_client):
    _bootstrap_and_login(auth_client)
    session = auth_client.test_session
    roles = {
        role.name: {permission.name for permission in role.permissions}
        for role in session.query(Role).all()
    }

    assert roles == {
        "admin": {
            "projects:read", "projects:write", "registries:read", "registries:write",
            "imports:read", "imports:write", "runs:read", "runs:execute",
        },
        "actuary": {
            "projects:read", "registries:read", "registries:write", "imports:read",
            "imports:write", "runs:read", "runs:execute",
        },
        "model_developer": {
            "projects:read", "registries:read", "registries:write", "imports:read",
            "imports:write", "runs:read", "runs:execute",
        },
        "reviewer": {"projects:read", "registries:read", "imports:read", "runs:read"},
        "read_only": {"projects:read", "registries:read", "imports:read", "runs:read"},
    }


@pytest.mark.parametrize("role", ["model_developer", "reviewer", "read_only"])
def test_non_admin_roles_can_read_but_cannot_manage_projects(auth_client, role):
    admin_token = _bootstrap_and_login(auth_client)
    created = auth_client.post(
        "/v1/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": f"{role}@example.com",
            "full_name": role.replace("_", " ").title(),
            "password": "role-specific-password",
            "roles": [role],
        },
    )
    assert created.status_code == 201
    token = auth_client.post(
        "/v1/auth/token",
        data={"username": f"{role}@example.com", "password": "role-specific-password"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert auth_client.get("/v1/projects/", headers=headers).status_code == 200
    assert auth_client.post(
        "/v1/projects/", headers=headers, json={"name": "Disallowed"}
    ).status_code == 403


def test_actuary_cannot_access_admin_user_list(auth_client):
    admin_token = _bootstrap_and_login(auth_client)
    created = auth_client.post(
        "/v1/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "actuary@example.com",
            "full_name": "Test Actuary",
            "password": "actuary-secure-password",
            "roles": ["actuary"],
        },
    )
    assert created.status_code == 201

    token = auth_client.post(
        "/v1/auth/token",
        data={
            "username": "actuary@example.com",
            "password": "actuary-secure-password",
        },
    ).json()["access_token"]
    response = auth_client.get(
        "/v1/users/", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    audit_response = auth_client.get(
        "/v1/audit-logs/", headers={"Authorization": f"Bearer {token}"}
    )
    assert audit_response.status_code == 403


def test_actuary_can_read_projects_but_cannot_mutate_them(auth_client):
    admin_token = _bootstrap_and_login(auth_client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    created = auth_client.post(
        "/v1/projects/",
        headers=admin_headers,
        json={"name": "Governed Portfolio"},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    user = auth_client.post(
        "/v1/users/",
        headers=admin_headers,
        json={
            "email": "actuary@example.com",
            "full_name": "Test Actuary",
            "password": "actuary-secure-password",
            "roles": ["actuary"],
        },
    )
    assert user.status_code == 201
    token = auth_client.post(
        "/v1/auth/token",
        data={
            "username": "actuary@example.com",
            "password": "actuary-secure-password",
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert auth_client.get("/v1/projects/", headers=headers).status_code == 200
    assert auth_client.get(
        f"/v1/projects/{project_id}", headers=headers
    ).status_code == 200
    assert auth_client.post(
        "/v1/projects/", headers=headers, json={"name": "Disallowed"}
    ).status_code == 403
    assert auth_client.patch(
        f"/v1/projects/{project_id}",
        headers=headers,
        json={"description": "Disallowed"},
    ).status_code == 403
    assert auth_client.delete(
        f"/v1/projects/{project_id}", headers=headers
    ).status_code == 405


def test_invalid_bootstrap_email_returns_validation_envelope(auth_client):
    response = auth_client.post(
        "/v1/auth/bootstrap",
        json={
            "email": "not-an-email",
            "full_name": "Initial Admin",
            "password": "correct-horse-battery-staple",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_user_email_returns_validation_envelope(auth_client):
    token = _bootstrap_and_login(auth_client)
    response = auth_client.post(
        "/v1/users/",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "not-an-email",
            "full_name": "Invalid User",
            "password": "correct-horse-battery-staple",
            "roles": ["actuary"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_empty_roles_returns_validation_envelope(auth_client):
    token = _bootstrap_and_login(auth_client)
    response = auth_client.post(
        "/v1/users/",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "user@example.com",
            "full_name": "Invalid User",
            "password": "correct-horse-battery-staple",
            "roles": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
