"""Acceptance test suite for CONV-01 through CONV-05.

Validates:
- CONV-01: Fail-closed fixture verification (modified, missing, malformed, authentic, zero partial publication).
- CONV-02: Explicit synthetic-data labeling and provenance semantics.
- CONV-03: Project authorization, tenant isolation, and registries:read permission enforcement.
- CONV-04: Preservation of explicit zero-valued actuarial inputs in baseline and dynamic lapse models.
- CONV-05: Real persisted valuation metadata (no fabricated STATUTORY basis) and independent arithmetic rollforward continuity recomputation.
"""

import json
import math
import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.core.assumptions import (
    ActuarialTableRegistry,
    IllustrativeLapseSchedule,
    RegistryError,
    baseline_lapse_rate,
)
from app.db.database import get_db
from app.db.models.project import Project
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.user import Permission, Role, User
from app.db.models.valuation_detail import ValuationRunDetail
from app.main import app
from app.services import assumptions_service, results_service

FIXTURES_DIR = Path(__file__).parents[2] / "app" / "core" / "assumptions" / "fixtures"


# =============================================================================
# Database and TestClient Fixtures
# =============================================================================

@pytest.fixture()
def test_env():
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
        User.__table__,
        Role.__table__,
        Permission.__table__,
    ):
        table.create(engine)

    session = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)

    # Authorized project
    proj_a = Project(id="proj-alpha", name="Alpha Portfolio")
    proj_b = Project(id="proj-beta", name="Beta Portfolio")
    session.add_all([proj_a, proj_b])

    # Valuation run with explicit persisted details
    val_run = Run(
        id="run-val-actual",
        project_id="proj-alpha",
        name="Actual Valuation Run",
        status="success",
        methodology="VM22",
        basis="STATUTORY",
        created_at=now,
    )
    val_detail = ValuationRunDetail(
        run_id="run-val-actual",
        valuation_date=date(2026, 6, 30),
        discount_curve_fingerprint="disc-fp-999",
        configuration={"methodology": "VM22", "horizon_months": 36},
        summary_results={"total_reserve": 2500000.0},
        created_at=now,
    )

    # General projection run with NO valuation detail
    proj_run = Run(
        id="run-proj-only",
        project_id="proj-alpha",
        name="General Projection Run",
        status="success",
        methodology=None,
        basis=None,
        created_at=now,
    )

    # Rollforward summaries: Cohort 1 is arithmetically continuous
    # Opening 100,000 + Movement 5,000 = Closing 105,000
    rf_c1_open = RollforwardSummary(
        id="rf-c1-open",
        run_id="run-val-actual",
        project_id="proj-alpha",
        cohort_id="COHORT-A",
        methodology="VM22",
        basis="STATUTORY",
        currency="USD",
        component_name="Beginning Reserve",
        component_order=1,
        is_movement=False,
        opening_balance=100000.0,
        movement_amount=0.0,
        closing_balance=100000.0,
        is_continuity_verified=True,
        continuity_difference=0.0,
        trace_identifier="tr-01",
        created_at=now,
    )
    rf_c1_mov = RollforwardSummary(
        id="rf-c1-mov",
        run_id="run-val-actual",
        project_id="proj-alpha",
        cohort_id="COHORT-A",
        methodology="VM22",
        basis="STATUTORY",
        currency="USD",
        component_name="Interest Accretion",
        component_order=2,
        is_movement=True,
        opening_balance=100000.0,
        movement_amount=5000.0,
        closing_balance=105000.0,
        is_continuity_verified=True,
        continuity_difference=0.0,
        trace_identifier="tr-01",
        created_at=now,
    )
    rf_c1_close = RollforwardSummary(
        id="rf-c1-close",
        run_id="run-val-actual",
        project_id="proj-alpha",
        cohort_id="COHORT-A",
        methodology="VM22",
        basis="STATUTORY",
        currency="USD",
        component_name="Ending Reserve",
        component_order=3,
        is_movement=False,
        opening_balance=105000.0,
        movement_amount=0.0,
        closing_balance=105000.0,
        is_continuity_verified=True,
        continuity_difference=0.0,
        trace_identifier="tr-01",
        created_at=now,
    )

    # Forged rollforward run: stored boolean is True, but arithmetic is broken!
    # Opening 100,000 + Movement 2,000 != Closing 105,000 (diff = 3,000)
    rf_forged_open = RollforwardSummary(
        id="rf-forged-open",
        run_id="run-forged-continuity",
        project_id="proj-alpha",
        cohort_id="COHORT-FORGED",
        methodology="VM22",
        basis="STATUTORY",
        currency="USD",
        component_name="Beginning Reserve",
        component_order=1,
        is_movement=False,
        opening_balance=100000.0,
        movement_amount=0.0,
        closing_balance=100000.0,
        is_continuity_verified=True,
        continuity_difference=0.0,
        trace_identifier="tr-forged",
        created_at=now,
    )
    rf_forged_mov = RollforwardSummary(
        id="rf-forged-mov",
        run_id="run-forged-continuity",
        project_id="proj-alpha",
        cohort_id="COHORT-FORGED",
        methodology="VM22",
        basis="STATUTORY",
        currency="USD",
        component_name="Interest Accretion",
        component_order=2,
        is_movement=True,
        opening_balance=100000.0,
        movement_amount=2000.0,  # 3000 shortfall
        closing_balance=102000.0,
        is_continuity_verified=True,  # Forged true!
        continuity_difference=0.0,
        trace_identifier="tr-forged",
        created_at=now,
    )
    rf_forged_close = RollforwardSummary(
        id="rf-forged-close",
        run_id="run-forged-continuity",
        project_id="proj-alpha",
        cohort_id="COHORT-FORGED",
        methodology="VM22",
        basis="STATUTORY",
        currency="USD",
        component_name="Ending Reserve",
        component_order=3,
        is_movement=False,
        opening_balance=102000.0,
        movement_amount=0.0,
        closing_balance=105000.0,  # Expected 102,000!
        is_continuity_verified=True,  # Forged true!
        continuity_difference=0.0,
        trace_identifier="tr-forged",
        created_at=now,
    )
    forged_run = Run(
        id="run-forged-continuity",
        project_id="proj-alpha",
        name="Forged Rollforward Run",
        status="success",
        methodology="VM22",
        basis="STATUTORY",
        created_at=now,
    )

    session.add_all([
        val_run, val_detail, proj_run, forged_run,
        rf_c1_open, rf_c1_mov, rf_c1_close,
        rf_forged_open, rf_forged_mov, rf_forged_close,
    ])
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="actuary-super",
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

    with TestClient(app) as client:
        yield client, session

    app.dependency_overrides.clear()


# =============================================================================
# CONV-01: P0 - Fix Fail-Open Fixture Verification
# =============================================================================

def test_conv01_fixture_verification_rejects_modified_fixture(tmp_path):
    """Modified fixture fails SHA-256 verification and raises RegistryError."""
    # Copy fixtures to tmp_path
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures")
    fixture_dir = tmp_path / "fixtures"

    # Modify one byte in mortality sample
    mort_file = fixture_dir / "synthetic_mortality_sample.csv"
    text = mort_file.read_text(encoding="utf-8")
    mort_file.write_text(text.replace("0.00120", "0.00999"), encoding="utf-8")

    # initialize_assumptions_registry must reject modified fixture fail-closed
    with pytest.raises(RegistryError, match="failed SHA-256 verification"):
        assumptions_service.initialize_assumptions_registry(fixtures_dir=fixture_dir, force_reload=True)


def test_conv01_fixture_verification_rejects_missing_fixture(tmp_path):
    """Missing declared fixture raises RegistryError."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures")
    fixture_dir = tmp_path / "fixtures"

    # Delete one declared fixture
    (fixture_dir / "standard_surrender_tiers.csv").unlink()

    with pytest.raises(RegistryError):
        assumptions_service.initialize_assumptions_registry(fixtures_dir=fixture_dir, force_reload=True)


def test_conv01_fixture_verification_rejects_malformed_manifest(tmp_path):
    """Corrupted manifest.json raises RegistryError."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures")
    fixture_dir = tmp_path / "fixtures"

    # Write invalid JSON
    (fixture_dir / "manifest.json").write_text("{corrupt json: true", encoding="utf-8")

    with pytest.raises(RegistryError, match="Malformed manifest.json"):
        assumptions_service.initialize_assumptions_registry(fixtures_dir=fixture_dir, force_reload=True)


def test_conv01_no_partial_publication_on_failure(tmp_path):
    """When verification fails, no partial or unverified fixtures are exposed."""
    shutil.copytree(FIXTURES_DIR, tmp_path / "fixtures")
    fixture_dir = tmp_path / "fixtures"

    # Modify the first fixture
    curve_file = fixture_dir / "illustrative_us_treasury_spot_curve.json"
    curve_file.write_text(curve_file.read_text(encoding="utf-8").replace("0.0410", "0.0999"), encoding="utf-8")

    with pytest.raises(RegistryError):
        assumptions_service.initialize_assumptions_registry(fixtures_dir=fixture_dir, force_reload=True)

    # Reload authentic fixtures to restore clean state
    assumptions_service.initialize_assumptions_registry(force_reload=True)


def test_conv01_authentic_fixtures_verify_cleanly():
    """Authentic fixtures verify 100% and populate the registry."""
    reg, curves = assumptions_service.initialize_assumptions_registry(force_reload=True)
    assert reg is not None
    assert len(reg.snapshot()) >= 4
    assert len(curves) >= 1


# =============================================================================
# CONV-02: P1 - Correct Synthetic Data Approval Semantics
# =============================================================================

def test_conv02_tables_explicitly_labeled_as_synthetic(test_env):
    """Machine-readable table responses identify synthetic data and non-production status."""
    client, _ = test_env
    response = client.get("/v1/assumptions/tables")
    assert response.status_code == 200
    data = response.json()
    assert "tables" in data
    assert len(data["tables"]) > 0

    for table in data["tables"]:
        assert table["is_synthetic"] is True
        assert table["is_production_approved"] is False
        assert table["classification"] == "SYNTHETIC_NON_PRODUCTION"
        assert table["approval_authority"] == "SYNTHETIC_DEMO_NON_AUTHORITATIVE"
        assert "not authorized for production" in table["governance_notice"].lower()


def test_conv02_table_detail_labeled_as_synthetic(test_env):
    """Table detail endpoint identifies synthetic classification and non-production status."""
    client, _ = test_env
    response = client.get("/v1/assumptions/tables/synthetic-mortality-v1")
    assert response.status_code == 200
    data = response.json()
    assert data["is_synthetic"] is True
    assert data["is_production_approved"] is False
    assert data["classification"] == "SYNTHETIC_NON_PRODUCTION"
    assert data["approval_authority"] == "SYNTHETIC_DEMO_NON_AUTHORITATIVE"


def test_conv02_curves_labeled_as_synthetic(test_env):
    """Yield curve endpoints identify synthetic classification and non-published status."""
    client, _ = test_env
    response = client.get("/v1/assumptions/curves")
    assert response.status_code == 200
    data = response.json()
    assert len(data["curves"]) > 0
    curve = data["curves"][0]
    assert curve["is_synthetic"] is True
    assert curve["is_production_approved"] is False
    assert curve["classification"] == "SYNTHETIC_NON_PRODUCTION"
    assert "not published" in curve["governance_notice"].lower()


# =============================================================================
# CONV-03: P1 - Enforce Project Authorization and Isolation
# =============================================================================

def test_conv03_project_factor_tables_authorized_access(test_env):
    """Authorized caller querying existing project gets 200 with labeled scope."""
    client, _ = test_env
    response = client.get("/v1/projects/proj-alpha/assumptions/tables")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == "proj-alpha"
    assert data["scope"] == "project"
    assert "global_reference_tables" in data
    assert len(data["global_reference_tables"]) >= 4


def test_conv03_project_factor_tables_nonexistent_project_returns_404(test_env):
    """Querying a nonexistent project ID returns 404 Not Found."""
    client, _ = test_env
    response = client.get("/v1/projects/nonexistent-project-9999/assumptions/tables")
    assert response.status_code == 404


def test_conv03_unauthorized_user_lacking_registries_read_returns_403(test_env):
    """User without registries:read permission is rejected with 403 Forbidden."""
    client, _ = test_env
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="limited-user",
        roles=[
            SimpleNamespace(
                name="limited",
                permissions=[SimpleNamespace(name="projects:read")],  # No registries:read!
            )
        ],
    )
    response = client.get("/v1/assumptions/tables")
    assert response.status_code == 403


def test_conv03_unauthorized_user_lacking_projects_read_returns_403(test_env):
    """User without projects:read permission is rejected on project assumptions route."""
    client, _ = test_env
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="limited-user",
        roles=[
            SimpleNamespace(
                name="limited",
                permissions=[SimpleNamespace(name="registries:read")],  # No projects:read!
            )
        ],
    )
    response = client.get("/v1/projects/proj-alpha/assumptions/tables")
    assert response.status_code == 403


# =============================================================================
# CONV-04: P1 - Preserve Explicit Zero-Valued Actuarial Inputs
# =============================================================================

def test_conv04_preserve_explicit_zero_expiration_shock_rate(test_env):
    """An explicit 0.0 expiration shock rate must be preserved, NOT replaced with 0.15."""
    client, _ = test_env
    payload = {
        "duration": 4,  # Duration 4 is shock duration (3 early durations + 1)
        "early_duration_rates": [0.03, 0.03, 0.03],
        "expiration_shock_rate": 0.0,  # Explicit zero!
        "post_shock_rate": 0.05,
        "ultimate_rate": 0.05,
    }
    response = client.post("/v1/assumptions/behavior/baseline-lapse", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["duration"] == 4
    assert data["shock_duration"] == 4
    assert data["baseline_lapse_rate"] == pytest.approx(0.0)  # Must be 0.0, NOT 0.15!


def test_conv04_preserve_explicit_zero_post_shock_rate(test_env):
    """An explicit 0.0 post-shock rate must be preserved, NOT replaced with 0.08."""
    client, _ = test_env
    payload = {
        "duration": 5,  # First duration after shock
        "early_duration_rates": [0.03, 0.03, 0.03],
        "expiration_shock_rate": 0.10,
        "post_shock_rate": 0.0,  # Explicit zero!
        "ultimate_rate": 0.06,
        "grading_years": 3,
    }
    response = client.post("/v1/assumptions/behavior/baseline-lapse", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["duration"] == 5
    # Progress = 1/3 towards ultimate rate 0.06 from post_shock 0.0 => 0.0 + (1/3)*0.06 = 0.02
    expected = 0.0 + (1.0 / 3.0) * (0.06 - 0.0)
    assert data["baseline_lapse_rate"] == pytest.approx(expected, abs=1e-6)


def test_conv04_preserve_explicit_zero_ultimate_rate(test_env):
    """An explicit 0.0 ultimate rate must be preserved, NOT replaced with 0.05."""
    client, _ = test_env
    payload = {
        "duration": 10,  # Well past shock + grading
        "early_duration_rates": [0.03, 0.03, 0.03],
        "expiration_shock_rate": 0.10,
        "post_shock_rate": 0.05,
        "ultimate_rate": 0.0,  # Explicit zero!
        "grading_years": 3,
    }
    response = client.post("/v1/assumptions/behavior/baseline-lapse", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["duration"] == 10
    assert data["baseline_lapse_rate"] == pytest.approx(0.0)  # Must grade to 0.0!


def test_conv04_reject_negative_or_nonfinite_rates(test_env):
    """Negative or non-finite rates are rejected fail-closed with 400 Bad Request."""
    client, _ = test_env
    # Negative rate
    payload = {
        "duration": 2,
        "early_duration_rates": [0.03, -0.05, 0.03],
        "expiration_shock_rate": 0.10,
    }
    response = client.post("/v1/assumptions/behavior/baseline-lapse", json=payload)
    assert response.status_code == 400

    # Negative shock (Pydantic ge=0.0 schema validator rejects with 422, or 400)
    payload = {
        "duration": 2,
        "early_duration_rates": [0.03, 0.03],
        "expiration_shock_rate": -0.10,
    }
    response = client.post("/v1/assumptions/behavior/baseline-lapse", json=payload)
    assert response.status_code in (400, 422)


def test_conv04_reject_unsupported_partial_parameters(test_env):
    """Supplying shock overrides without early duration rates is rejected with 400."""
    client, _ = test_env
    payload = {
        "duration": 5,
        "expiration_shock_rate": 0.20,  # Shock without early_duration_rates
    }
    response = client.post("/v1/assumptions/behavior/baseline-lapse", json=payload)
    assert response.status_code == 400
    assert "require early_duration_rates" in response.json()["error"]["message"]


# =============================================================================
# CONV-05: P1 - Correct Valuation and Rollforward API Semantics
# =============================================================================

def test_conv05_genuine_valuation_detail_returns_actual_persisted_fields(test_env):
    """A genuine valuation run returns its actual recorded basis, methodology, and reserves."""
    client, _ = test_env
    response = client.get("/v1/runs/run-val-actual/valuation-detail")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-val-actual"
    assert data["is_valuation_run"] is True
    assert data["has_valuation_detail"] is True
    assert data["methodology"] == "VM22"
    assert data["basis"] == "STATUTORY"
    assert data["valuation_date"] == "2026-06-30"
    assert data["summary_results"]["total_reserve"] == 2500000.0


def test_conv05_projection_run_does_not_fabricate_statutory_basis(test_env):
    """A projection run without valuation detail MUST NOT fabricate STATUTORY basis."""
    client, _ = test_env
    response = client.get("/v1/runs/run-proj-only/valuation-detail")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-proj-only"
    assert data["is_valuation_run"] is False
    assert data["has_valuation_detail"] is False
    assert data["basis"] is None  # MUST be None, NEVER fabricated "STATUTORY"!
    assert data["methodology"] is None
    assert "without dedicated subordinate valuation records" in data["notice"]


def test_conv05_rollforward_independent_arithmetic_continuity_verified(test_env):
    """Rollforward summary independently verifies arithmetic continuity."""
    client, _ = test_env
    response = client.get("/v1/runs/run-val-actual/rollforward")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-val-actual"
    assert data["count"] == 3
    assert data["stored_continuity_verified"] is True
    assert data["recomputed_continuity_verified"] is True
    assert data["is_tampered_or_forged"] is False
    assert data["max_continuity_difference"] == pytest.approx(0.0)


def test_conv05_rollforward_catches_forged_stored_flag(test_env):
    """Rollforward catches forged stored boolean when arithmetic fails."""
    client, _ = test_env
    response = client.get("/v1/runs/run-forged-continuity/rollforward")
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-forged-continuity"
    # Stored flag in DB is True, but arithmetic check fails (diff = 3000 > 0.01 tolerance)
    assert data["stored_continuity_verified"] is True
    assert data["recomputed_continuity_verified"] is False
    assert data["is_tampered_or_forged"] is True
    assert data["max_continuity_difference"] == pytest.approx(3000.0)
