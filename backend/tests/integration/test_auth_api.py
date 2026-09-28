"""Integration tests for authentication and role authorization."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import get_db
from app.db.models.audit_log import AuditLog
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
    session = sessionmaker(bind=engine)()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
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
