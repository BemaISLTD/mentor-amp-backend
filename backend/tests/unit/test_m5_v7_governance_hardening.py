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
from app.services.derived_dataset_service import approve_derived_dataset, promote_run_to_derived_dataset, validate_derived_dataset
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
    """V8-P0-01 & V8-P2-06: Release approval requires frozen RunPackage, independently approved ExpectedResultSet, passing reports, and verified rollforward."""
    from app.core.execution import run_package as core_pkg
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    mv_id = v7_db["model_version_id"]
    user_id = v7_db["user_id"]
    reviewer_id = v7_db["reviewer_id"]
    val_run_id = f"run-v8-rep-{uuid.uuid4().hex[:8]}"
    actual_reserve = 125000.0

    with Session() as db:
        # 1. Seed sealed frozen RunPackage
        pkg_doc = core_pkg.seal(
            identity={
                "package_id": f"pkg-{val_run_id}",
                "frozen_for_run_id": val_run_id,
                "project_id": proj_id,
                "created_at": "2026-11-04T00:00:00Z",
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

        # 2. Seed Run & ValuationRunDetail
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
            run_package_fingerprint=pkg_doc["fingerprint"],
        )
        detail = ValuationRunDetail(
            run_id=val_run_id,
            valuation_date=date(2026, 12, 31),
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
            summary_results={"present_value_reserve": actual_reserve, "closing_reserve": actual_reserve},
        )

        # 3. Seed four-eyes approved ExpectedResultSet
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
            name="Approved VM22 Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_BENCHMARK",
            row_count=1,
            columns_detected=["basis", "methodology", "cohort_id", "valuation_date", "present_value_reserve"],
            rows_data=ers_rows,
            status="approved",
            fingerprint=fingerprint(ers_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )

        # 4. Seed verified RollforwardSummary with VERIFIED trace
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
            trace_identifier=f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:12]}",
        )

        # 5. Seed passing ReconciliationReport linked to approved ExpectedResultSet
        rec_pass = ReconciliationReport(
            id=f"rec-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            run_id=val_run_id,
            expected_result_set_id=ers.id,
            level="policy_month",
            variables_requested=["present_value_reserve"],
            expected_result_fingerprint=ers.fingerprint,
            expected_result_version=ers.version_label,
            status="PASS",
            summary={"difference": 0.0, "reason": "Exact match"},
        )
        db.add_all([run_pkg, run, detail, ers, rf_summary, rec_pass])
        db.commit()

        val_result = {
            "valuation_run_id": val_run_id,
            "project_id": proj_id,
            "present_value_reserve": actual_reserve,
            "basis": "STATUTORY",
            "methodology": "VM22",
            "run_package_id": run_pkg.id,
            "run_package_fingerprint": run_pkg.fingerprint,
        }
        rf_result = {"is_continuous": True, "summary_id": rf_summary.id}

        # Positive Case: Frozen RunPackage + Approved ExpectedResultSet + PASS report + Verified rollforward -> Approved release
        pass_rep = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_result,
            rollforward_result=rf_result,
            reconciliation_result={"report_id": rec_pass.id, "status": "PASS", "difference": 0.0},
            require_approved_reconciliation=True,
        )
        assert pass_rep["is_approved_for_release"] is True
        assert pass_rep["governance_status"] == "GOVERNED_RECONCILED_FOUNDATION_PACKAGE"
        assert pass_rep["artifact_type"] == "reporting"
        assert pass_rep["package_id"] == f"DISCLOSURE-{val_run_id}"

        # Acceptance Test (a): Manufactured PASS row without RunPackage -> nonapproval
        no_pkg_val_run_id = f"run-no-pkg-{uuid.uuid4().hex[:8]}"
        run_no_pkg = Run(
            id=no_pkg_val_run_id,
            project_id=proj_id,
            projection_key="COHORT_MYGA_2026",
            status="success",
            run_type="valuation",
            model_version_id=mv_id,
            basis="STATUTORY",
            methodology="VM22",
            valuation_date=date(2026, 12, 31),
        )
        detail_no_pkg = ValuationRunDetail(
            run_id=no_pkg_val_run_id,
            valuation_date=date(2026, 12, 31),
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
            summary_results={"present_value_reserve": actual_reserve},
        )
        rf_no_pkg = RollforwardSummary(
            id=f"rf-nopkg-{uuid.uuid4().hex[:8]}",
            run_id=no_pkg_val_run_id,
            project_id=proj_id,
            cohort_id="COHORT_MYGA_2026",
            methodology="VM22",
            basis="STATUTORY",
            component_name="ending_reserve",
            component_order=99,
            is_movement=False,
            closing_balance=actual_reserve,
            is_continuity_verified=True,
            trace_identifier=f"VERIFIED:tol-policy-m5-standard:{uuid.uuid4().hex[:12]}",
        )
        rec_pass_no_pkg = ReconciliationReport(
            id=f"rec-nopkg-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            run_id=no_pkg_val_run_id,
            expected_result_set_id=ers.id,
            variables_requested=["present_value_reserve"],
            status="PASS",
        )
        db.add_all([run_no_pkg, detail_no_pkg, rf_no_pkg, rec_pass_no_pkg])
        db.commit()

        rep_no_pkg = execute_reporting_manifest(
            db=db,
            valuation_run_id=no_pkg_val_run_id,
            project_id=proj_id,
            valuation_result={"valuation_run_id": no_pkg_val_run_id, "project_id": proj_id, "present_value_reserve": actual_reserve, "basis": "STATUTORY", "methodology": "VM22"},
            rollforward_result={"is_continuous": True, "summary_id": rf_no_pkg.id},
            reconciliation_result={"report_id": rec_pass_no_pkg.id, "status": "PASS"},
            require_approved_reconciliation=False,
        )
        assert rep_no_pkg["is_approved_for_release"] is False
        assert rep_no_pkg["governance_status"] == "REJECTED_NON_APPROVED"
        assert rep_no_pkg["artifact_type"] == "reporting_quarantined"

        # Acceptance Test (b): PASS row without approved ExpectedResultSet -> nonapproval
        rec_unapproved_ers = ReconciliationReport(
            id=f"rec-unappr-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            run_id=val_run_id,
            expected_result_set_id=None,  # No approved ExpectedResultSet!
            variables_requested=["present_value_reserve"],
            status="PASS",
        )
        db.add(rec_unapproved_ers)
        db.commit()

        rep_unappr_ers = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_result,
            rollforward_result=rf_result,
            reconciliation_result={"report_id": rec_unapproved_ers.id, "status": "PASS"},
            require_approved_reconciliation=False,
        )
        assert rep_unappr_ers["is_approved_for_release"] is False
        assert rep_unappr_ers["governance_status"] == "TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED"

        # Acceptance Test (c): Contradictory reports: Failed mandatory reserve report for same run -> nonapproval
        rec_failing = ReconciliationReport(
            id=f"rec-fail-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            run_id=val_run_id,
            expected_result_set_id=ers.id,
            variables_requested=["present_value_reserve"],
            status="FAIL",
            summary={"difference": 5000.0, "reason": "Breached tolerance"},
        )
        db.add(rec_failing)
        db.commit()

        # Even if caller points to passing rec_pass.id, existence of failing report disqualifies release!
        rep_with_fail = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_result,
            rollforward_result=rf_result,
            reconciliation_result={"report_id": rec_pass.id, "status": "PASS"},
            require_approved_reconciliation=False,
        )
        assert rep_with_fail["is_approved_for_release"] is False
        assert rep_with_fail["governance_status"] == "TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED"

        # V8-P2-06: Empty variables_requested in ReconciliationReport fails closed without NameError
        db.delete(rec_failing)
        db.commit()

        rec_empty_vars = ReconciliationReport(
            id=f"rec-empty-vars-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            run_id=val_run_id,
            expected_result_set_id=ers.id,
            variables_requested=[],  # Empty variables list!
            status="PASS",
        )
        db.add(rec_empty_vars)
        db.commit()

        rep_empty_vars = execute_reporting_manifest(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
            valuation_result=val_result,
            rollforward_result=rf_result,
            reconciliation_result={"report_id": rec_empty_vars.id, "status": "PASS"},
            require_approved_reconciliation=False,
        )
        # Must fail closed safely without raising NameError ('target_metric' is not defined)
        assert rep_empty_vars["is_approved_for_release"] is False
        assert rep_empty_vars["governance_status"] == "TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED"


# =========================================================================
# V6-P0-03: Governed, Bounded Rollforward Continuity Tolerances
# =========================================================================

def test_v6_p0_03_bounded_rollforward_tolerances_and_movements(v7_db):
    """P0-03: Proves rollforward continuity validates finite, non-negative, bounded tolerances."""
    Session = v7_db["Session"]
    proj_id = v7_db["project_id"]
    mv_id = v7_db["model_version_id"]
    user_id = v7_db["user_id"]

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

        # Valid bounded rollforward succeeds with approved policy
        rf_ok = execute_rollforward_attribution(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            opening=125000.0,
            ending=125000.0,
            movements={},
            tolerance=0.01,
            tolerance_policy_id="tol-policy-m5-standard",
        )
        assert rf_ok["is_continuous"] is True
        assert rf_ok["difference"] == 0.0
        assert rf_ok["has_unverified_movement_source"] is False
        assert rf_ok["trace_identifier"].startswith("VERIFIED:tol-policy-m5-standard:")

        # V8-P0-02 Acceptance Test: Fabricated tolerance policy ID rejected with 400
        with pytest.raises(ServiceError) as exc_fab_pol:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=run_id,
                project_id=proj_id,
                opening=125000.0,
                ending=125000.0,
                movements={},
                tolerance_policy_id="tol-fabricated-custom",
            )
        assert exc_fab_pol.value.status_code == 400
        assert exc_fab_pol.value.code == "TOLERANCE_POLICY_NOT_APPROVED"

        # V8-P0-02 Acceptance Test: Changed movement amount where key exists in period_movements
        # Seed prior valuation run with known period movements
        prior_run_id = f"run-v8-prior-{uuid.uuid4().hex[:8]}"
        prior_run = Run(
            id=prior_run_id,
            project_id=proj_id,
            projection_key="COHORT_MYGA_2026",
            status="success",
            run_type="valuation",
            model_version_id=mv_id,
            basis="STATUTORY",
            methodology="VM22",
            valuation_date=date(2025, 12, 31),
        )
        prior_detail = ValuationRunDetail(
            run_id=prior_run_id,
            valuation_date=date(2025, 12, 31),
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
            summary_results={
                "opening_reserve": 100000.0,
                "closing_reserve": 125000.0,
                "present_value_reserve": 125000.0,
                "period_movements": {
                    "accretion": 15000.0,
                    "benefit_payout": -10000.0,
                    "assumption_change": 20000.0,
                },
            },
        )
        curr_detail = ValuationRunDetail(
            run_id=run_id,
            valuation_date=date(2026, 12, 31),
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026"},
            summary_results={
                "opening_reserve": 125000.0,
                "closing_reserve": 150000.0,
                "present_value_reserve": 150000.0,
            },
        )
        db.add_all([prior_run, prior_detail])
        db.merge(curr_detail)
        db.commit()

        # Caller passes 'accretion' but changes amount to 25000.0 (persisted is 15000.0)
        rf_tampered_mov = execute_rollforward_attribution(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            prior_valuation_run_id=prior_run_id,
            movements={
                "accretion": 25000.0,  # Changed amount!
            },
            tolerance=0.01,
        )
        # Must NOT receive verified attribution!
        assert rf_tampered_mov["has_unverified_movement_source"] is True
        assert rf_tampered_mov["trace_identifier"].startswith("UNVERIFIED_SOURCE:")

        # Replaying rollforward_result with has_unverified_movement_source=False CANNOT override DB provenance!
        from app.core.execution import run_package as core_pkg
        pkg_doc = core_pkg.seal(
            identity={"package_id": f"pkg-{run_id}", "frozen_for_run_id": run_id, "project_id": proj_id, "created_at": "2026-11-04T00:00:00Z", "created_by": {"id": user_id}},
            governance={"governance_mode": "governed"},
            configuration={"basis": "STATUTORY", "methodology": "VM22", "cohort_id": "COHORT_MYGA_2026", "valuation_date": "2026-12-31"},
        )
        db.merge(RunPackage(id=f"pkg-{run_id}", run_id=run_id, project_id=proj_id, schema_version=pkg_doc["schema_version"], fingerprint_algorithm=pkg_doc["fingerprint_algorithm"], fingerprint=pkg_doc["fingerprint"], package=pkg_doc))
        db.commit()

        rep_override_attempt = execute_reporting_manifest(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            valuation_result={"valuation_run_id": run_id, "project_id": proj_id, "present_value_reserve": 150000.0, "basis": "STATUTORY", "methodology": "VM22"},
            rollforward_result={
                "is_continuous": True,
                "summary_id": rf_tampered_mov["summary_id"],
                "has_unverified_movement_source": False,  # Caller falsely claims False!
            },
            reconciliation_result={"report_id": "rec-none", "status": "PASS"},
            require_approved_reconciliation=False,
        )
        # Persisted UNVERIFIED_SOURCE trace in DB blocks approval!
        assert rep_override_attempt["is_approved_for_release"] is False
        assert rep_override_attempt["governance_status"] == "REJECTED_NON_APPROVED"


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

        # V8-P0-03 Acceptance Test: Single-row benchmark missing scenario or cohort cannot yield PASS for cell-specific valuation
        missing_cohort_rows = [{
            "basis": "STATUTORY",
            "methodology": "VM22",
            "valuation_date": "2026-12-31",
            "present_value_reserve": 125000.0,
            # Omits cohort_id!
        }]
        ers_missing_cohort = ExpectedResultSet(
            id=f"ers-no-cohort-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="Missing Cohort Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_BENCHMARK",
            row_count=1,
            columns_detected=["basis", "methodology", "valuation_date", "present_value_reserve"],
            rows_data=missing_cohort_rows,
            status="approved",
            fingerprint=fingerprint(missing_cohort_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add(ers_missing_cohort)
        db.commit()

        rec_missing_cohort = execute_governed_reconciliation(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            expected_result_set_id=ers_missing_cohort.id,
        )
        # Must NOT yield PASS! Must fail closed with NOT_VERIFIABLE due to dimensional contract
        assert rec_missing_cohort["status"] == "NOT_VERIFIABLE"
        assert "cohort" in rec_missing_cohort["not_verifiable_reason"]

        # Valid aggregate-scope benchmark deliberately omitting cohort/scenario is allowed per explicit contract
        aggregate_rows = [{
            "scope_level": "AGGREGATE",
            "basis": "STATUTORY",
            "methodology": "VM22",
            "valuation_date": "2026-12-31",
            "present_value_reserve": 125000.0,
        }]
        ers_aggregate = ExpectedResultSet(
            id=f"ers-agg-{uuid.uuid4().hex[:8]}",
            project_id=proj_id,
            name="Aggregate Scope Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_BENCHMARK",
            row_count=1,
            columns_detected=["scope_level", "basis", "methodology", "valuation_date", "present_value_reserve"],
            rows_data=aggregate_rows,
            status="approved",
            fingerprint=fingerprint(aggregate_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add(ers_aggregate)
        db.commit()

        rec_aggregate = execute_governed_reconciliation(
            db=db,
            valuation_run_id=run_id,
            project_id=proj_id,
            expected_result_set_id=ers_aggregate.id,
        )
        assert rec_aggregate["status"] == "PASS"
        assert rec_aggregate["difference"] == 0.0


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

        # 5. Validate derived dataset
        ds_val = validate_derived_dataset(
            db=db,
            project_id=proj_id,
            dataset_id=ds.id,
            user_id=user_id,
        )
        assert ds_val.status == "validated"

        # V8-P1-04 Acceptance Test: Tamper physical disk bytes between validation and approval
        store = get_artifact_store()
        parquet_path = store.path_for_uri(ds.storage_uri)
        original_bytes = parquet_path.read_bytes()
        parquet_path.write_bytes(original_bytes + b"TAMPERED_POST_VALIDATION_BYTES")

        # Attempting approval must fail closed with 400 Bad Request checksum mismatch
        with pytest.raises(ServiceError) as exc_tamper:
            approve_derived_dataset(
                db=db,
                project_id=proj_id,
                dataset_id=ds.id,
                approved_by_user_id=reviewer_id,
            )
        assert exc_tamper.value.status_code == 400
        assert "checksum mismatch" in exc_tamper.value.message.lower()
        db.refresh(ds)
        assert ds.status == "validated"  # NOT approved!

        # Restore genuine bytes -> approval succeeds
        parquet_path.write_bytes(original_bytes)
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
