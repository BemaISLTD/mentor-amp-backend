"""Unit and integration tests for Assumption & Factor Table REST APIs."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.project import Project
from app.main import app


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add(Project(id="project-123", name="Test Project"))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="actuary-1",
        roles=[
            SimpleNamespace(
                name="admin",
                permissions=[
                    SimpleNamespace(name="registries:read"),
                    SimpleNamespace(name="registries:write"),
                    SimpleNamespace(name="projects:read"),
                    SimpleNamespace(name="projects:write"),
                ],
            )
        ],
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_list_factor_tables(client):
    response = client.get("/v1/assumptions/tables")
    assert response.status_code == 200
    data = response.json()
    assert "tables" in data
    table_ids = [t["table_id"] for t in data["tables"]]
    assert "synthetic-mortality-v1" in table_ids
    assert "synthetic-scale-g2-v1" in table_ids
    assert "standard-surrender-tiers-v1" in table_ids


def test_list_project_factor_tables(client):
    response = client.get("/v1/projects/project-123/assumptions/tables")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == "project-123"
    assert data["scope"] == "project"
    assert len(data["global_reference_tables"]) >= 4


def test_get_table_detail(client):
    response = client.get("/v1/assumptions/tables/synthetic-mortality-v1")
    assert response.status_code == 200
    data = response.json()
    assert data["table_id"] == "synthetic-mortality-v1"
    assert data["status"] == "approved"
    assert data["is_synthetic"] is True
    assert len(data["dimensions"]) == 2
    assert "sample_preview" in data
    assert len(data["sample_preview"]) > 0


def test_get_nonexistent_table_returns_404(client):
    response = client.get("/v1/assumptions/tables/nonexistent-table")
    assert response.status_code == 404


def test_lookup_table_value_scalar_success(client):
    payload = {
        "coordinates": {"attained_age": 42, "sex": "F"}
    }
    response = client.post("/v1/assumptions/tables/synthetic-mortality-v1/lookup", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["table_id"] == "synthetic-mortality-v1"
    assert pytest.approx(data["value"], abs=1e-6) == 0.00109
    assert data["fingerprint_verified"] is True
    assert data["is_synthetic"] is True


def test_lookup_table_value_out_of_bounds_fail_closed(client):
    payload = {
        "coordinates": {"attained_age": 99, "sex": "F"}
    }
    response = client.post("/v1/assumptions/tables/synthetic-mortality-v1/lookup", json=payload)
    assert response.status_code == 400
    data = response.json()
    assert "out of bounds" in data["error"]["message"].lower()


def test_list_mortality_tables(client):
    response = client.get("/v1/assumptions/mortality/tables")
    assert response.status_code == 200
    data = response.json()
    assert "mortality_tables" in data
    table_ids = [t["table_id"] for t in data["mortality_tables"]]
    assert "synthetic-mortality-v1" in table_ids


def test_project_mortality_scale_g2(client):
    payload = {
        "table_id": "synthetic-mortality-v1",
        "improvement_table_id": "synthetic-scale-g2-v1",
        "attained_age": 40,
        "sex": "M",
        "base_year": 2026,
        "projection_year": 2028,
        "mode": "FIXED_ATTAINED_AGE",
    }
    response = client.post("/v1/assumptions/mortality/project", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["method"] == "SCALE_G2"
    assert data["base_qx"] == pytest.approx(0.0012)
    expected = 0.0012 * (1.0 - 0.012) ** 2
    assert data["projected_qx"] == pytest.approx(expected, abs=1e-8)
    assert data["is_synthetic"] is True


def test_project_mortality_mp_matrix(client):
    payload = {
        "table_id": "synthetic-mortality-v1",
        "improvement_table_id": "synthetic-mp-matrix-v1",
        "attained_age": 40,
        "sex": "F",
        "base_year": 2026,
        "projection_year": 2028,
        "mode": "FIXED_ATTAINED_AGE",
    }
    response = client.post("/v1/assumptions/mortality/project", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["method"] == "MP_MATRIX"
    assert data["base_qx"] == pytest.approx(0.0009)
    assert data["projected_qx"] < data["base_qx"]


def test_monthly_decrement_constant_force(client):
    payload = {
        "annual_qx": 0.012,
        "method": "constant_force",
    }
    response = client.post("/v1/assumptions/mortality/monthly-decrement", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["method"] == "constant_force"
    expected = 1.0 - (1.0 - 0.012) ** (1.0 / 12.0)
    assert data["monthly_decrement"] == pytest.approx(expected, abs=1e-7)


def test_monthly_decrement_udd(client):
    payload = {
        "annual_qx": 0.012,
        "method": "udd",
        "month_index": 6,
    }
    response = client.post("/v1/assumptions/mortality/monthly-decrement", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["method"] == "udd"
    expected = (0.012 / 12.0) / (1.0 - (5.0 / 12.0) * 0.012)
    assert data["monthly_decrement"] == pytest.approx(expected, abs=1e-7)


def test_list_yield_curves(client):
    response = client.get("/v1/assumptions/curves")
    assert response.status_code == 200
    data = response.json()
    assert "curves" in data
    assert len(data["curves"]) >= 1
    assert "illustrative" in data["curves"][0]["curve_id"] and "treasury" in data["curves"][0]["curve_id"]
    assert data["curves"][0]["is_synthetic"] is True


def test_get_curve_detail(client):
    response = client.get("/v1/assumptions/curves/illustrative-us-treasury-spot")
    assert response.status_code == 200
    data = response.json()
    assert "illustrative" in data["curve_id"] and "treasury" in data["curve_id"]
    assert data["curve_type"] == "SPOT_ZERO"
    assert data["is_synthetic"] is True
    assert len(data["points"]) == 11


def test_curve_discount_factor(client):
    payload = {"tenor_years": 5.0}
    response = client.post(
        "/v1/assumptions/curves/illustrative-us-treasury-spot/discount-factor",
        json=payload,
    )
    assert response.status_code == 200
    data = response.json()
    assert "illustrative" in data["curve_id"] and "treasury" in data["curve_id"]
    assert data["tenor_years"] == 5.0
    assert data["interpolated_rate"] == pytest.approx(0.0390)
    expected_df = (1.0 + 0.0390) ** (-5.0)
    assert data["discount_factor"] == pytest.approx(expected_df, abs=1e-6)


def test_baseline_lapse_duration(client):
    payload = {"duration": 5}
    response = client.post("/v1/assumptions/behavior/baseline-lapse", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["duration"] == 5
    assert data["baseline_lapse_rate"] == pytest.approx(0.06)
    assert data["is_synthetic"] is True


def test_dynamic_lapse_evaluate(client):
    payload = {
        "base_lapse": 0.05,
        "market_rate": 0.06,
        "contract_rate": 0.04,
        "buffer": 0.01,
        "threshold": 0.02,
        "alpha": 2.0,
        "beta": 1.0,
        "max_lapse_rate": 0.50,
    }
    response = client.post("/v1/assumptions/behavior/evaluate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["base_lapse"] == 0.05
    assert data["spread"] == pytest.approx(0.01)
    assert data["dynamic_multiplier"] == pytest.approx(2.0)
    assert data["effective_lapse_rate"] == pytest.approx(0.10)
    assert data["is_synthetic"] is True
