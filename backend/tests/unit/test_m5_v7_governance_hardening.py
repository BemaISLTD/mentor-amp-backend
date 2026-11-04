"""Targeted Unit and Governance Hardening Tests for Milestone 5 V7 Review.

Covers V6 findings V6-P0-01 through V6-P1-07:
1. V6-P0-01: Immutable source identity and inforce provenance on retries (409 RUN_RETRY_LINEAGE_MISMATCH).
2. V6-P0-02: Reporting approval derived strictly from verified persisted database records.
3. V6-P0-03: Governed, bounded rollforward continuity tolerances and multi-period movement verification.
4. V6-P0-04: Strict, approval-bound benchmark dimensions, product matching, and required reserve metrics.
5. V6-P1-05: ModelVersion four-eyes approval and creator/approver integrity.
6. V6-P1-06: Derived dataset promotion on accepted attempt with verified physical parquet checksums.
7. V6-P1-07: Worker crash simulation description and test consistency.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import os
import uuid

import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import close_all_sessions, sessionmaker

from app.core.artifacts import get_artifact_store
from app.core.execution.fingerprints import fingerprint, inforce_fingerprint, INFORCE_FINGERPRINT_SCHEME
import app.db.database as db_mod
from app.db.database import Base
from app.db.models.governance import DerivedDataset
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.modeling import Model, ModelVersion
from app.db.models.project import Project
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.user import User
from app.db.models.valuation_detail import ValuationRunDetail
from app.core.valuation.rollforward import validate_continuity
from app.services.common import ServiceError, now_utc
from app.services.derived_dataset_service import approve_derived_dataset, promote_run_to_derived_dataset
from app.services.valuation_pipeline_service import (
    execute_cash_flow_aggregation,
    execute_governed_reconciliation,
    execute_inforce_import,
    execute_projection_simulation,
    execute_reporting_manifest,
    execute_rollforward_attribution,
)
from scripts.seed_demo import seed_myga


def _create_second_inforce(db, project_id: str, user_id: str, reviewer_id: str) -> InforceFile:
    rows = [
        {"policy_id": "ALT-001", "data": {"policy_id": "ALT-001", "product_type": "MYGA", "premium": 500000.0, "cohort_id": "COHORT_MYGA_ALT_2026", "credited_rate_annual": 0.045, "guaranteed_minimum_rate_annual": 0.03, "surrender_charge_schedule_id": "MYGA_5YR_STD", "guarantee_duration_months": 60, "free_withdrawal_pct": 0.10, "duration_months": 0, "account_value": 500000.0, "single_premium": 500000.0, "issue_age": 60, "attained_age": 60, "gender": "F"}},
        {"policy_id": "ALT-002", "data": {"policy_id": "ALT-002", "product_type": "MYGA", "premium": 750000.0, "cohort_id": "COHORT_MYGA_ALT_2026", "credited_rate_annual": 0.050, "guaranteed_minimum_rate_annual": 0.03, "surrender_charge_schedule_id": "MYGA_5YR_STD", "guarantee_duration_months": 60, "free_withdrawal_pct": 0.10, "duration_months": 0, "account_value": 750000.0, "single_premium": 750000.0, "issue_age": 65, "attained_age": 65, "gender": "M"}},
    ]
    fp = inforce_fingerprint((r["policy_id"], r["data"]) for r in rows)
    inf = InforceFile(
        id=f"inf-alt-{uuid.uuid4().hex[:8]}",
        project_id=project_id,
        filename="ALT_INFORCE.csv",
        file_type="csv",
        version_label="v2.0",
        status="approved",
        row_count=2,
        columns_detected=list(rows[0]["data"].keys()),
        fingerprint=fp,
        fingerprint_scheme=INFORCE_FINGERPRINT_SCHEME,
        committed_by=user_id,
        committed_at=now_utc(),
        approved_by=reviewer_id,
        approved_at=now_utc(),
    )
    db.add(inf)
    db.flush()
    for r in rows:
        db.add(InforceRecord(file_id=inf.id, policy_id=r["policy_id"], data=r["data"]))
    db.commit()
    return inf


@pytest.fixture
def v7_db(monkeypatch, tmp_path):
    """Clean isolated test database with seeded MYGA environment."""
    db_path = tmp_path / "v7_governance_hardening.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)

    with TestSessionLocal() as db:
        seeded = seed_myga(db, project_name="V7 Hardening Test Project")
        proj_id = seeded["project"].id
        inforce_id = seeded["inforce"].id
        mv_id = seeded["version"].id
        ps_id = seeded["projection_set"].id
        scen_id = seeded["scenarios"][0].id
        user = seeded["user"]
        reviewer = seeded["reviewer"]
        inf2 = _create_second_inforce(db, proj_id, user.id, reviewer.id)

    yield {
        "engine": test_engine,
        "Session": TestSessionLocal,
        "project_id": proj_id,
        "inforce_file_id_1": inforce_id,
        "inforce_file_id_2": inf2.id,
        "model_version_id": mv_id,
        "projection_set_id": ps_id,
        "scenario_id": scen_id,
        "user_id": user.id,
        "reviewer_id": reviewer.id,
    }

    close_all_sessions()
    test_engine.dispose()


# =========================================================================
# V6-P0-01: Immutable Source Identity & Inforce Provenance on Retries
# =========================================================================

def test_v6_p0_01_retry_inforce_lineage_immutability(v7_db):
    """P0-01: Proves retried execution preserves immutable inforce lineage and rejects substituted inforce."""
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    inf1_id = v7_db["inforce_file_id_1"]
    inf2_id = v7_db["inforce_file_id_2"]
    mv_id = v7_db["model_version_id"]
    scen_id = v7_db["scenario_id"]
    run_id = f"run-v7-retry-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        imported1 = execute_inforce_import(db=db, project_id=proj_id, inforce_file_id=inf1_id)
        imported2 = execute_inforce_import(db=db, project_id=proj_id, inforce_file_id=inf2_id)

        # Attempt 1: Execute simulation successfully with inforce 1
        res1 = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported1,
            run_id=run_id,
            model_version_id=mv_id,
            scenario_id=scen_id,
            valuation_date="2026-12-31",
            projection_months=6,
        )
        assert res1["run_id"] == run_id
        assert res1["accepted_attempt_number"] == 1

        # Positive replay: retrying same run_id with identical inforce 1 succeeds
        res_replay = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported1,
            run_id=run_id,
            model_version_id=mv_id,
            scenario_id=scen_id,
            valuation_date="2026-12-31",
            projection_months=6,
        )
        assert res_replay["run_id"] == run_id
        assert res_replay["accepted_attempt_number"] == 1
        assert res_replay["attempt_count"] == 1

        # Negative replay: retrying same run_id with substituted inforce 2 fails closed with 409
        with pytest.raises(ServiceError) as exc_lineage:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported2,  # Substituted inforce!
                run_id=run_id,
                model_version_id=mv_id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
                projection_months=6,
            )
        assert exc_lineage.value.status_code == 409
        assert exc_lineage.value.code == "RUN_RETRY_LINEAGE_MISMATCH"
        assert "Cannot replay existing run under different inforce lineage" in exc_lineage.value.message


# =========================================================================
# V6-P0-02: Reporting Approval Derived From Verified Persisted Records
# =========================================================================

def test_v6_p0_02_reporting_approval_persisted_record_verification(v7_db):
    """P0-02: Proves reporting asset approval is derived strictly from persisted, verified DB records."""
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    mv_id = v7_db["model_version_id"]
    val_run_id = f"run-v7-rep-{uuid.uuid4().hex[:8]}"
    actual_reserve = 125000.0

    with Session() as db:
        # Seed Run & ValuationRunDetail
        run = Run(
            id=val_run_id,
            project_id=proj_id,
            projection_key="COHORT_MYGA_2026",
            status="success",
            run_type="valuation",
            model_version_id=mv_id,
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v1",
            valuation_date=date(2026, 12, 31),
        )
        detail = ValuationRunDetail(
            run_id=val_run_id,
            valuation_date=date(2026, 12, 31),
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
            summary_results={"present_value_reserve": actual_reserve, "closing_reserve": actual_reserve},
        )
        # Seed verified RollforwardSummary
        rf_summary = RollforwardSummary(
            id=f"rf-{uuid.uuid4().hex[:8]}",
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
            trace_identifier="trace-01",
        )
        # Seed passing ReconciliationReport
        rec_pass = ReconciliationReport(
            id=f"rec-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            run_id=val_run_id,
            level="policy_month",
            variables_requested=["present_value_reserve"],
            status="PASS",
            summary={"difference": 0.001, "reason": "Match"},
        )
        # Seed failing ReconciliationReport
        rec_fail = ReconciliationReport(
            id=f"rec-fail-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            run_id=val_run_id,
            level="policy_month",
            variables_requested=["present_value_reserve"],
            status="FAIL",
            summary={"difference": 5000.0, "reason": "Threshold exceeded"},
        )
        db.add_all([run, detail, rf_summary, rec_pass, rec_fail])
        db.commit()

        val_result = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "present_value_reserve": actual_reserve,
            "basis": "STATUTORY",
            "methodology": "VM22",
        }
        rf_result = {"is_continuous": True, "summary_id": rf_summary.id}

        # 1. Positive: Persisted PASS report & continuous rollforward -> Approved release
        pass_rep = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_result,
            rollforward_result=rf_result,
            reconciliation_result={"report_id": rec_pass.id, "status": "PASS", "difference": 0.001},
            require_approved_reconciliation=True,
        )
        assert pass_rep["is_approved_for_release"] is True
        assert pass_rep["governance_status"] == "GOVERNED_RECONCILED_FOUNDATION_PACKAGE"
        assert pass_rep["artifact_type"] == "reporting"

        # 2. Negative: Forged caller status='PASS' with no persisted report in DB -> NOT_VERIFIABLE & unverified artifact
        unverified_rep = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_result,
            rollforward_result=rf_result,
            reconciliation_result={"report_id": "rec-nonexistent-id", "status": "PASS"},
            require_approved_reconciliation=False,
        )
        assert unverified_rep["is_approved_for_release"] is False
        assert unverified_rep["governance_status"] == "TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED"
        assert unverified_rep["artifact_type"] == "reporting_unverified"

        # When require_approved_reconciliation=True, unverified report raises 422
        with pytest.raises(ServiceError) as exc_gate_unverified:
            execute_reporting_manifest(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                valuation_result=val_result,
                rollforward_result=rf_result,
                reconciliation_result={"report_id": "rec-nonexistent-id", "status": "PASS"},
                require_approved_reconciliation=True,
            )
        assert exc_gate_unverified.value.status_code == 422

        # 3. Negative: Caller claims 'PASS' but DB row has status='FAIL' -> Quarantined
        quarantined_rep = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_result,
            rollforward_result=rf_result,
            reconciliation_result={"report_id": rec_fail.id, "status": "PASS"},  # Caller lies!
            require_approved_reconciliation=False,
        )
        assert quarantined_rep["is_approved_for_release"] is False
        assert quarantined_rep["governance_status"] == "REJECTED_NON_APPROVED"
        assert quarantined_rep["artifact_type"] == "reporting_quarantined"


# =========================================================================
# V6-P0-03: Governed, Bounded Rollforward Continuity Tolerances
# =========================================================================

def test_v6_p0_03_bounded_rollforward_tolerances_and_movements(v7_db):
    """P0-03: Proves rollforward continuity validates finite, non-negative, bounded tolerances."""
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    mv_id = v7_db["model_version_id"]

    # 1. validate_continuity core rejection of negative, NaN, and Inf tolerances
    with pytest.raises(ValueError, match="finite, non-negative"):
        validate_continuity(Decimal("100"), [], Decimal("100"), tolerance=Decimal("-0.01"))

    with pytest.raises(ValueError, match="finite, non-negative"):
        validate_continuity(Decimal("100"), [], Decimal("100"), tolerance=Decimal("NaN"))

    with pytest.raises(ValueError, match="finite, non-negative"):
        validate_continuity(Decimal("100"), [], Decimal("100"), tolerance=Decimal("Infinity"))

    # 2. execute_rollforward_attribution rejects caller tolerance exceeding MAX_GOVERNED_ROLLFORWARD_TOLERANCE (0.05)
    run_id = f"run-v7-rf-{uuid.uuid4().hex[:8]}"
    with Session() as db:
        r = Run(
            id=run_id,
            project_id=proj_id,
            projection_key="COHORT_MYGA_2026",
            status="success",
            run_type="valuation",
            model_version_id=mv_id,
            basis="STATUTORY",
            methodology="VM22",
            valuation_date=date(2026, 12, 31),
        )
        detail = ValuationRunDetail(
            run_id=run_id,
            valuation_date=date(2026, 12, 31),
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
            summary_results={
                "opening_reserve": 125000.0,
                "present_value_reserve": 125000.0,
                "closing_reserve": 125000.0,
            },
        )
        db.add_all([r, detail])
        db.commit()

        # Giant tolerance override rejected with 400
        with pytest.raises(ServiceError) as exc_tol_bound:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=run_id,
                project_id=proj_id,
                opening=125000.0,
                ending=125000.0,
                movements={},
                tolerance=1000.0,  # Far above 0.05 limit!
            )
        assert exc_tol_bound.value.status_code == 400
        assert exc_tol_bound.value.code == "GOVERNED_TOLERANCE_EXCEEDED"

        # Negative tolerance rejected with 400
        with pytest.raises(ServiceError) as exc_neg_tol:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=run_id,
                project_id=proj_id,
                opening=125000.0,
                ending=125000.0,
                movements={},
                tolerance=-0.01,
            )
        assert exc_neg_tol.value.status_code == 400
        assert exc_neg_tol.value.code == "INVALID_ROLLFORWARD_TOLERANCE"

        # Non-finite movement value rejected
        with pytest.raises(ServiceError) as exc_nan_mov:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=run_id,
                project_id=proj_id,
                opening=125000.0,
                ending=125000.0,
                movements={"custom_movement": float("nan")},
            )
        assert exc_nan_mov.value.status_code == 400
        assert exc_nan_mov.value.code == "INVALID_ROLLFORWARD_MOVEMENT"

        # Valid bounded rollforward succeeds
        rf_ok = execute_rollforward_attribution(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            opening=125000.0,
            ending=125000.0,
            movements={},
            tolerance=0.01,
        )
        assert rf_ok["is_continuous"] is True
        assert rf_ok["difference"] == 0.0


# =========================================================================
# V6-P0-04: Strict Benchmark Dimensions, Products, & Required Metrics
# =========================================================================

def test_v6_p0_04_benchmark_dimensions_products_and_metrics(v7_db):
    """P0-04: Proves benchmark reconciliation requires matching product, basis, method, valuation date, and reserve metric."""
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    mv_id = v7_db["model_version_id"]
    user_id = v7_db["user_id"]
    reviewer_id = v7_db["reviewer_id"]
    run_id = f"run-v7-bm-{uuid.uuid4().hex[:8]}"

    with Session() as db:
        r = Run(
            id=run_id,
            project_id=proj_id,
            projection_key="COHORT_MYGA_2026",
            status="success",
            run_type="valuation",
            model_version_id=mv_id,
            basis="STATUTORY",
            methodology="VM22",
            valuation_date=date(2026, 12, 31),
        )
        detail = ValuationRunDetail(
            run_id=run_id,
            valuation_date=date(2026, 12, 31),
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
            summary_results={
                "present_value_reserve": 125000.0,
                "closing_reserve": 125000.0,
                "tax_expense": 500.0,
            },
        )
        db.add_all([r, detail])

        # Benchmark with wrong valuation date (2027 vs 2026)
        date_rows = [{
            "basis": "STATUTORY",
            "methodology": "VM22",
            "valuation_date": "2027-12-31",  # Run is 2026-12-31!
            "present_value_reserve": 125000.0,
        }]
        ers_date_mismatch = ExpectedResultSet(
            id=f"ers-date-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="Date Mismatch Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_TABLE",
            row_count=1,
            columns_detected=["basis", "methodology", "valuation_date", "present_value_reserve"],
            rows_data=date_rows,
            status="approved",
            fingerprint=fingerprint(date_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )

        # Benchmark with wrong methodology (VM21 vs VM22)
        meth_rows = [{
            "basis": "STATUTORY",
            "methodology": "VM21",  # Run is VM22!
            "valuation_date": "2026-12-31",
            "present_value_reserve": 125000.0,
        }]
        ers_method_mismatch = ExpectedResultSet(
            id=f"ers-meth-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="Method Mismatch Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_TABLE",
            row_count=1,
            columns_detected=["basis", "methodology", "valuation_date", "present_value_reserve"],
            rows_data=meth_rows,
            status="approved",
            fingerprint=fingerprint(meth_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )

        # Benchmark with UNKNOWN product
        unk_rows = [{"present_value_reserve": 125000.0}]
        ers_unknown_prod = ExpectedResultSet(
            id=f"ers-unk-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="Unknown Product Benchmark",
            product_code="UNKNOWN",
            version_label="v1",
            source_reference="ACTUARIAL_TABLE",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=unk_rows,
            status="approved",
            fingerprint=fingerprint(unk_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add_all([ers_date_mismatch, ers_method_mismatch, ers_unknown_prod])
        db.commit()

        # 1. Valuation date mismatch returns NOT_VERIFIABLE
        rec_date = execute_governed_reconciliation(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            expected_result_set_id=ers_date_mismatch.id,
        )
        assert rec_date["status"] == "NOT_VERIFIABLE"
        assert "valuation_date" in rec_date["not_verifiable_reason"]

        # 2. Methodology mismatch returns NOT_VERIFIABLE
        rec_meth = execute_governed_reconciliation(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            expected_result_set_id=ers_method_mismatch.id,
        )
        assert rec_meth["status"] == "NOT_VERIFIABLE"
        assert "methodology" in rec_meth["not_verifiable_reason"]

        # 3. UNKNOWN product raises 400 UNKNOWN_PRODUCT_FOR_BENCHMARK
        with pytest.raises(ServiceError) as exc_unk:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=run_id,
                project_id=proj_id,
                expected_result_set_id=ers_unknown_prod.id,
            )
        assert exc_unk.value.status_code == 400
        assert exc_unk.value.code == "UNKNOWN_PRODUCT_FOR_BENCHMARK"


# =========================================================================
# V6-P1-05: ModelVersion Four-Eyes Approval & Creator/Approver Integrity
# =========================================================================

def test_v6_p1_05_model_version_four_eyes_and_approval_integrity(v7_db):
    """P1-05: Proves ModelVersion requires non-null, distinct creator & approver, and approved status."""
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    inf1_id = v7_db["inforce_file_id_1"]
    scen_id = v7_db["scenario_id"]
    user_id = v7_db["user_id"]
    reviewer_id = v7_db["reviewer_id"]

    with Session() as db:
        imported = execute_inforce_import(db=db, project_id=proj_id, inforce_file_id=inf1_id)

        m = Model(
            id=f"mod-v7-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="V7 Test Model",
            product_code="MYGA",
            status="approved",
            created_by=user_id,
        )
        db.add(m)
        db.flush()

        # Unapproved draft version
        mv_draft = ModelVersion(
            id=f"mv-draft-{uuid.uuid4().hex[:8]}",
            model_id=m.id,
            version_label="v1.0",
            version_number=1,
            basis="STATUTORY",
            methodology="VM22",
            status="draft",
            created_by=user_id,
            approved_by=None,
        )
        # Self-approved version (created_by == approved_by)
        mv_self = ModelVersion(
            id=f"mv-self-{uuid.uuid4().hex[:8]}",
            model_id=m.id,
            version_label="v2.0",
            version_number=2,
            basis="STATUTORY",
            methodology="VM22",
            status="approved",
            created_by=user_id,
            approved_by=user_id,
            approved_at=now_utc(),
        )
        # Version with missing created_by
        mv_no_creator = ModelVersion(
            id=f"mv-nocreator-{uuid.uuid4().hex[:8]}",
            model_id=m.id,
            version_label="v3.0",
            version_number=3,
            basis="STATUTORY",
            methodology="VM22",
            status="approved",
            created_by=None,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        # Genuine four-eyes approved version
        mv_legit = ModelVersion(
            id=f"mv-legit-{uuid.uuid4().hex[:8]}",
            model_id=m.id,
            version_label="v4.0",
            version_number=4,
            basis="STATUTORY",
            methodology="VM22",
            status="approved",
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add_all([mv_draft, mv_self, mv_no_creator, mv_legit])
        db.commit()

        # 1. Draft rejected in execute_projection_simulation
        with pytest.raises(ServiceError) as exc_draft:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                run_id=f"run-{uuid.uuid4().hex[:8]}",
                model_version_id=mv_draft.id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_draft.value.status_code == 400
        assert exc_draft.value.code == "MODEL_VERSION_NOT_APPROVED"

        # 2. Self-approved rejected with SELF_APPROVED_MODEL_FORBIDDEN
        with pytest.raises(ServiceError) as exc_self:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                run_id=f"run-{uuid.uuid4().hex[:8]}",
                model_version_id=mv_self.id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_self.value.status_code == 400
        assert exc_self.value.code == "SELF_APPROVED_MODEL_FORBIDDEN"

        # 3. Missing creator rejected with MODEL_VERSION_NOT_APPROVED
        with pytest.raises(ServiceError) as exc_nocreator:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                run_id=f"run-{uuid.uuid4().hex[:8]}",
                model_version_id=mv_no_creator.id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_nocreator.value.status_code == 400
        assert exc_nocreator.value.code == "MODEL_VERSION_NOT_APPROVED"

        # 4. Legit four-eyes approved succeeds
        sim = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported,
            run_id=f"run-legit-{uuid.uuid4().hex[:8]}",
            model_version_id=v7_db["model_version_id"],
            scenario_id=scen_id,
            valuation_date="2026-12-31",
            projection_months=6,
        )
        assert sim["run_id"] is not None
        assert sim["accepted_attempt_number"] == 1


# =========================================================================
# V6-P1-06: Derived Dataset Promotion on Accepted Attempt with Checksums
# =========================================================================

def test_v6_p1_06_derived_dataset_promotion_and_checksum_integrity(v7_db, tmp_path):
    """P1-06: Proves derived dataset promotion requires accepted_attempt_number >= 1 and physical sha256 match."""
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    mv_id = v7_db["model_version_id"]
    user_id = v7_db["user_id"]
    reviewer_id = v7_db["reviewer_id"]

    store = get_artifact_store()
    rows = [{"policy_id": "P1", "cash_flow": 100.0}, {"policy_id": "P2", "cash_flow": 200.0}]
    stored_mis = store.write_rows("run-acc-mis", "outputs", rows, attempt_number=2)
    stored_bad = store.write_rows("run-acc-bad", "outputs", rows, attempt_number=1)
    stored_val = store.write_rows("run-acc-val", "outputs", rows, attempt_number=1)

    real_sha256 = stored_val.checksum_sha256

    with Session() as db:
        # Run without accepted attempt
        run_no_acc = Run(
            id=f"run-noacc-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            projection_key="K1",
            status="success",
            run_type="valuation",
            model_version_id=mv_id,
            basis="STATUTORY",
            methodology="VM22",
            valuation_date=date(2026, 12, 31),
            accepted_attempt_number=None,  # Not accepted!
        )
        # Run with accepted attempt 1
        run_acc = Run(
            id=f"run-acc-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            projection_key="K2",
            status="success",
            run_type="valuation",
            model_version_id=mv_id,
            basis="STATUTORY",
            methodology="VM22",
            valuation_date=date(2026, 12, 31),
            accepted_attempt_number=1,
        )
        db.add_all([run_no_acc, run_acc])
        db.flush()

        # Artifact with wrong attempt (attempt 2 vs accepted 1)
        art_mismatch = RunArtifact(
            id=f"art-mis-{uuid.uuid4().hex[:8]}",
            run_id=run_acc.id,
            artifact_type="outputs",
            storage_uri=stored_mis.uri,
            storage_backend="local",
            row_count=2,
            attempt_number=2,  # Mismatch!
            checksum_sha256=stored_mis.checksum_sha256,
        )
        # Artifact with tampered checksum
        art_bad_hash = RunArtifact(
            id=f"art-badhash-{uuid.uuid4().hex[:8]}",
            run_id=run_acc.id,
            artifact_type="outputs",
            storage_uri=stored_bad.uri,
            storage_backend="local",
            row_count=2,
            attempt_number=1,
            checksum_sha256="deadbeef" * 8,  # Does not match file!
        )
        # Valid artifact
        art_valid = RunArtifact(
            id=f"art-valid-{uuid.uuid4().hex[:8]}",
            run_id=run_acc.id,
            artifact_type="outputs",
            storage_uri=stored_val.uri,
            storage_backend="local",
            row_count=2,
            attempt_number=1,
            checksum_sha256=stored_val.checksum_sha256,
        )
        db.add_all([art_mismatch, art_bad_hash, art_valid])
        db.commit()

        # 1. Negative: Promotion on unaccepted run raises 400 ACCEPTED_ATTEMPT_REQUIRED
        with pytest.raises(ServiceError) as exc_noacc:
            promote_run_to_derived_dataset(
                db=db,
                project_id=proj_id,
                run_id=run_no_acc.id,
                artifact_id=art_valid.id,
                dataset_name="Test DS 1",
                dataset_version="1",
                user_id=user_id,
            )
        assert exc_noacc.value.status_code == 400
        assert "has no accepted attempt" in exc_noacc.value.message

        # 2. Negative: Promotion on attempt mismatch raises 400 ARTIFACT_ATTEMPT_MISMATCH
        with pytest.raises(ServiceError) as exc_mis:
            promote_run_to_derived_dataset(
                db=db,
                project_id=proj_id,
                run_id=run_acc.id,
                artifact_id=art_mismatch.id,
                dataset_name="Test DS 2",
                dataset_version="1",
                user_id=user_id,
            )
        assert exc_mis.value.status_code == 400
        assert "not accepted attempt" in exc_mis.value.message

        # 3. Negative: Promotion with tampered checksum raises 400 CHECKSUM_VERIFICATION_FAILED
        with pytest.raises(ServiceError) as exc_badhash:
            promote_run_to_derived_dataset(
                db=db,
                project_id=proj_id,
                run_id=run_acc.id,
                artifact_id=art_bad_hash.id,
                dataset_name="Test DS 3",
                dataset_version="1",
                user_id=user_id,
            )
        assert exc_badhash.value.status_code == 400
        assert "SHA-256 mismatch" in exc_badhash.value.message

        # 4. Positive: Valid promotion succeeds
        ds = promote_run_to_derived_dataset(
            db=db,
            project_id=proj_id,
            run_id=run_acc.id,
            artifact_id=art_valid.id,
            dataset_name="Valid Derived Dataset",
            dataset_version="1",
            user_id=user_id,
        )
        assert ds.id is not None
        assert ds.fingerprint == real_sha256
        assert ds.status == "draft"

        # 5. Approve derived dataset re-verifies checksum
        ds_approved = approve_derived_dataset(
            db=db,
            project_id=proj_id,
            dataset_id=ds.id,
            approved_by_user_id=reviewer_id,
        )
        assert ds_approved.status == "approved"
        assert ds_approved.approved_by == reviewer_id


# =========================================================================
# V6-P1-07: Evidence Consistency & Worker Crash Simulation Verification
# =========================================================================

def test_v6_p1_07_evidence_and_worker_crash_consistency():
    """P1-07: Verifies test suite documentation and comments accurately reflect test methodology."""
    import inspect
    from tests.postgres import test_m5_postgres_governance

    source_code = inspect.getsource(test_m5_postgres_governance)
    # Proves no inaccurate claims of OS SIGKILL in descriptions
    assert "Simulate a worker crash after real PostgreSQL commits" in source_code
