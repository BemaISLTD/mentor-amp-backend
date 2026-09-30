"""Integration tests for project dashboard aggregations."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.inforce import InforceFile
from app.db.models.product import AssetPosition, Product
from app.db.models.project import Project
from app.db.models.run import Run
from app.main import app


@pytest.fixture()
def dashboard_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        Project.__table__,
        Product.__table__,
        AssetPosition.__table__,
        Run.__table__,
        InforceFile.__table__,
    ):
        table.create(engine)
    session = sessionmaker(bind=engine)()
    project = Project(id="project-1", name="Portfolio")
    fia = Product(
        id="product-fia",
        project_id=project.id,
        code="FIA",
        name="Fixed Indexed Annuity",
        product_type="FIA",
        status="active",
    )
    rila = Product(
        id="product-rila",
        project_id=project.id,
        code="RILA",
        name="Registered Index Linked Annuity",
        product_type="RILA",
        status="draft",
    )
    session.add_all([project, fia, rila])
    session.flush()
    session.add_all(
        [
            AssetPosition(
                project_id=project.id,
                product_id=fia.id,
                as_of_date=date(2026, 8, 31),
                asset_class="fixed_income",
                market_value=Decimal("900"),
                currency="USD",
            ),
            AssetPosition(
                project_id=project.id,
                product_id=fia.id,
                as_of_date=date(2026, 9, 30),
                asset_class="fixed_income",
                market_value=Decimal("1000"),
                currency="USD",
            ),
            AssetPosition(
                project_id=project.id,
                product_id=rila.id,
                as_of_date=date(2026, 9, 30),
                asset_class="equity",
                market_value=Decimal("500"),
                currency="USD",
            ),
            Run(project_id=project.id, status="success"),
            Run(project_id=project.id, status="failed"),
            InforceFile(
                project_id=project.id,
                filename="policies.csv",
                file_type="csv",
                row_count=250,
            ),
        ]
    )
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="actuary-1",
        roles=[
            SimpleNamespace(
                name="actuary",
                permissions=[SimpleNamespace(name="projects:read")],
            )
        ],
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_dashboard_uses_latest_asset_snapshot(dashboard_client):
    response = dashboard_client.get(
        "/v1/dashboard/stats", params={"project_id": "project-1"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["product_count"] == 2
    assert body["active_product_count"] == 1
    assert body["asset_as_of_date"] == "2026-09-30"
    assert float(body["total_market_value"]) == 1500.0
    assert body["run_count"] == 2
    assert body["inforce_file_count"] == 1
    assert body["policy_record_count"] == 250
    assert [item["code"] for item in body["product_mix"]] == ["FIA", "RILA"]


def test_dashboard_supports_historical_asset_date(dashboard_client):
    response = dashboard_client.get(
        "/v1/dashboard/stats",
        params={"project_id": "project-1", "as_of_date": "2026-08-31"},
    )

    assert response.status_code == 200
    assert float(response.json()["total_market_value"]) == 900.0


def test_dashboard_rejects_unknown_project(dashboard_client):
    response = dashboard_client.get(
        "/v1/dashboard/stats", params={"project_id": "missing"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
