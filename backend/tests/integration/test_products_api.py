"""Integration tests for products, mappings, and asset positions."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.audit_log import AuditLog
from app.db.models.product import AssetPosition, Product, ProductMapping
from app.db.models.project import Project
from app.main import app


@pytest.fixture()
def product_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        Project.__table__,
        Product.__table__,
        ProductMapping.__table__,
        AssetPosition.__table__,
        AuditLog.__table__,
    ):
        table.create(engine)
    session = sessionmaker(bind=engine)()
    session.add(Project(id="project-1", name="Portfolio"))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="actuary-1", roles=[SimpleNamespace(name="actuary")]
    )
    try:
        with TestClient(app) as client:
            yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def _create_product(client: TestClient) -> str:
    response = client.post(
        "/v1/products/",
        json={
            "project_id": "project-1",
            "code": "fia-001",
            "name": "Core FIA",
            "product_type": "FIA",
            "configuration": {"guarantee_period": 10},
        },
    )
    assert response.status_code == 201
    assert response.json()["code"] == "FIA-001"
    return response.json()["id"]


def test_product_crud_uniqueness_and_audit(product_client):
    client, session = product_client
    product_id = _create_product(client)

    duplicate = client.post(
        "/v1/products/",
        json={
            "project_id": "project-1",
            "code": "fia-001",
            "name": "Duplicate",
            "product_type": "FIA",
        },
    )
    assert duplicate.status_code == 409

    updated = client.patch(
        f"/v1/products/{product_id}",
        json={"name": "Updated FIA", "status": "inactive"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated FIA"
    assert updated.json()["updated_by"] == "actuary-1"

    listing = client.get("/v1/products/?project_id=project-1&status=inactive")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    actions = {
        entry.action for entry in session.query(AuditLog).filter(
            AuditLog.entity_id == product_id
        )
    }
    assert actions == {"product.created", "product.updated"}


def test_mapping_and_asset_lifecycle_soft_delete_with_product(product_client):
    client, session = product_client
    product_id = _create_product(client)

    mapping = client.post(
        f"/v1/products/{product_id}/mappings",
        json={
            "source_system": "policy-admin",
            "source_product_code": "LEGACY_FIA",
            "effective_from": "2026-01-01",
            "mapping_data": {"plan_code": "A1"},
        },
    )
    assert mapping.status_code == 201
    mapping_id = mapping.json()["id"]

    position = client.post(
        "/v1/asset-positions/",
        json={
            "product_id": product_id,
            "as_of_date": "2026-09-30",
            "asset_class": "fixed_income",
            "security_id": "BOND-1",
            "market_value": "1250000.50",
            "currency": "usd",
            "attributes": {"duration": 7.2},
        },
    )
    assert position.status_code == 201
    position_id = position.json()["id"]
    assert position.json()["currency"] == "USD"

    updated = client.patch(
        f"/v1/asset-positions/{position_id}",
        json={"market_value": "1300000.00"},
    )
    assert updated.status_code == 200
    assert float(updated.json()["market_value"]) == 1300000.0

    positions = client.get(
        "/v1/asset-positions/",
        params={"product_id": product_id, "as_of_date": "2026-09-30"},
    )
    assert positions.status_code == 200
    assert positions.json()["total"] == 1

    deleted = client.delete(f"/v1/products/{product_id}")
    assert deleted.status_code == 204
    assert client.get(f"/v1/products/{product_id}").status_code == 404
    assert client.get(f"/v1/asset-positions/{position_id}").status_code == 404

    assert session.query(ProductMapping).filter(
        ProductMapping.id == mapping_id,
        ProductMapping.deleted_at.is_not(None),
    ).count() == 1
    assert session.query(AssetPosition).filter(
        AssetPosition.id == position_id,
        AssetPosition.deleted_at.is_not(None),
    ).count() == 1


def test_mapping_validates_effective_date_range(product_client):
    client, _ = product_client
    product_id = _create_product(client)

    response = client.post(
        f"/v1/products/{product_id}/mappings",
        json={
            "source_system": "legacy",
            "source_product_code": "P1",
            "effective_from": "2026-06-01",
            "effective_to": "2026-01-01",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
