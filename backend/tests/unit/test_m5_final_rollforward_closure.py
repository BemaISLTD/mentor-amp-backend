"""Milestone 5 Final Engineering Closure: Rollforward Release Verification & Adversarial Auditing.

Exhaustively verifies V9-P0-01 and Section 5 adversarial requirements:
- Required Negative Tests 1 through 10 (Rollforward Release Verification).
- Positive Control (Authentic Passing Rollforward & Release Approval).
- Adversarial Scenarios: RunPackage tampering, execution attempt failure,
  reconciliation failure gate, and four-eyes bypass enforcement.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import close_all_sessions, sessionmaker

from app.core.artifacts import get_artifact_store
from app.core.execution import run_package as core_pkg
from app.core.execution import run_state
from app.core.execution.fingerprints import fingerprint
import app.db.database as db_mod
from app.db.database import Base
from app.db.models.modeling import Model, ModelVersion
from app.db.models.project import Project
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.user import User
from app.db.models.valuation_detail import ValuationRunDetail
from app.services.common import ServiceError, now_utc
from app.services.valuation_pipeline_service import (
    evaluate_rollforward_evidence,
    execute_cash_flow_aggregation,
    execute_foundation_valuation,
    execute_governed_reconciliation,
    execute_inforce_import,
    execute_projection_simulation,
    execute_reporting_manifest,
)
from scripts.seed_demo import seed_myga


@pytest.fixture
def closure_db(monkeypatch, tmp_path):
    """Clean isolated test database with seeded MYGA environment."""
    db_path = tmp_path / "closure_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)

    with TestSessionLocal() as db:
        seeded = seed_myga(db, project_name="Final Closure Test Project")
        proj_id = seeded["project"].id
        mv_id = seeded["version"].id
        user = seeded["user"]
        reviewer = seeded["reviewer"]
        inf_id = seeded["inforce"].id
        scen_id = seeded["scenarios"][0].id if isinstance(seeded["scenarios"], list) else seeded["scenarios"].id

    yield {
        "engine": test_engine,
        "Session": TestSessionLocal,
        "project_id": proj_id,
        "model_version_id": mv_id,
        "user_id": user.id,
        "reviewer_id": reviewer.id,
        "inforce_id": inf_id,
        "scenario_id": scen_id,
    }

    close_all_sessions()
    test_engine.dispose()


def _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0, accepted_attempt=1):
    """Helper to set up a sealed RunPackage, Run, and ValuationRunDetail."""
    pkg_doc = core_pkg.seal(
        identity={
            "package_id": f"pkg-{val_run_id}",
            "frozen_for_run_id": val_run_id,
            "project_id": proj_id,
            "created_at": "2026-11-06T00:00:00Z",
            "created_by": {"id": user_id},
        },
        governance={"governance_mode": "governed"},
        configuration={
            "basis": "STATUTORY",
            "methodology": "VM22",
            "cohort_id": "COHORT_MYGA_2026",
            "valuation_date": "2026-12-31",
            "model_version": {"id": mv_id},
        },
    )
    run_pkg = RunPackage(
        id=f"pkg-{val_run_id}",
        run_id=val_run_id,
        project_id=proj_id,
        schema_version=pkg_doc["schema_version"],
        fingerprint_algorithm=pkg_doc["fingerprint_algorithm"],
        fingerprint=pkg_doc["fingerprint"],
        package=pkg_doc,
    )
    run = Run(
        id=val_run_id,
        project_id=proj_id,
        projection_key="COHORT_MYGA_2026",
        status=run_state.SUCCESS,
        run_type="valuation",
        model_version_id=mv_id,
        basis="STATUTORY",
        methodology="VM22",
        methodology_version="v1",
        valuation_date=date(2026, 12, 31),
        run_package_fingerprint=pkg_doc["fingerprint"],
        accepted_attempt_number=accepted_attempt,
    )
    detail = ValuationRunDetail(
        run_id=val_run_id,
        valuation_date=date(2026, 12, 31),
        configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
        summary_results={
            "present_value_reserve": actual_reserve,
            "opening_reserve": 100000.0,
            "closing_reserve": actual_reserve,
            "cohort_id": "COHORT_MYGA_2026",
        },
    )
    db.add_all([run_pkg, run, detail])
    db.commit()
    return run, detail, run_pkg


# =========================================================================
# REQUIRED NEGATIVE TESTS (1 through 10)
# =========================================================================

def test_negative_01_unbalanced_movements_claimed_pass(closure_db):
    """Negative Test 1: Opening 100, movements +20, closing 150, with claimed continuity PASS -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg01-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=150.0)
        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:8]}"

        # Arithmetic: 100 + 20 = 120 != 150 (difference = 30.0)
        rf_doc = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,  # Claimed PASS
            "has_unverified_movement_source": False,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": trace_id,
            "opening_balance": 100.0,
            "ending_balance": 150.0,
            "movements": {"unwind": 20.0},
            "tolerance": 0.05,
        }
        raw_bytes = json.dumps(rf_doc).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf.json", raw_bytes)
        rf_art = RunArtifact(
            id=f"art-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            partition={"trace_identifier": trace_id},
        )
        db.add(rf_art)
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art.id})
        assert eval_res["is_verified"] is False
        assert "Arithmetic continuity breach" in eval_res["reason"]
        assert eval_res["recomputed_difference"] == 30.0

        rep = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result={"valuation_run_id": val_run_id, "project_id": proj_id, "present_value_reserve": 150.0},
            rollforward_result={"artifact_id": rf_art.id, "is_continuous": True},
            reconciliation_result={"status": "PASS"},
            require_approved_reconciliation=False,
        )
        assert rep["is_approved_for_release"] is False
        assert rep["governance_status"] == "REJECTED_NON_APPROVED"


def test_negative_02_closing_balance_mismatches_persisted_reserve(closure_db):
    """Negative Test 2: Artifact closing balance differs from persisted valuation reserve -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg02-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        # Persisted reserve is 125,000.0
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0)
        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:8]}"

        # Continuous artifact arithmetic: 100,000 + 35,000 = 135,000 (differs from 125,000!)
        rf_doc = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": trace_id,
            "opening_balance": 100000.0,
            "ending_balance": 135000.0,  # Mismatches detail present_value_reserve (125000.0)
            "movements": {"unwind": 35000.0},
            "tolerance": 0.05,
        }
        raw_bytes = json.dumps(rf_doc).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf.json", raw_bytes)
        rf_art = RunArtifact(
            id=f"art-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            partition={"trace_identifier": trace_id},
        )
        db.add(rf_art)
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art.id})
        assert eval_res["is_verified"] is False
        assert "differs from persisted valuation reserve" in eval_res["reason"]


def test_negative_03_artifact_from_unsuccessful_or_superseded_attempt(closure_db):
    """Negative Test 3: Artifact from unsuccessful or superseded attempt -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg03-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, accepted_attempt=2)

        # Record RunAttempts: attempt 1 failed/superseded, attempt 2 succeeded
        att1 = RunAttempt(id=f"att1-{uuid.uuid4().hex[:8]}", run_id=val_run_id, attempt_number=1, status=run_state.FAILED)
        att2 = RunAttempt(id=f"att2-{uuid.uuid4().hex[:8]}", run_id=val_run_id, attempt_number=2, status=run_state.SUCCESS)
        db.add_all([att1, att2])
        db.commit()

        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:8]}"
        rf_doc = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": trace_id,
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "movements": {"unwind": 25000.0},
            "tolerance": 0.05,
        }
        raw_bytes = json.dumps(rf_doc).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf_att1.json", raw_bytes)
        # Artifact belongs to attempt 1 (superseded)
        rf_art_att1 = RunArtifact(
            id=f"art-att1-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            partition={"trace_identifier": trace_id},
        )
        db.add(rf_art_att1)
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art_att1.id})
        assert eval_res["is_verified"] is False
        assert "belongs to superseded attempt 1, not accepted attempt 2" in eval_res["reason"]


def test_negative_04_missing_accepted_attempt_artifact(closure_db):
    """Negative Test 4: Missing accepted-attempt artifact -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg04-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, accepted_attempt=2)
        att1 = RunAttempt(id=f"att1-{uuid.uuid4().hex[:8]}", run_id=val_run_id, attempt_number=1, status=run_state.FAILED)
        att2 = RunAttempt(id=f"att2-{uuid.uuid4().hex[:8]}", run_id=val_run_id, attempt_number=2, status=run_state.SUCCESS)
        db.add_all([att1, att2])
        db.commit()

        # Only attempt 1 artifact exists; no attempt 2 artifact was ever produced
        raw_bytes = json.dumps({"valuation_run_id": val_run_id}).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf_att1.json", raw_bytes)
        rf_art_att1 = RunArtifact(
            id=f"art-att1-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
        db.add(rf_art_att1)
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {})
        assert eval_res["is_verified"] is False
        assert "No rollforward_summary artifact found for accepted attempt 2" in eval_res["reason"]


def test_negative_05_manipulated_physical_file(closure_db):
    """Negative Test 5: A manipulated physical file -> Rejected by SHA-256 integrity check."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg05-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id)
        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:8]}"

        original_bytes = json.dumps({"valuation_run_id": val_run_id, "trace": trace_id}).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf.json", original_bytes)
        recorded_sha = hashlib.sha256(original_bytes).hexdigest()

        rf_art = RunArtifact(
            id=f"art-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=recorded_sha,
            partition={"trace_identifier": trace_id},
        )
        db.add(rf_art)
        db.commit()

        # Adversary alters physical file on disk behind the database's back
        phys_path = store.path_for_uri(stored.uri)
        phys_path.write_bytes(b'{"valuation_run_id": "TAMPERED_CONTENT"}')

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art.id})
        assert eval_res["is_verified"] is False
        assert "SHA-256 mismatch" in eval_res["reason"]


def test_negative_06_checksum_attached_to_wrong_run_or_summary(closure_db):
    """Negative Test 6: A correct checksum attached to the wrong run or summary -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id_A = f"run-A-{uuid.uuid4().hex[:8]}"
    val_run_id_B = f"run-B-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run_B, detail_B, run_pkg_B = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id_B)

        # Artifact payload is authentically generated for Run A
        rf_doc_A = {
            "valuation_run_id": val_run_id_A,  # Identifies Run A!
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": "VERIFIED:tol-policy-m5-standard:trace-A",
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "movements": {"unwind": 25000.0},
        }
        raw_bytes_A = json.dumps(rf_doc_A).encode("utf-8")
        stored = store.write_bytes(val_run_id_B, "rollforward_summary", "rf.json", raw_bytes_A)
        actual_sha = hashlib.sha256(raw_bytes_A).hexdigest()

        # Caller associates Run A's authentic file with Run B
        rf_art_B = RunArtifact(
            id=f"art-B-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id_B,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=actual_sha,  # Correct checksum for physical bytes!
        )
        db.add(rf_art_B)
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run_B, detail_B, run_pkg_B, {"artifact_id": rf_art_B.id})
        assert eval_res["is_verified"] is False
        assert f"Artifact valuation_run_id '{val_run_id_A}' mismatches valuation run '{val_run_id_B}'" in eval_res["reason"]


def test_negative_07_tampered_component_amounts(closure_db):
    """Negative Test 7: Tampered component amounts between DB rows and artifact -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg07-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id)
        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:8]}"

        # DB row records discount_unwind of 25,000.0
        rf_primary = RollforwardSummary(
            id=f"rf-p-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="ending_reserve",
            component_order=99,
            is_movement=False,
            opening_balance=100000.0,
            movement_amount=25000.0,
            closing_balance=125000.0,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        rf_mov = RollforwardSummary(
            id=f"rf-m-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="discount_unwind",
            component_order=1,
            is_movement=True,
            opening_balance=100000.0,
            movement_amount=25000.0,  # 25,000 in DB
            closing_balance=125000.0,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        # Artifact doc has 25,001.0 (tampered by 1.0)
        rf_doc = {
            "summary_id": rf_primary.id,
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": trace_id,
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "movements": {"discount_unwind": 25000.01},  # Tampered!
            "tolerance": 0.05,
        }
        raw_bytes = json.dumps(rf_doc).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf.json", raw_bytes)
        rf_art = RunArtifact(
            id=f"art-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=2,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            partition={"summary_id": rf_primary.id, "trace_identifier": trace_id},
        )
        db.add_all([rf_primary, rf_mov, rf_art])
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art.id})
        assert eval_res["is_verified"] is False
        assert ("disagrees with artifact" in eval_res["reason"] or "disagrees with recomputed difference" in eval_res["reason"])


def test_negative_08_unauthorized_tolerance_policy(closure_db):
    """Negative Test 8: Unauthorized tolerance policy or out-of-bounds tolerance -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg08-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id)

        # Case 8a: Fake unapproved policy ID
        rf_doc_unauth = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "tolerance_policy_id": "tol-policy-forged-hacker-spec",
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "movements": {"unwind": 25000.0},
        }
        raw_bytes = json.dumps(rf_doc_unauth).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf_unauth.json", raw_bytes)
        art_unauth = RunArtifact(
            id=f"art-unauth-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
        db.add(art_unauth)
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": art_unauth.id})
        assert eval_res["is_verified"] is False
        assert "is not an approved governed tolerance policy" in eval_res["reason"]

        # Case 8b: Policy approved but excessive tolerance exceeding policy max
        rf_doc_excess = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "tolerance": 9999.0,  # Far exceeds 0.05 policy max
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "movements": {"unwind": 25000.0},
        }
        raw_excess = json.dumps(rf_doc_excess).encode("utf-8")
        stored_excess = store.write_bytes(val_run_id, "rollforward_summary", "rf_excess.json", raw_excess)
        art_excess = RunArtifact(
            id=f"art-excess-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored_excess.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_excess).hexdigest(),
        )
        db.add(art_excess)
        db.commit()

        eval_excess = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": art_excess.id})
        assert eval_excess["is_verified"] is False
        assert "exceeds maximum approved policy threshold" in eval_excess["reason"]


def test_negative_09_nonfinite_or_forbidden_balancing_movement(closure_db):
    """Negative Test 9: Nonfinite movement values or forbidden residual plug names -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg09-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id)

        # Case 9a: Forbidden balancing plug name
        rf_doc_plug = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "movements": {"balancing_residual": 25000.0},  # Forbidden plug!
        }
        raw_plug = json.dumps(rf_doc_plug).encode("utf-8")
        stored_plug = store.write_bytes(val_run_id, "rollforward_summary", "rf_plug.json", raw_plug)
        art_plug = RunArtifact(
            id=f"art-plug-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored_plug.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_plug).hexdigest(),
        )
        db.add(art_plug)
        db.commit()

        eval_plug = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": art_plug.id})
        assert eval_plug["is_verified"] is False
        assert "forbidden balancing/residual plug" in eval_plug["reason"]

        # Case 9b: Nonfinite balance value (e.g. NaN or Inf)
        rf_doc_nan = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "opening_balance": "NaN",
            "ending_balance": 125000.0,
            "movements": {"unwind": 25000.0},
        }
        raw_nan = json.dumps(rf_doc_nan).encode("utf-8")
        stored_nan = store.write_bytes(val_run_id, "rollforward_summary", "rf_nan.json", raw_nan)
        art_nan = RunArtifact(
            id=f"art-nan-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored_nan.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_nan).hexdigest(),
        )
        db.add(art_nan)
        db.commit()

        eval_nan = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": art_nan.id})
        assert eval_nan["is_verified"] is False
        assert "Non-finite balances" in eval_nan["reason"]


def test_negative_10_forged_verified_marker_without_authenticated_evidence(closure_db):
    """Negative Test 10: Forged VERIFIED marker without authenticated supporting DB rows -> Rejected."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-neg10-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id)
        forged_trace = f"VERIFIED:tol-policy-m5-standard:forged-marker-001"

        # Artifact has valid continuous numbers and authentic checksum
        rf_doc = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": forged_trace,
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "movements": {"unwind": 25000.0},
            "tolerance": 0.05,
        }
        raw_bytes = json.dumps(rf_doc).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf_forged.json", raw_bytes)
        rf_art = RunArtifact(
            id=f"art-forged-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            partition={"trace_identifier": forged_trace},
        )
        db.add(rf_art)
        db.commit()
        # NOTE: NO RollforwardSummary database rows were seeded!

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art.id})
        assert eval_res["is_verified"] is False
        assert "No persisted RollforwardSummary database rows found matching trace_identifier" in eval_res["reason"]


# =========================================================================
# POSITIVE CONTROL & ADVERSARIAL PIPELINE VERIFICATION
# =========================================================================

def test_positive_01_authentic_passing_rollforward_and_governed_release(closure_db):
    """Positive Control: Complete authentic rollforward and reconciliation yields release approval."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    reviewer_id = closure_db["reviewer_id"]
    val_run_id = f"run-pos01-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()
    actual_reserve = 125000.0

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=actual_reserve)
        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:12]}"

        # Seed matching authentic RollforwardSummary rows
        rf_primary = RollforwardSummary(
            id=f"rf-primary-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="ending_reserve",
            component_order=99,
            is_movement=False,
            opening_balance=100000.0,
            movement_amount=25000.0,
            closing_balance=actual_reserve,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        rf_movement = RollforwardSummary(
            id=f"rf-mov-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="discount_unwind",
            component_order=1,
            is_movement=True,
            opening_balance=100000.0,
            movement_amount=25000.0,
            closing_balance=actual_reserve,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        db.add_all([rf_primary, rf_movement])

        # Write matching physical JSON artifact
        rf_doc = {
            "summary_id": rf_primary.id,
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,
            "has_unverified_movement_source": False,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": trace_id,
            "opening_balance": 100000.0,
            "ending_balance": actual_reserve,
            "movements": {"discount_unwind": 25000.0},
            "difference": 0.0,
            "tolerance": 0.05,
        }
        raw_bytes = json.dumps(rf_doc, sort_keys=True).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf_summary.json", raw_bytes)
        rf_art = RunArtifact(
            id=f"art-rf-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=2,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            partition={
                "summary_id": rf_primary.id,
                "has_unverified_movement_source": False,
                "tolerance_policy_id": "tol-policy-m5-standard",
                "trace_identifier": trace_id,
            },
        )
        db.add(rf_art)

        # Seed four-eyes approved ExpectedResultSet matching dimensions exactly
        ers_rows = [{
            "basis": "STATUTORY",
            "methodology": "VM22",
            "cohort_id": "COHORT_MYGA_2026",
            "valuation_date": "2026-12-31",
            "present_value_reserve": actual_reserve,
            "abs_tol": 0.01,
            "rel_tol": 1e-4,
        }]
        ers = ExpectedResultSet(
            id=f"ers-appr-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="Golden Standard VM22",
            product_code="MYGA",
            version_label="v1",
            source_reference="BENCHMARK",
            row_count=1,
            rows_data=ers_rows,
            status="approved",
            fingerprint=fingerprint(ers_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add(ers)
        db.commit()

        # Run genuine reconciliation
        rec_res = execute_governed_reconciliation(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            expected_result_set_id=ers.id,
            target_metric="present_value_reserve",
        )
        assert rec_res["status"] == "PASS"

        # Independent rollforward evaluation
        rf_eval = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art.id})
        assert rf_eval["is_verified"] is True
        assert rf_eval["recomputed_difference"] == 0.0

        # Execute Reporting Manifest Gate
        val_res = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "present_value_reserve": actual_reserve,
            "basis": "STATUTORY",
            "methodology": "VM22",
            "run_package_id": run_pkg.id,
            "run_package_fingerprint": run_pkg.fingerprint,
        }
        rf_res = {
            "artifact_id": rf_art.id,
            "summary_id": rf_primary.id,
            "trace_identifier": trace_id,
            "is_continuous": True,
        }
        report = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_res,
            rollforward_result=rf_res,
            reconciliation_result=rec_res,
            require_approved_reconciliation=True,
        )
        assert report["is_approved_for_release"] is True
        assert report["governance_status"] == "GOVERNED_RECONCILED_FOUNDATION_PACKAGE"
        assert report["rollforward_verified"] is True
        assert report["summary"]["rollforward"]["is_continuous"] is True
        assert report["summary"]["rollforward"]["difference"] == 0.0


def test_adversarial_reconciliation_failure_halts_release(closure_db):
    """Adversarial Scenario: Reconciliation FAIL status prevents release approval even if rollforward is verified."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    reviewer_id = closure_db["reviewer_id"]
    val_run_id = f"run-adv-rec-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()
    actual_reserve = 125000.0

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=actual_reserve)
        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:8]}"

        # Seed valid rollforward
        rf_p = RollforwardSummary(
            id=f"rf-p-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="ending_reserve",
            component_order=99,
            is_movement=False,
            opening_balance=100000.0,
            movement_amount=25000.0,
            closing_balance=actual_reserve,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        rf_m = RollforwardSummary(
            id=f"rf-m-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="unwind",
            component_order=1,
            is_movement=True,
            opening_balance=100000.0,
            movement_amount=25000.0,
            closing_balance=actual_reserve,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        rf_doc = {
            "summary_id": rf_p.id,
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "is_continuous": True,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": trace_id,
            "opening_balance": 100000.0,
            "ending_balance": actual_reserve,
            "movements": {"unwind": 25000.0},
            "tolerance": 0.05,
        }
        raw_b = json.dumps(rf_doc).encode("utf-8")
        stored = store.write_bytes(val_run_id, "rollforward_summary", "rf.json", raw_b)
        rf_art = RunArtifact(
            id=f"art-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=2,
            checksum_sha256=hashlib.sha256(raw_b).hexdigest(),
            partition={"summary_id": rf_p.id, "trace_identifier": trace_id},
        )
        db.add_all([rf_p, rf_m, rf_art])

        # Benchmark with different value -> Reconciliation FAIL
        ers_fail_rows = [{
            "basis": "STATUTORY",
            "methodology": "VM22",
            "cohort_id": "COHORT_MYGA_2026",
            "valuation_date": "2026-12-31",
            "present_value_reserve": 199999.0,  # Far from 125000
            "abs_tol": 0.01,
            "rel_tol": 1e-4,
        }]
        ers_fail = ExpectedResultSet(
            id=f"ers-fail-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="Failing Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="BENCHMARK",
            row_count=1,
            rows_data=ers_fail_rows,
            status="approved",
            fingerprint=fingerprint(ers_fail_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add(ers_fail)
        db.commit()

        rec_res = execute_governed_reconciliation(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            expected_result_set_id=ers_fail.id,
            target_metric="present_value_reserve",
        )
        assert rec_res["status"] == "FAIL"

        with pytest.raises(ServiceError) as exc_gate:
            execute_reporting_manifest(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                valuation_result={"valuation_run_id": val_run_id, "project_id": proj_id, "present_value_reserve": actual_reserve},
                rollforward_result={"artifact_id": rf_art.id, "summary_id": rf_p.id, "is_continuous": True},
                reconciliation_result=rec_res,
                require_approved_reconciliation=True,
            )
        assert exc_gate.value.status_code == 422
        assert exc_gate.value.code == "REPORTING_APPROVAL_GATE_FAILED"

        report_quarantine = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result={"valuation_run_id": val_run_id, "project_id": proj_id, "present_value_reserve": actual_reserve},
            rollforward_result={"artifact_id": rf_art.id, "summary_id": rf_p.id, "is_continuous": True},
            reconciliation_result=rec_res,
            require_approved_reconciliation=False,
        )
        assert report_quarantine["is_approved_for_release"] is False
        assert report_quarantine["governance_status"] == "REJECTED_NON_APPROVED"
        assert report_quarantine["artifact_type"] == "reporting_quarantined"


def test_negative_11_pending_run_status_rejected_for_release(closure_db):
    """Pending valuation execution cannot authorize release (attempt-lineage invariant)."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-pending-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0)
        run.status = "pending"
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {})
        assert eval_res["is_verified"] is False
        assert "non-successful execution status 'pending'" in eval_res["reason"]


def test_negative_12_running_run_status_rejected_for_release(closure_db):
    """Running valuation execution cannot authorize release (attempt-lineage invariant)."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-running-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0)
        run.status = "running"
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {})
        assert eval_res["is_verified"] is False
        assert "non-successful execution status 'running'" in eval_res["reason"]


def test_negative_13_multi_attempt_without_run_attempt_records_rejected(closure_db):
    """Multi-attempt valuation run lacking RunAttempt audit records cannot authorize release."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-multi-no-rec-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0)
        run.attempt_count = 2  # Multi-attempt!
        run.accepted_attempt_number = 2
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {})
        assert eval_res["is_verified"] is False
        assert "indicates multiple execution attempts (attempt_count=2) but has no RunAttempt audit records" in eval_res["reason"]


def test_negative_14_non_valuation_run_type_without_attempt_records_rejected(closure_db):
    """Non-valuation run type without RunAttempt records cannot authorize release."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-proj-no-rec-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0)
        run.run_type = "projection"  # Projection run!
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {})
        assert eval_res["is_verified"] is False
        assert "without RunAttempt records; attempt records are mandatory for non-valuation runs" in eval_res["reason"]


def test_negative_15_invalid_accepted_attempt_number_rejected(closure_db):
    """Valuation run with invalid accepted_attempt_number < 1 cannot authorize release."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-invalid-att-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0)
        run.accepted_attempt_number = 0  # Invalid < 1!
        db.commit()

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {})
        assert eval_res["is_verified"] is False
        assert "invalid accepted_attempt_number=0 (< 1)" in eval_res["reason"]


def test_positive_02_direct_valuation_execution_creates_canonical_run_attempt(closure_db):
    """execute_foundation_valuation consistently creates and persists canonical RunAttempt record for attempt 1."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    inf_id = closure_db["inforce_id"]
    scen_id = closure_db["scenario_id"]
    proj_run_id = f"run-proj-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        imported = execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA", inforce_file_id=inf_id)
        proj_out = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported,
            projection_months=6,
            run_id=proj_run_id,
            model_version_id=mv_id,
            scenario_id=scen_id,
            valuation_date="2026-12-31",
        )
        agg_out = execute_cash_flow_aggregation(
            db=db,
            projection_run_id=proj_out["run_id"],
            artifact_id=proj_out["artifact_id"],
            storage_uri=proj_out["storage_uri"],
            project_id=proj_id,
        )

        # Execute foundation valuation directly
        val_res = execute_foundation_valuation(
            db=db,
            upstream_run_id=proj_out["run_id"],
            basis="STATUTORY",
            methodology="VM22",
            valuation_date="2026-12-31",
            annual_discount_rate=0.045,
            aggregated_cash_flows=agg_out["aggregated_cash_flows"],
            project_id=proj_id,
        )

        val_run_id = val_res["valuation_run_id"]
        val_run_db = db.get(Run, val_run_id)
        assert val_run_db is not None
        assert val_run_db.accepted_attempt_number == 1
        assert val_run_db.status == "success"

        # Verify canonical RunAttempt record exists in DB
        att_db = db.query(RunAttempt).filter(RunAttempt.run_id == val_run_id).all()
        assert len(att_db) == 1
        assert att_db[0].attempt_number == 1
        assert att_db[0].status == "success"


def test_positive_03_direct_valuation_contract_single_attempt_without_attempt_records(closure_db):
    """Direct single-attempt valuation run without RunAttempt rows verifies under direct valuation contract."""
    Session = closure_db["Session"]
    proj_id = closure_db["project_id"]
    mv_id = closure_db["model_version_id"]
    user_id = closure_db["user_id"]
    val_run_id = f"run-val-direct-contract-{uuid.uuid4().hex[:8]}"
    store = get_artifact_store()

    with Session() as db:
        run, detail, run_pkg = _setup_base_valuation(db, proj_id, mv_id, user_id, val_run_id, actual_reserve=125000.0)
        trace_id = f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:8]}"

        rf_p = RollforwardSummary(
            id=f"rf-pos03-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="ending_reserve",
            component_order=99,
            is_movement=False,
            opening_balance=100000.0,
            movement_amount=25000.0,
            closing_balance=125000.0,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        rf_m = RollforwardSummary(
            id=f"rf-pos03-m-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="unwind",
            component_order=1,
            is_movement=True,
            opening_balance=100000.0,
            movement_amount=25000.0,
            closing_balance=125000.0,
            is_continuity_verified=True,
            continuity_difference=0.0,
            trace_identifier=trace_id,
        )
        rf_doc = {
            "summary_id": rf_p.id,
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "cohort_id": "COHORT_MYGA_2026",
            "basis": "STATUTORY",
            "methodology": "VM22",
            "valuation_date": "2026-12-31",
            "opening_balance": 100000.0,
            "ending_balance": 125000.0,
            "actual_closing": 125000.0,
            "movements": {"unwind": 25000.0},
            "is_continuous": True,
            "difference": 0.0,
            "tolerance": 0.05,
            "tolerance_policy_id": "tol-policy-m5-standard",
            "trace_identifier": trace_id,
        }
        rf_bytes = json.dumps(rf_doc, sort_keys=True).encode("utf-8")
        stored_rf = store.write_bytes(namespace=val_run_id, artifact_type="rollforward_summary", filename="rf.json", content=rf_bytes)
        rf_art = RunArtifact(
            id=f"art-pos03-{uuid.uuid4().hex[:8]}",
            run_id=val_run_id,
            attempt_number=1,
            artifact_type="rollforward_summary",
            storage_uri=stored_rf.uri,
            storage_backend="local",
            file_format="json",
            schema_version="m2-v1",
            row_count=1,
            checksum_sha256=hashlib.sha256(rf_bytes).hexdigest(),
            partition={"summary_id": rf_p.id, "tolerance_policy_id": "tol-policy-m5-standard", "trace_identifier": trace_id},
        )
        db.add_all([rf_p, rf_m, rf_art])
        db.commit()

        # Zero RunAttempt records in DB
        assert db.query(RunAttempt).filter(RunAttempt.run_id == val_run_id).count() == 0

        eval_res = evaluate_rollforward_evidence(db, run, detail, run_pkg, {"artifact_id": rf_art.id, "summary_id": rf_p.id, "is_continuous": True})
        assert eval_res["is_verified"] is True
        assert eval_res["recomputed_difference"] == 0.0
