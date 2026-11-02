"""Tests for Dagster Workflow Orchestration (M5 Directive 1).

Per CAS §9 and §15.8:
Verifies:
1. Complete 8-stage software-defined asset graph.
2. In-process asset materialization across the end-to-end pipeline:
   Inforce -> Validation -> Projection -> Aggregation -> Valuation ->
   Rollforward -> Reconciliation -> Reporting.
3. Dagster is a pure orchestrator: actual domain services perform all actuarial calculations.
4. Real database persistence across the pipeline:
   - Data Manager inforce input verified
   - Canonical projection Run & Parquet RunArtifact persisted
   - Aggregation consumes persisted outputs
   - Canonical valuation Run & RunPackage & ValuationRunDetail persisted
   - Rollforward continuity verified and RollforwardSummary persisted
   - Governed reconciliation executed and ReconciliationReport persisted
   - Final disclosure package and reporting RunArtifact persisted
5. Real Dagster Retry Demonstration:
   - Demonstrates that a transient infrastructure failure triggers an actual retry,
     advancing attempt count and succeeding on retry.
6. Deterministic failure trapping halts immediately with allow_retries=False.
"""

from decimal import Decimal
import hashlib
import json
import uuid
from dagster import Failure, RetryPolicy, RetryRequested, asset, build_asset_context, materialize
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.database as db_mod
from app.core.artifacts import get_artifact_store
from app.db.database import Base
from app.db.models.project import Project
from app.db.models.reconciliation import ReconciliationReport
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunPackage
from app.db.models.user import User
from app.db.models.valuation_detail import ValuationRunDetail
from app.orchestration.dagster.assets import (
    DEFAULT_RETRY_POLICY,
    InforceConfig,
    ProjectionConfig,
    ReconciliationConfig,
    ValuationConfig,
    aggregation_asset,
    inforce_data_asset,
    projection_run_asset,
    reconciliation_report_asset,
    reporting_asset,
    rollforward_asset,
    validation_asset,
    valuation_asset,
)
from app.orchestration.dagster.definitions import defs


@pytest.fixture(autouse=True)
def init_test_db(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)

    with TestSessionLocal() as db:
        # Seed test project and user
        proj_id = "proj-m5-valuation-default"
        user = User(id="user-dagster-test", email="dagster@test.com", full_name="Dagster Actuary", password_hash="hash")
        db.add(user)
        db.flush()
        proj = Project(id=proj_id, name="Dagster Governed Project", created_by=user.id)
        db.add(proj)
        db.commit()

    yield
    from sqlalchemy.orm import close_all_sessions
    close_all_sessions()
    test_engine.dispose()


def test_dagster_definitions_structure():
    """Verify that defs contains the expected 8 assets and pipeline job."""
    asset_keys = {key.path[-1] for key in defs.resolve_asset_graph().get_all_asset_keys()}
    expected_keys = {
        "inforce_data_asset",
        "validation_asset",
        "projection_run_asset",
        "aggregation_asset",
        "valuation_asset",
        "rollforward_asset",
        "reconciliation_report_asset",
        "reporting_asset",
    }
    assert expected_keys.issubset(asset_keys), f"Missing assets: {expected_keys - asset_keys}"

    job = defs.resolve_job_def("mentoramp_valuation_pipeline_job")
    assert job is not None
    assert job.name == "mentoramp_valuation_pipeline_job"


def test_dagster_end_to_end_pipeline_materialization_and_physical_persistence():
    """Execute all 8 assets in dependency sequence via Dagster in-process materialization.

    Proves Directive 1 invariants:
    - Real service calls persist real Parquet files to disk.
    - Physical artifact SHA-256 matches RunArtifact / event metadata.
    - Downstream stages consume actual upstream run IDs.
    - Valuation foundation executes generic PV without claiming VM21/VM22 compliance.
    - Dynamic manifest SHA-256 is verified.
    - Database verification: queries actual database rows created during execution:
      runs, run_packages, run_artifacts, valuation_run_details, rollforward_summaries, reconciliation_reports.
    """
    store = get_artifact_store()
    assets = [
        inforce_data_asset,
        validation_asset,
        projection_run_asset,
        aggregation_asset,
        valuation_asset,
        rollforward_asset,
        reconciliation_report_asset,
        reporting_asset,
    ]

    result = materialize(assets)
    assert result.success is True

    mat_events = result.get_asset_materialization_events()
    assert len(mat_events) == 8

    # 1. Inforce asset: verifies persisted inforce artifact
    inforce_event = [e for e in mat_events if e.asset_key.path[-1] == "inforce_data_asset"][0]
    inforce_meta = inforce_event.materialization.metadata
    assert inforce_meta["record_count"].value == 3
    assert len(inforce_meta["fingerprint"].value) == 64
    inforce_uri = inforce_meta["storage_uri"].value
    assert inforce_uri.startswith("local://")
    inforce_path = store.path_for_uri(inforce_uri)
    assert inforce_path.exists(), f"Inforce file not persisted on disk: {inforce_path}"

    # 2. Validation asset
    val_event = [e for e in mat_events if e.asset_key.path[-1] == "validation_asset"][0]
    val_meta = val_event.materialization.metadata
    assert val_meta["validation_status"].value == "clean"
    assert val_meta["error_count"].value == 0
    assert val_meta["record_count"].value == 3

    # 3. Projection asset: proves real Parquet persistence and fingerprint integrity
    proj_event = [e for e in mat_events if e.asset_key.path[-1] == "projection_run_asset"][0]
    proj_meta = proj_event.materialization.metadata
    assert proj_meta["time_steps"].value == 12
    assert proj_meta["row_count"].value == 36  # 3 policies * 12 months
    proj_fp = proj_meta["artifact_fingerprint"].value
    assert len(proj_fp) == 64
    proj_uri = proj_meta["storage_uri"].value
    assert proj_uri.startswith("local://")

    # PROVE: Projection output was REALLY persisted to physical storage
    proj_path = store.path_for_uri(proj_uri)
    assert proj_path.exists(), f"Physical Parquet file does not exist at {proj_path}"
    physical_sha = hashlib.sha256(proj_path.read_bytes()).hexdigest()
    assert physical_sha == proj_fp, "Physical Parquet hash must match artifact_fingerprint"

    # 4. Aggregation asset: consumes upstream run_id and aggregates from disk
    agg_event = [e for e in mat_events if e.asset_key.path[-1] == "aggregation_asset"][0]
    agg_meta = agg_event.materialization.metadata
    assert agg_meta["upstream_run_id"].value == proj_meta["run_id"].value
    assert agg_meta["total_cash_flow_sum"].value > 0.0
    assert agg_meta["ending_stock"].value > 0.0
    assert agg_meta["time_steps_aggregated"].value == 12

    # 5. Valuation asset: verifies generic TVOM foundation without claiming VM21/VM22 compliance
    valn_event = [e for e in mat_events if e.asset_key.path[-1] == "valuation_asset"][0]
    valn_meta = valn_event.materialization.metadata
    assert valn_meta["basis"].value == "STATUTORY"
    assert valn_meta["methodology"].value == "VM22"
    assert valn_meta["present_value_reserve"].value > 0.0
    assert valn_meta["compliance_status"].value == "FOUNDATION_GENERIC_PV"
    valn_run_pkg_fp = valn_meta["run_package_fingerprint"].value
    assert len(valn_run_pkg_fp) == 64

    # 6. Rollforward asset: Decimal continuity
    rf_event = [e for e in mat_events if e.asset_key.path[-1] == "rollforward_asset"][0]
    rf_meta = rf_event.materialization.metadata
    assert rf_meta["is_continuous"].value is True
    assert rf_meta["opening_balance"].value == 5000.0
    assert rf_meta["ending_balance"].value == 6000.0
    assert rf_meta["difference"].value == 0.0

    # 7. Reconciliation asset: defaults to NOT_VERIFIABLE when no benchmark supplied
    rec_event = [e for e in mat_events if e.asset_key.path[-1] == "reconciliation_report_asset"][0]
    rec_meta = rec_event.materialization.metadata
    assert rec_meta["reconciliation_status"].value == "NOT_VERIFIABLE"
    assert rec_meta["difference"].value == 0.0

    # 8. Reporting asset: persists final disclosure package and produces verified manifest SHA-256
    rep_event = [e for e in mat_events if e.asset_key.path[-1] == "reporting_asset"][0]
    rep_meta = rep_event.materialization.metadata
    assert rep_meta["rollforward_verified"].value is True
    assert rep_meta["reconciliation_status"].value == "NOT_VERIFIABLE"
    manifest_sha = rep_meta["manifest_sha256"].value
    assert len(manifest_sha) == 64
    rep_uri = rep_meta["storage_uri"].value
    assert rep_uri.startswith("local://")
    rep_path = store.path_for_uri(rep_uri)
    assert rep_path.exists(), f"Disclosure package not persisted at {rep_path}"
    assert hashlib.sha256(rep_path.read_bytes()).hexdigest() == manifest_sha

    # =========================================================================
    # REAL DATABASE EVIDENCE: Query and verify persisted objects
    # =========================================================================
    with db_mod.SessionLocal() as db:
        # 1. Verify projection Run row
        proj_run_id = proj_meta["run_id"].value
        db_proj_run = db.get(Run, proj_run_id)
        assert db_proj_run is not None, f"Projection run '{proj_run_id}' not found in database!"
        assert db_proj_run.status == "success"
        assert db_proj_run.run_type == "projection"

        # 2. Verify projection RunArtifact row
        proj_art_id = proj_meta["artifact_id"].value
        db_proj_art = db.get(RunArtifact, proj_art_id)
        assert db_proj_art is not None, f"Projection RunArtifact '{proj_art_id}' not found in DB!"
        assert db_proj_art.checksum_sha256 == proj_fp
        assert db_proj_art.artifact_type == "cash_flows"

        # 3. Verify valuation Run row
        val_runs = db.query(Run).filter(Run.run_type == "valuation", Run.source_projection_run_id == proj_run_id).all()
        assert len(val_runs) >= 1, "Valuation Run not persisted in DB!"
        val_run = val_runs[0]
        assert val_run.basis == "STATUTORY"
        assert val_run.methodology == "VM22"
        assert val_run.status == "success"

        # 4. Verify valuation RunPackage row
        val_pkg = db.query(RunPackage).filter(RunPackage.run_id == val_run.id).first()
        assert val_pkg is not None, "Valuation RunPackage not persisted in DB!"
        assert val_pkg.fingerprint == valn_run_pkg_fp
        assert val_pkg.package["configuration"]["valuation"]["source_projection_run_id"] == proj_run_id

        # 5. Verify ValuationRunDetail row
        val_detail = db.get(ValuationRunDetail, val_run.id)
        assert val_detail is not None, "ValuationRunDetail not persisted in DB!"
        assert val_detail.summary_results["present_value_reserve"] == valn_meta["present_value_reserve"].value

        # 6. Verify RollforwardSummary row
        rf_row = db.query(RollforwardSummary).filter(RollforwardSummary.run_id == val_run.id).first()
        assert rf_row is not None, "RollforwardSummary not persisted in DB!"
        assert rf_row.is_continuity_verified is True
        assert rf_row.opening_balance == 5000.0
        assert rf_row.closing_balance == 6000.0

        # 7. Verify ReconciliationReport row
        rec_row = db.query(ReconciliationReport).filter(ReconciliationReport.run_id == val_run.id).first()
        assert rec_row is not None, "ReconciliationReport not persisted in DB!"
        assert rec_row.status == "NOT_VERIFIABLE"

        # 8. Verify reporting RunArtifact row
        rep_art_id = rep_meta["artifact_id"].value
        db_rep_art = db.get(RunArtifact, rep_art_id)
        assert db_rep_art is not None, f"Reporting RunArtifact '{rep_art_id}' not found in DB!"
        assert db_rep_art.checksum_sha256 == manifest_sha
        assert db_rep_art.artifact_type == "reporting"


def test_dagster_real_retry_on_transient_infrastructure_failure():
    """Demonstrate a REAL Dagster retry in action under RetryPolicy (Blocker 1).

    Proves:
    - Attempt 1 encounters transient infrastructure contention and raises RetryRequested.
    - Dagster catches the retry request, triggers step retry (attempt #2).
    - Attempt 2 succeeds.
    - Materialize overall succeeds with attempt tracker recording [1, 2].
    """
    attempt_history = []

    @asset(
        retry_policy=RetryPolicy(max_retries=2, delay=0.05),
        description="Asset demonstrating real Dagster retry on transient DB connection failure.",
    )
    def transient_infrastructure_asset():
        attempt_num = len(attempt_history) + 1
        attempt_history.append(attempt_num)
        if attempt_num == 1:
            # Simulate transient database / network connection glitch
            raise RetryRequested(max_retries=2, seconds_to_wait=0.05)
        return {"status": "recovered_on_retry", "attempt": attempt_num}

    res = materialize([transient_infrastructure_asset])
    assert res.success is True, "Pipeline materialization must succeed after transient retry!"
    assert attempt_history == [1, 2], f"Expected exactly 2 attempts [1, 2], got {attempt_history}"


def test_dagster_failure_trapping_empty_inforce():
    """Verify that an empty inforce block fails immediately with allow_retries=False."""
    context = build_asset_context()
    empty_inforce = {"status": "ingested", "record_count": 0, "fingerprint": "empty", "policies": []}

    with pytest.raises(Failure) as exc_info:
        validation_asset(context, empty_inforce)

    assert "Inforce validation failed: Block contains 0 records" in str(exc_info.value.description)
    assert exc_info.value.allow_retries is False


def test_dagster_failure_trapping_invalid_policy_attributes():
    """Verify that corrupt policy attributes fail validation with allow_retries=False."""
    context = build_asset_context()
    corrupt_inforce = {
        "status": "ingested",
        "fingerprint": "test_corrupt_fp",
        "policies": [
            {"policy_id": "P1", "issue_age": -5, "premium": 1000.0, "account_value": 1000.0},
        ],
    }

    with pytest.raises(Failure) as exc_info:
        validation_asset(context, corrupt_inforce)

    assert "Invalid issue_age" in str(exc_info.value.description)
    assert exc_info.value.allow_retries is False


def test_dagster_reconciliation_discrepancy_and_tolerance():
    """Verify three-state reconciliation reporting on benchmark comparison with real DB persistence."""
    context = build_asset_context()
    val_run_id = f"run-test-rec-{uuid.uuid4().hex[:8]}"

    with db_mod.SessionLocal() as db:
        # Create parent run so foreign key constraint passes
        r = Run(id=val_run_id, project_id="proj-m5-valuation-default", status="success", run_type="valuation")
        db.add(r)
        db.commit()

    val_out = {
        "valuation_run_id": val_run_id,
        "project_id": "proj-m5-valuation-default",
        "present_value_reserve": 125000.00,
        "basis": "STATUTORY",
        "methodology": "VM22",
    }
    rf_out = {"is_continuous": True}

    # Case 1: Match within tolerance -> PASS
    cfg_pass = ReconciliationConfig(expected_benchmark=125000.005, tolerance=0.01)
    pass_res = reconciliation_report_asset(context, cfg_pass, val_out, rf_out)
    assert pass_res.value["status"] == "PASS"

    # Case 2: Discrepancy beyond tolerance -> FAIL
    cfg_fail = ReconciliationConfig(expected_benchmark=130000.00, tolerance=0.01)
    fail_res = reconciliation_report_asset(context, cfg_fail, val_out, rf_out)
    assert fail_res.value["status"] == "FAIL"
    assert fail_res.value["difference"] == 5000.00

    # Case 3: Missing benchmark -> NOT_VERIFIABLE (No fake identity pass)
    cfg_unverifiable = ReconciliationConfig(expected_benchmark=None)
    unverifiable_res = reconciliation_report_asset(context, cfg_unverifiable, val_out, rf_out)
    assert unverifiable_res.value["status"] == "NOT_VERIFIABLE"
    assert unverifiable_res.value["expected_benchmark"] is None
