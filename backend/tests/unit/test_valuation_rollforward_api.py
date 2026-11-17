"""Unit and integration tests for valuation-detail, rollforward, and governance endpoints under /v1."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.database import get_db
from app.db.models.governance import DerivedDataset, Report, RollforwardTemplate
from app.db.models.project import Project
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.valuation_detail import ValuationRunDetail
from app.main import app


@pytest.fixture()
def client_with_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        Project.__table__,
        Run.__table__,
        ValuationRunDetail.__table__,
        RollforwardSummary.__table__,
        RollforwardTemplate.__table__,
        Report.__table__,
        DerivedDataset.__table__,
    ):
        table.create(engine)

    session = sessionmaker(bind=engine)()

    # Seed baseline project
    project = Project(id="proj-100", name="Valuation Portfolio")
    session.add(project)

    # Seed valuation run with ValuationRunDetail
    now = datetime.now(timezone.utc)
    run1 = Run(
        id="run-val-1",
        project_id="proj-100",
        name="M5 Valuation Run",
        status="success",
        methodology="M5_STATUTORY_VALUATION",
        basis="STATUTORY",
        created_at=now,
    )
    session.add(run1)

    val_detail = ValuationRunDetail(
        run_id="run-val-1",
        valuation_date=date(2026, 1, 1),
        discount_curve_fingerprint="abc123def456",
        configuration={"horizon_months": 72, "reserve_basis": "CRVM"},
        summary_results={"reserve_at_valuation": 1250000.0, "net_surplus": 50000.0},
        created_at=now,
    )
    session.add(val_detail)

    # Seed rollforward summaries with unbroken continuity
    rf1 = RollforwardSummary(
        id="rf-1",
        run_id="run-val-1",
        project_id="proj-100",
        cohort_id="COHORT-2026-A",
        methodology="M5_STATUTORY_VALUATION",
        basis="STATUTORY",
        currency="USD",
        component_name="Beginning Balance",
        component_order=1,
        is_movement=False,
        opening_balance=1000000.0,
        movement_amount=0.0,
        closing_balance=1000000.0,
        is_continuity_verified=True,
        continuity_difference=0.0,
        trace_identifier="trace-001",
        created_at=now,
    )
    rf2 = RollforwardSummary(
        id="rf-2",
        run_id="run-val-1",
        project_id="proj-100",
        cohort_id="COHORT-2026-A",
        methodology="M5_STATUTORY_VALUATION",
        basis="STATUTORY",
        currency="USD",
        component_name="Interest Accretion",
        component_order=2,
        is_movement=True,
        opening_balance=1000000.0,
        movement_amount=50000.0,
        closing_balance=1050000.0,
        is_continuity_verified=True,
        continuity_difference=0.0,
        trace_identifier="trace-001",
        created_at=now,
    )
    session.add_all([rf1, rf2])

    # Seed rollforward template
    tmpl = RollforwardTemplate(
        id="tmpl-1",
        project_id="proj-100",
        name="Statutory Rollforward Schedule",
        description="Standard M5 Rollforward",
        model_version_id="mv-1",
        version_number=1,
        status="draft",
        configuration={"tolerance": 0.01},
        created_at=now,
    )
    session.add(tmpl)

    # Seed report
    rpt = Report(
        id="rpt-1",
        project_id="proj-100",
        name="Executive Valuation Summary",
        report_type="VALUATION_EXECUTIVE",
        version_number=1,
        status="draft",
        definition={"sections": ["reserves", "rollforward"]},
        created_at=now,
    )
    session.add(rpt)

    # Seed derived dataset
    ds = DerivedDataset(
        id="ds-1",
        project_id="proj-100",
        name="Attributed Valuation Movements",
        dataset_type="VALUATION_MOVEMENTS",
        version_number=1,
        status="draft",
        created_at=now,
    )
    session.add(ds)

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
                    SimpleNamespace(name="projects:read"),
                    SimpleNamespace(name="projects:write"),
                    SimpleNamespace(name="registries:read"),
                    SimpleNamespace(name="registries:write"),
                    SimpleNamespace(name="runs:read"),
                ],
            )
        ],
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_get_valuation_detail_with_detail_record(client_with_db):
    response = client_with_db.get("/v1/runs/run-val-1/valuation-detail")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-val-1"
    assert data["has_valuation_detail"] is True
    assert data["methodology"] == "M5_STATUTORY_VALUATION"
    assert data["basis"] == "STATUTORY"
    assert data["valuation_date"] == "2026-01-01"
    assert data["summary_results"]["reserve_at_valuation"] == 1250000.0


def test_get_rollforward_summary(client_with_db):
    response = client_with_db.get("/v1/runs/run-val-1/rollforward")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-val-1"
    assert data["count"] == 2
    assert data["recomputed_continuity_verified"] is True
    assert data["stored_continuity_verified"] is True
    items = data["items"]
    assert len(items) == 2
    assert items[0]["component_name"] == "Beginning Balance"
    assert items[1]["component_name"] == "Interest Accretion"
    assert items[1]["movement_amount"] == 50000.0


def test_get_rollforward_templates_under_v1(client_with_db):
    response = client_with_db.get("/v1/projects/proj-100/rollforward-templates")
    assert response.status_code == 200
    data = response.json()
    assert "templates" in data
    assert len(data["templates"]) == 1
    assert data["templates"][0]["name"] == "Statutory Rollforward Schedule"


def test_get_reports_under_v1(client_with_db):
    response = client_with_db.get("/v1/projects/proj-100/reports")
    assert response.status_code == 200
    data = response.json()
    assert "reports" in data
    assert len(data["reports"]) == 1
    assert data["reports"][0]["name"] == "Executive Valuation Summary"


def test_get_derived_datasets_under_v1(client_with_db):
    response = client_with_db.get("/v1/projects/proj-100/derived-datasets")
    assert response.status_code == 200
    data = response.json()
    assert "derived_datasets" in data
    assert len(data["derived_datasets"]) == 1
    assert data["derived_datasets"][0]["name"] == "Attributed Valuation Movements"
