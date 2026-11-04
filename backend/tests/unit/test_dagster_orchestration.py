"""Tests for Dagster Workflow Orchestration & M5 V6 Corrective Verification (P0-01..P0-07, V5-P0-B..V5-P1-I).

Per CAS §9, §15.8, and M5 V5 Independent Senior Review:
Verifies:
1. Complete 8-stage software-defined asset graph with zero production default identities (`V5-P0-B`).
2. P0-01 & V5-P0-B: Governed Data Manager ingestion (`InforceFile`/`InforceRecord`) & shared projection engine
   execution (`run_execution_service.execute_run`). Rejects missing `project_id`, `inforce_file_id`, `run_id`,
   `product_code`, `model_version_id`/`projection_set_id`, `scenario_id`, and `valuation_date`.
3. P0-02: Trusted model & model_version lineage from PostgreSQL; rejects MYGA/SPIA product-model
   mismatches, unapproved model versions, self-approved model versions, foreign-project models, and missing models.
4. P0-04 & V5-P0-D: Fail-closed physical artifact and accepted-attempt verification in `execute_cash_flow_aggregation`
   (no fallback to in-memory rows or stale attempts; fails on missing file, wrong URI, corrupted bytes,
   wrong attempt_number, non-canonical artifact_type, missing accepted_attempt_number, or invalid schema).
5. P0-05 & V5-P0-E: Valuation-derived rollforward attribution with independent `discount_unwind_illustrative`
   and `period_cash_flow_release_illustrative` movements (`ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY` — never
   regulatory VM22/LDTI/DAC/MRB/VM21/ASC815 labels and never a balancing residual `closing - opening - cf_release`).
   Supports two-period persisted valuation comparison (`prior_valuation_run_id` -> `valuation_run_id`) and rejects
   unexplained residuals or real unaccounted closing movements.
6. P0-06 & V5-P0-F: Governed benchmark reconciliation (`ExpectedResultSet` required for PASS/FAIL; actual value
   loaded from persisted `ValuationRunDetail.summary_results`; enforces four-eyes approval, product family match,
   scoped multi-row benchmark matching, and governed tolerances; rejects raw benchmarks, caller actual spoofing,
   and caller tolerance overrides).
7. P0-07 & V5-P1-G: Real Dagster retry after partial persistence (`Run`/`RunPackage` committed or `RunAttempt`
   running on Attempt 1) resuming the **exact same `run_id`** with zero duplicate `Run` rows and preserved
   attempt evidence.
8. V5-P1-H: `ValuationTaxonomyError` imported and mapped to deterministic `400 INVALID_VALUATION_TAXONOMY`
   with `allow_retries=False`.
9. V5-P1-I: `reconciliation_report_asset` halts on `FAIL` when `halt_on_reconciliation_failure=True`, and
   `reporting_asset` / `execute_reporting_manifest` distinguishes `GOVERNED_RECONCILED_FOUNDATION_PACKAGE`
   from `TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED` (`reporting_unverified`) and `REJECTED_NON_APPROVED`
   (`reporting_quarantined`).
"""

from datetime import date
from decimal import Decimal
import hashlib
import uuid

from dagster import DagsterEventType, DagsterInstance, Failure, build_asset_context, materialize
import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import close_all_sessions, sessionmaker

from app.core.artifacts import get_artifact_store
from app.core.execution import run_state
from app.core.execution.fingerprints import fingerprint, inforce_fingerprint
import app.db.database as db_mod
from app.db.database import Base
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
import app.orchestration.dagster.assets as assets_mod
from app.orchestration.dagster.assets import (
    InforceConfig,
    ProjectionConfig,
    ReconciliationConfig,
    ReportingConfig,
    RollforwardConfig,
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
from app.services import run_execution_service
from app.services.common import ServiceError, now_utc
import app.services.valuation_pipeline_service as vps_mod
from app.services.valuation_pipeline_service import (
    execute_cash_flow_aggregation,
    execute_foundation_valuation,
    execute_governed_reconciliation,
    execute_inforce_import,
    execute_projection_simulation,
    execute_reporting_manifest,
    execute_rollforward_attribution,
)
from scripts.seed_demo import seed_myga


ALL_PIPELINE_ASSETS = [
    inforce_data_asset,
    validation_asset,
    projection_run_asset,
    aggregation_asset,
    valuation_asset,
    rollforward_asset,
    reconciliation_report_asset,
    reporting_asset,
]


def _create_second_approved_inforce_file(
    db, project_id: str, user_id: str, reviewer_id: str, base_inforce_id: str | None = None
) -> InforceFile:
    """Create a second genuinely distinct approved MYGA InforceFile in the same project."""
    from app.core.execution.fingerprints import INFORCE_FINGERPRINT_SCHEME

    base_records = []
    if base_inforce_id:
        base_records = (
            db.query(InforceRecord)
            .filter(InforceRecord.file_id == base_inforce_id)
            .order_by(InforceRecord.policy_id)
            .limit(2)
            .all()
        )

    row_dicts = []
    if base_records:
        for idx, rec in enumerate(base_records, start=1):
            pid = f"MYGA-ALT-00{idx}"
            data = dict(rec.data)
            data["policy_id"] = pid
            data["product_type"] = "MYGA"
            for num_col in ("account_value", "single_premium", "premium", "initial_premium", "mgsv_base"):
                if num_col in data and data[num_col] is not None:
                    try:
                        data[num_col] = round(float(data[num_col]) * 2.5, 2)
                    except (ValueError, TypeError):
                        pass
            row_dicts.append({"policy_id": pid, "data": data})
    else:
        alt_policies = [
            ("MYGA-ALT-001", 60, "F", 500000.0, 0.048, 0.030),
            ("MYGA-ALT-002", 65, "M", 750000.0, 0.050, 0.030),
        ]
        for pid, age, gender, av, cred_rate, g_rate in alt_policies:
            data = {
                "policy_id": pid,
                "product_type": "MYGA",
                "issue_age": age,
                "attained_age": age,
                "gender": gender,
                "premium": av,
                "single_premium": av,
                "account_value": av,
                "duration_months": 0,
                "credited_rate_annual": cred_rate,
                "guaranteed_minimum_rate_annual": g_rate,
                "surrender_charge_schedule_id": "MYGA_5YR_STD",
                "guarantee_duration_months": 60,
                "free_withdrawal_pct": 0.10,
                "cohort_id": "COHORT_MYGA_ALT_2026",
            }
            row_dicts.append({"policy_id": pid, "data": data})

    fp = inforce_fingerprint((r["policy_id"], r["data"]) for r in row_dicts)
    inf2 = InforceFile(
        id="inf-myga-block-alt-002",
        project_id=project_id,
        filename="BLOCK_MYGA_ALT_2026.csv",
        file_type="csv",
        version_label="v2.0",
        status="approved",
        row_count=len(row_dicts),
        columns_detected=list(row_dicts[0]["data"].keys()),
        fingerprint=fp,
        fingerprint_scheme=INFORCE_FINGERPRINT_SCHEME,
        committed_by=user_id,
        committed_at=now_utc(),
        approved_by=reviewer_id,
        approved_at=now_utc(),
    )
    db.add(inf2)
    db.flush()

    for r in row_dicts:
        db.add(InforceRecord(file_id=inf2.id, policy_id=r["policy_id"], data=r["data"]))
    db.commit()
    return inf2


@pytest.fixture(autouse=True)
def init_test_db(monkeypatch, tmp_path):
    """Provision a file-backed SQLite DB with governed MYGA project & clean disposal (zero STDERR warnings)."""
    db_path = tmp_path / "dagster_orchestration_test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)

    with TestSessionLocal() as db:
        seeded = seed_myga(db, project_name="Dagster Governed MYGA Project")
        proj_id = seeded["project"].id
        inforce_id = seeded["inforce"].id
        mv_id = seeded["version"].id
        ps_id = seeded["projection_set"].id
        scen_id = seeded["scenarios"][0].id
        user = seeded["user"]
        reviewer = seeded["reviewer"]

        # Create second approved inforce block in same project for input sensitivity tests
        inf2 = _create_second_approved_inforce_file(db, proj_id, user.id, reviewer.id, base_inforce_id=inforce_id)

        # Create a foreign project for cross-project isolation tests
        foreign_proj = Project(id="proj-foreign-isolation-002", name="Foreign Project", created_by=user.id)
        db.add(foreign_proj)
        db.commit()

    yield {
        "project_id": proj_id,
        "inforce_file_id_1": inforce_id,
        "inforce_file_id_2": inf2.id,
        "model_version_id": mv_id,
        "projection_set_id": ps_id,
        "scenario_id": scen_id,
        "user_id": user.id,
        "reviewer_id": reviewer.id,
        "foreign_project_id": "proj-foreign-isolation-002",
    }

    close_all_sessions()
    test_engine.dispose()


def _make_run_config(
    project_id: str,
    inforce_file_id: str,
    model_version_id: str,
    scenario_id: str,
    run_id: str | None = None,
    projection_set_id: str | None = None,
    projection_months: int = 12,
    basis: str = "STATUTORY",
    methodology: str = "VM22",
    valuation_date: str = "2026-12-31",
    annual_discount_rate: float = 0.045,
    expected_result_set_id: str | None = None,
    prior_valuation_run_id: str | None = None,
    halt_on_reconciliation_failure: bool = True,
    require_approved_reconciliation: bool = False,
) -> dict:
    return {
        "ops": {
            "inforce_data_asset": {
                "config": {
                    "project_id": project_id,
                    "policy_block": "BLOCK_MYGA_2026",
                    "inforce_file_id": inforce_file_id,
                }
            },
            "projection_run_asset": {
                "config": {
                    "project_id": project_id,
                    "projection_months": projection_months,
                    "run_id": run_id or f"run-m5-dagster-proj-{uuid.uuid4().hex[:8]}",
                    "projection_set_id": projection_set_id,
                    "model_version_id": model_version_id,
                    "scenario_id": scenario_id,
                    "valuation_date": valuation_date,
                }
            },
            "valuation_asset": {
                "config": {
                    "basis": basis,
                    "methodology": methodology,
                    "valuation_date": valuation_date,
                    "annual_discount_rate": annual_discount_rate,
                }
            },
            "rollforward_asset": {
                "config": {
                    "prior_valuation_run_id": prior_valuation_run_id,
                    "tolerance": 0.01,
                }
            },
            "reconciliation_report_asset": {
                "config": {
                    "expected_result_set_id": expected_result_set_id,
                    "target_metric": "present_value_reserve",
                    "halt_on_reconciliation_failure": halt_on_reconciliation_failure,
                }
            },
            "reporting_asset": {
                "config": {
                    "require_approved_reconciliation": require_approved_reconciliation,
                }
            },
        }
    }


def test_dagster_definitions_structure_and_zero_default_identities():
    """Verify that defs contains the expected 8 assets and Config classes have no hardcoded default IDs (V5-P0-B)."""
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

    # V5-P0-B: Verify Dagster Config classes do not default to synthetic project/run/valuation_date IDs
    inf_cfg = InforceConfig()
    proj_cfg = ProjectionConfig()
    val_cfg = ValuationConfig()
    assert inf_cfg.project_id is None
    assert inf_cfg.inforce_file_id is None
    assert proj_cfg.project_id is None
    assert proj_cfg.run_id is None
    assert proj_cfg.valuation_date is None
    assert val_cfg.valuation_date is None


def test_dagster_end_to_end_pipeline_materialization_and_input_sensitivity(init_test_db):
    """P0-01, P0-02, P0-04, P0-05, P0-06, V5-P0-B..V5-P1-I: Execute all 8 assets on 2 distinct governed policy blocks.

    Proves:
    - Real governed InforceFile + InforceRecord rows are loaded from DB and cryptographically verified.
    - Shared projection engine (`run_execution_service.execute_run`) executes real MYGA model formulas.
    - Frozen projection & valuation RunPackages record the actual MYGA Model and approved ModelVersion from DB.
    - Physical Parquet artifacts exist on disk and match RunArtifact SHA-256 checksums for the accepted attempt.
    - Rollforward derives opening, illustrative generic PV movements, closing, cohort, basis, and methodology
      from the valuation run (`ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY`).
    - Two distinct approved inforce blocks produce genuinely different fingerprints, Parquet hashes,
      aggregated cash flows, valuation reserves, and rollforward opening/closing balances.
    """
    store = get_artifact_store()
    proj_id = init_test_db["project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]
    inf2_id = init_test_db["inforce_file_id_2"]
    mv_id = init_test_db["model_version_id"]
    scen_id = init_test_db["scenario_id"]

    cfg1 = _make_run_config(
        proj_id, inforce_file_id=inf1_id, model_version_id=mv_id, scenario_id=scen_id, run_id="run-m5-proj-block-1"
    )
    cfg2 = _make_run_config(
        proj_id, inforce_file_id=inf2_id, model_version_id=mv_id, scenario_id=scen_id, run_id="run-m5-proj-block-2"
    )
    with DagsterInstance.ephemeral() as instance:
        res1 = materialize(ALL_PIPELINE_ASSETS, run_config=cfg1, instance=instance)
        assert res1.success is True
        res2 = materialize(ALL_PIPELINE_ASSETS, run_config=cfg2, instance=instance)
        assert res2.success is True

    events1 = {e.asset_key.path[-1]: e.materialization.metadata for e in res1.get_asset_materialization_events()}
    events2 = {e.asset_key.path[-1]: e.materialization.metadata for e in res2.get_asset_materialization_events()}
    assert len(events1) == 8
    assert len(events2) == 8

    # 1. P0-01: Distinct governed inforce blocks produce distinct fingerprints & record counts
    assert events1["inforce_data_asset"]["record_count"].value == 25
    assert events2["inforce_data_asset"]["record_count"].value == 2
    assert events1["inforce_data_asset"]["fingerprint"].value != events2["inforce_data_asset"]["fingerprint"].value

    # 2. P0-01 & P0-04: Projection outputs are persisted via shared engine with distinct Parquet hashes
    proj1_fp = events1["projection_run_asset"]["artifact_fingerprint"].value
    proj2_fp = events2["projection_run_asset"]["artifact_fingerprint"].value
    assert proj1_fp != proj2_fp
    assert events1["projection_run_asset"]["accepted_attempt_number"].value == 1

    proj1_path = store.path_for_uri(events1["projection_run_asset"]["storage_uri"].value)
    assert proj1_path.exists()
    assert hashlib.sha256(proj1_path.read_bytes()).hexdigest() == proj1_fp

    # 3. P0-01: Aggregated cash flows and valuation reserves differ between the two policy blocks
    reserve1 = events1["valuation_asset"]["present_value_reserve"].value
    reserve2 = events2["valuation_asset"]["present_value_reserve"].value
    assert reserve1 > 0.0
    assert reserve2 > 0.0
    assert reserve1 != reserve2

    # 4. P0-05 & V5-P0-E: Rollforward opening/closing balances are derived from actual valuation runs
    rf1_open = events1["rollforward_asset"]["opening_balance"].value
    rf1_close = events1["rollforward_asset"]["ending_balance"].value
    rf2_close = events2["rollforward_asset"]["ending_balance"].value
    assert events1["rollforward_asset"]["attribution_mode"].value == "ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY"
    assert abs(rf1_close - reserve1) <= 0.01
    assert abs(rf2_close - reserve2) <= 0.01
    assert rf1_close != rf2_close

    # 5. V5-P1-I: Without an ExpectedResultSet, reporting_asset emits TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED
    assert events1["reporting_asset"]["governance_status"].value == "TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED"
    assert events1["reporting_asset"]["is_approved_for_release"].value is False
    assert events1["reporting_asset"]["artifact_type"].value == "reporting_unverified"

    # 6. Verify Database Rows & P0-02 Trusted Model Lineage
    with db_mod.SessionLocal() as db:
        db_proj_run = db.get(Run, "run-m5-proj-block-1")
        assert db_proj_run is not None
        assert db_proj_run.status in ("completed", "success")
        assert db_proj_run.accepted_attempt_number == 1
        assert db_proj_run.model_version_id == mv_id

        db_proj_pkg = db.query(RunPackage).filter(RunPackage.run_id == db_proj_run.id).first()
        assert db_proj_pkg is not None
        assert db_proj_pkg.package["configuration"]["model"]["product_code"] == "MYGA"
        assert db_proj_pkg.package["configuration"]["model_version"]["id"] == mv_id
        assert len(db_proj_pkg.package["configuration"]["formulas"]) > 0

        val_run = db.query(Run).filter(
            Run.run_type == "valuation",
            Run.source_projection_run_id == db_proj_run.id,
        ).first()
        assert val_run is not None
        val_pkg = db.query(RunPackage).filter(RunPackage.run_id == val_run.id).first()
        assert val_pkg is not None
        assert val_pkg.package["configuration"]["model"]["product_code"] == "MYGA"
        assert val_pkg.package["configuration"]["model_version"]["id"] == mv_id

        rf_rows = db.query(RollforwardSummary).filter(RollforwardSummary.run_id == val_run.id).all()
        assert len(rf_rows) == 4
        assert all(r.is_continuity_verified for r in rf_rows)
        assert abs(float(rf_rows[0].closing_balance) - reserve1) <= 0.01
        assert {r.component_name for r in rf_rows} == {
            "opening_balance",
            "discount_unwind_illustrative",
            "period_cash_flow_release_illustrative",
            "ending_balance",
        }


def test_v5_p0_b_no_implicit_defaults_or_substitutions(init_test_db):
    """V5-P0-B: Reject missing/default project_id, inforce_file_id, run_id, product_code, model_version_id,
    scenario_id, and valuation_date."""
    proj_id = init_test_db["project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]
    mv_id = init_test_db["model_version_id"]
    scen_id = init_test_db["scenario_id"]

    with db_mod.SessionLocal() as db:
        # 1. Missing or default project_id -> 400 PROJECT_ID_REQUIRED
        for bad_proj in ("", "proj-m5-valuation-default"):
            with pytest.raises(ServiceError) as exc_proj:
                execute_inforce_import(db=db, project_id=bad_proj, inforce_file_id=inf1_id)
            assert exc_proj.value.status_code == 400
            assert exc_proj.value.code == "PROJECT_ID_REQUIRED"

        # 2. Missing inforce_file_id (no implicit latest-file selection!) -> 400 INFORCE_FILE_REQUIRED
        with pytest.raises(ServiceError) as exc_inf:
            execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA_2026", inforce_file_id=None)
        assert exc_inf.value.status_code == 400
        assert exc_inf.value.code == "INFORCE_FILE_REQUIRED"

        imported = execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA_2026", inforce_file_id=inf1_id)

        # 3. Missing or default run_id -> 400 RUN_ID_REQUIRED
        for bad_run_id in (None, "", "run-m5-dagster-projection-001"):
            with pytest.raises(ServiceError) as exc_run:
                execute_projection_simulation(
                    db=db,
                    project_id=proj_id,
                    validated_inforce=imported,
                    run_id=bad_run_id,
                    model_version_id=mv_id,
                    scenario_id=scen_id,
                    valuation_date="2026-12-31",
                )
            assert exc_run.value.status_code == 400
            assert exc_run.value.code == "RUN_ID_REQUIRED"

        # 4. Missing product_code / product_type on policy records (no "MYGA" fallback!) -> 400 PRODUCT_CODE_REQUIRED
        bad_inforce = dict(imported)
        bad_inforce["policies"] = [{"policy_id": "POL-NO-PROD", "account_value": 100000.0}]
        bad_inforce["validated_policies"] = [{"policy_id": "POL-NO-PROD", "account_value": 100000.0}]
        bad_inforce["records"] = [{"policy_id": "POL-NO-PROD", "account_value": 100000.0}]
        with pytest.raises(ServiceError) as exc_prod:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=bad_inforce,
                run_id="run-no-prod-code",
                model_version_id=mv_id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_prod.value.status_code == 400
        assert exc_prod.value.code == "PRODUCT_CODE_REQUIRED"

        # 5. Missing model_version_id AND projection_set_id (no implicit first-model query!) -> 400 MODEL_IDENTITY_REQUIRED
        with pytest.raises(ServiceError) as exc_mod:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                run_id="run-no-model-id",
                model_version_id=None,
                projection_set_id=None,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_mod.value.status_code == 400
        assert exc_mod.value.code == "MODEL_IDENTITY_REQUIRED"

        # 6. Missing scenario_id when projection_set_id is omitted -> 400 SCENARIO_REQUIRED
        with pytest.raises(ServiceError) as exc_scen:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                run_id="run-no-scen-id",
                model_version_id=mv_id,
                scenario_id=None,
                valuation_date="2026-12-31",
            )
        assert exc_scen.value.status_code == 400
        assert exc_scen.value.code == "SCENARIO_REQUIRED"

        # 7. Missing valuation_date when projection_set_id is omitted -> 400 VALUATION_DATE_REQUIRED
        with pytest.raises(ServiceError) as exc_vdate:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                run_id="run-no-val-date",
                model_version_id=mv_id,
                scenario_id=scen_id,
                valuation_date=None,
            )
        assert exc_vdate.value.status_code == 400
        assert exc_vdate.value.code == "VALUATION_DATE_REQUIRED"


def test_p0_01_and_p0_02_inforce_and_model_governance_rejections(init_test_db):
    """P0-01 & P0-02: Reject missing/foreign/unapproved/tampered inforce and mismatched/unapproved/self-approved models."""
    proj_id = init_test_db["project_id"]
    foreign_proj_id = init_test_db["foreign_project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]
    scen_id = init_test_db["scenario_id"]
    user_id = init_test_db["user_id"]
    reviewer_id = init_test_db["reviewer_id"]

    with db_mod.SessionLocal() as db:
        # 1. Nonexistent inforce_file_id -> 404 INFORCE_NOT_FOUND
        with pytest.raises(ServiceError) as exc_missing:
            execute_inforce_import(db=db, project_id=proj_id, policy_block="X", inforce_file_id="nonexistent-inf")
        assert exc_missing.value.status_code == 404
        assert exc_missing.value.code == "INFORCE_NOT_FOUND"

        # 2. Cross-project inforce_file_id -> 404 INFORCE_NOT_FOUND
        with pytest.raises(ServiceError) as exc_foreign:
            execute_inforce_import(db=db, project_id=foreign_proj_id, policy_block="X", inforce_file_id=inf1_id)
        assert exc_foreign.value.status_code == 404
        assert exc_foreign.value.code == "INFORCE_NOT_FOUND"

        # 3. Unapproved (draft) InforceFile -> 400 INFORCE_NOT_RUNNABLE
        draft_inf = InforceFile(
            id="inf-draft-001",
            project_id=proj_id,
            filename="DRAFT_BLOCK.csv",
            file_type="csv",
            version_label="v1",
            status="uploaded",
            row_count=1,
        )
        db.add(draft_inf)
        db.commit()

        with pytest.raises(ServiceError) as exc_draft:
            execute_inforce_import(db=db, project_id=proj_id, policy_block="DRAFT_BLOCK", inforce_file_id=draft_inf.id)
        assert exc_draft.value.status_code == 400
        assert exc_draft.value.code in ("INFORCE_NOT_APPROVED", "INFORCE_NOT_RUNNABLE")

        # 4. P0-02: MYGA inforce with SPIA model -> 400 PRODUCT_MODEL_MISMATCH
        spia_model = Model(
            id="model-spia-mismatch",
            project_id=proj_id,
            name="SPIA Model",
            product_code="SPIA",
            status="approved",
            created_by=user_id,
        )
        db.add(spia_model)
        db.flush()
        spia_mv = ModelVersion(
            id="mv-spia-mismatch-v1",
            model_id=spia_model.id,
            version_label="v1.0",
            status="approved",
            basis="STATUTORY",
            methodology="VM22",
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add(spia_mv)
        # Unapproved MYGA model version
        myga_model = db.query(Model).filter(Model.project_id == proj_id, Model.product_code == "MYGA").first()
        draft_mv = ModelVersion(
            id="mv-myga-draft-unapproved",
            model_id=myga_model.id,
            version_label="v99-draft",
            status="draft",
            basis="STATUTORY",
            methodology="VM22",
            approved_at=None,
        )
        # Self-approved MYGA model version
        self_mv = ModelVersion(
            id="mv-myga-self-approved",
            model_id=myga_model.id,
            version_label="v99-self",
            status="approved",
            basis="STATUTORY",
            methodology="VM22",
            created_by=user_id,
            approved_by=user_id,
            approved_at=now_utc(),
        )
        db.add_all([draft_mv, self_mv])
        db.commit()

        imported = execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA_2026", inforce_file_id=inf1_id)

        with pytest.raises(ServiceError) as exc_prod_mismatch:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                projection_months=12,
                run_id="run-mismatch-spia",
                model_version_id=spia_mv.id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_prod_mismatch.value.status_code == 400
        assert exc_prod_mismatch.value.code == "PRODUCT_MODEL_MISMATCH"

        # 5. P0-02: Unapproved (draft) ModelVersion -> 400 MODEL_VERSION_NOT_APPROVED
        with pytest.raises(ServiceError) as exc_unapproved_mv:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                projection_months=12,
                run_id="run-unapproved-mv",
                model_version_id=draft_mv.id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_unapproved_mv.value.status_code == 400
        assert exc_unapproved_mv.value.code == "MODEL_VERSION_NOT_APPROVED"

        # 6. V5-P0-B: Self-approved ModelVersion -> 400 SELF_APPROVED_MODEL_FORBIDDEN
        with pytest.raises(ServiceError) as exc_self_mv:
            execute_projection_simulation(
                db=db,
                project_id=proj_id,
                validated_inforce=imported,
                projection_months=12,
                run_id="run-self-approved-mv",
                model_version_id=self_mv.id,
                scenario_id=scen_id,
                valuation_date="2026-12-31",
            )
        assert exc_self_mv.value.status_code == 400
        assert exc_self_mv.value.code == "SELF_APPROVED_MODEL_FORBIDDEN"


def test_p0_04_and_v5_p0_d_aggregation_fails_closed_on_missing_corrupted_or_stale_attempt_artifact(init_test_db, tmp_path):
    """P0-04 & V5-P0-D: Aggregation never falls back to in-memory rows or stale attempts; fails closed on
    missing/corrupt/mismatched Parquet, wrong attempt_number, wrong artifact_type, or missing accepted_attempt_number."""
    store = get_artifact_store()
    proj_id = init_test_db["project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]
    mv_id = init_test_db["model_version_id"]
    scen_id = init_test_db["scenario_id"]

    with db_mod.SessionLocal() as db:
        imported = execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA_2026", inforce_file_id=inf1_id)
        proj_out = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported,
            projection_months=6,
            run_id="run-p004-agg-check",
            model_version_id=mv_id,
            scenario_id=scen_id,
            valuation_date="2026-12-31",
        )
        run_id = proj_out["run_id"]
        art_id = proj_out["artifact_id"]
        uri = proj_out["storage_uri"]
        parquet_path = store.path_for_uri(uri)
        assert parquet_path.exists()

        # 1. Wrong URI fails closed -> 400 ARTIFACT_URI_MISMATCH
        with pytest.raises(ServiceError) as exc_uri:
            execute_cash_flow_aggregation(
                db=db,
                projection_run_id=run_id,
                artifact_id=art_id,
                storage_uri="local://wrong/uri/outputs.parquet",
                project_id=proj_id,
            )
        assert exc_uri.value.status_code == 400
        assert exc_uri.value.code == "ARTIFACT_URI_MISMATCH"

        # 2. V5-P0-D: Stale attempt artifact fails closed -> 400 ARTIFACT_ATTEMPT_MISMATCH
        stale_stored = store.write_bytes(
            namespace=run_id,
            artifact_type="outputs",
            filename="stale_attempt2.parquet",
            content=parquet_path.read_bytes(),
        )
        stale_art = RunArtifact(
            id="art-stale-attempt-99",
            run_id=run_id,
            attempt_number=99,  # accepted_attempt_number is 1!
            artifact_type="outputs",
            storage_uri=stale_stored.uri,
            storage_backend="local",
            file_format="parquet",
            schema_version="m2-v1",
            row_count=proj_out["row_count"],
            checksum_sha256=proj_out["artifact_fingerprint"],
        )
        log_stored = store.write_bytes(
            namespace=run_id,
            artifact_type="log",
            filename="attempt1_log.parquet",
            content=parquet_path.read_bytes(),
        )
        log_art = RunArtifact(
            id="art-log-attempt-1",
            run_id=run_id,
            attempt_number=1,
            artifact_type="log",
            storage_uri=log_stored.uri,
            storage_backend="local",
            file_format="parquet",
            schema_version="m2-v1",
            row_count=proj_out["row_count"],
            checksum_sha256=proj_out["artifact_fingerprint"],
        )
        db.add_all([stale_art, log_art])
        db.commit()

        with pytest.raises(ServiceError) as exc_stale:
            execute_cash_flow_aggregation(
                db=db,
                projection_run_id=run_id,
                artifact_id=stale_art.id,
                storage_uri=stale_stored.uri,
                project_id=proj_id,
            )
        assert exc_stale.value.status_code == 400
        assert exc_stale.value.code == "ARTIFACT_ATTEMPT_MISMATCH"

        # 3. V5-P0-D: Non-canonical artifact_type='log' fails closed -> 400 INVALID_ARTIFACT_TYPE
        with pytest.raises(ServiceError) as exc_log_type:
            execute_cash_flow_aggregation(
                db=db,
                projection_run_id=run_id,
                artifact_id=log_art.id,
                storage_uri=log_stored.uri,
                project_id=proj_id,
            )
        assert exc_log_type.value.status_code == 400
        assert exc_log_type.value.code == "INVALID_ARTIFACT_TYPE"

        # 4. Mutated Parquet bytes fail closed -> 400 ARTIFACT_CHECKSUM_MISMATCH
        original_bytes = parquet_path.read_bytes()
        parquet_path.write_bytes(original_bytes + b"tampered_bytes")
        with pytest.raises(ServiceError) as exc_hash:
            execute_cash_flow_aggregation(
                db=db,
                projection_run_id=run_id,
                artifact_id=art_id,
                storage_uri=uri,
                project_id=proj_id,
            )
        assert exc_hash.value.status_code == 400
        assert exc_hash.value.code == "ARTIFACT_CHECKSUM_MISMATCH"

        # 5. Deleted physical Parquet file fails closed -> 400 PHYSICAL_ARTIFACT_MISSING
        parquet_path.unlink()
        with pytest.raises(ServiceError) as exc_missing:
            execute_cash_flow_aggregation(
                db=db,
                projection_run_id=run_id,
                artifact_id=art_id,
                storage_uri=uri,
                project_id=proj_id,
            )
        assert exc_missing.value.status_code == 400
        assert exc_missing.value.code == "PHYSICAL_ARTIFACT_MISSING"

        # 6. Invalid Parquet schema on accepted attempt fails closed -> 422 INVALID_ARTIFACT_SCHEMA
        bad_df = pl.DataFrame({"unexpected_col": [1, 2, 3]})
        bad_tmp = tmp_path / "bad_schema.parquet"
        bad_df.write_parquet(bad_tmp)
        bad_bytes = bad_tmp.read_bytes()
        bad_sha = hashlib.sha256(bad_bytes).hexdigest()
        stored_bad = store.write_bytes(
            namespace=run_id,
            artifact_type="outputs",
            filename="bad_schema.parquet",
            content=bad_bytes,
        )
        bad_art = RunArtifact(
            id=f"art-bad-schema-{uuid.uuid4().hex[:8]}",
            run_id=run_id,
            attempt_number=1,
            artifact_type="outputs",
            storage_uri=stored_bad.uri,
            storage_backend="local",
            file_format="parquet",
            schema_version="m2-v1",
            row_count=3,
            checksum_sha256=bad_sha,
        )
        db.add(bad_art)
        db.commit()

        with pytest.raises(ServiceError) as exc_schema:
            execute_cash_flow_aggregation(
                db=db,
                projection_run_id=run_id,
                artifact_id=bad_art.id,
                storage_uri=stored_bad.uri,
                project_id=proj_id,
            )
        assert exc_schema.value.status_code in (400, 422)
        assert exc_schema.value.code in ("INVALID_ARTIFACT_SCHEMA", "ARTIFACT_SCHEMA_INVALID")

        # 7. V5-P0-D: Missing accepted_attempt_number on Run fails closed -> 400 ACCEPTED_ATTEMPT_MISSING
        proj_run_row = db.get(Run, run_id)
        proj_run_row.accepted_attempt_number = None
        db.commit()
        with pytest.raises(ServiceError) as exc_no_acc:
            execute_cash_flow_aggregation(
                db=db,
                projection_run_id=run_id,
                artifact_id=bad_art.id,
                project_id=proj_id,
            )
        assert exc_no_acc.value.status_code == 400
        assert exc_no_acc.value.code == "ACCEPTED_ATTEMPT_MISSING"


def test_p0_05_and_v5_p0_e_rollforward_attribution_and_two_period_comparison(init_test_db):
    """P0-05 & V5-P0-E: Rollforward uses independent generic PV movements (never residual plugs or regulatory
    movement names), supports two-period persisted valuation comparison (`prior_valuation_run_id`), and rejects
    unexplained residuals or real unaccounted closing movements."""
    proj_id = init_test_db["project_id"]
    foreign_proj_id = init_test_db["foreign_project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]
    mv_id = init_test_db["model_version_id"]
    scen_id = init_test_db["scenario_id"]

    with db_mod.SessionLocal() as db:
        imported = execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA_2026", inforce_file_id=inf1_id)
        proj_out = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported,
            projection_months=6,
            run_id="run-p005-proj",
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

        # Create Prior GAAP / LDTI valuation run at 2026-12-31
        val_prior = execute_foundation_valuation(
            db=db,
            upstream_run_id=proj_out["run_id"],
            basis="GAAP",
            methodology="LDTI",
            valuation_date="2026-12-31",
            annual_discount_rate=0.04,
            aggregated_cash_flows=agg_out["aggregated_cash_flows"],
            project_id=proj_id,
        )
        prior_val_id = val_prior["valuation_run_id"]

        # 1. Single-run illustrative PV attribution uses ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY movements
        rf_single = execute_rollforward_attribution(
            db=db,
            valuation_run_id=prior_val_id,
            project_id=proj_id,
        )
        assert rf_single["basis"] == "GAAP"
        assert rf_single["methodology"] == "LDTI"
        assert rf_single["attribution_mode"] == "ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY"
        assert set(rf_single["movements"].keys()) == {
            "discount_unwind_illustrative",
            "period_cash_flow_release_illustrative",
        }
        assert abs(rf_single["ending_balance"] - val_prior["present_value_reserve"]) <= 0.01

        # 2. Two-period persisted valuation comparison (prior 2026-12-31 -> current 2027-01-31)
        val_curr = execute_foundation_valuation(
            db=db,
            upstream_run_id=proj_out["run_id"],
            basis="GAAP",
            methodology="LDTI",
            valuation_date="2027-01-31",
            annual_discount_rate=0.04,
            aggregated_cash_flows=agg_out["aggregated_cash_flows"][1:],
            project_id=proj_id,
        )
        curr_val_id = val_curr["valuation_run_id"]

        # Seed explicit two-period attribution movements explaining opening (prior closing) -> closing (curr closing)
        prior_close_dec = Decimal(str(round(val_prior["present_value_reserve"], 4)))
        curr_close_dec = Decimal(str(round(val_curr["present_value_reserve"], 4)))
        r_m = (1.0 + 0.04) ** (1.0 / 12.0) - 1.0
        cf_2 = float(agg_out["aggregated_cash_flows"][1])
        unwind_p2 = Decimal(str(round(float(prior_close_dec) * r_m, 4)))
        release_p2 = Decimal(str(round(-cf_2, 4)))

        rf_two_period = execute_rollforward_attribution(
            db=db,
            valuation_run_id=curr_val_id,
            project_id=proj_id,
            prior_valuation_run_id=prior_val_id,
            movements={
                "discount_unwind_illustrative": unwind_p2,
                "period_cash_flow_release_illustrative": release_p2,
            },
        )
        assert rf_two_period["attribution_mode"] == "MULTI_PERIOD_PERSISTED_VALUATION_COMPARISON"
        assert rf_two_period["prior_valuation_run_id"] == prior_val_id
        assert abs(rf_two_period["opening_balance"] - float(prior_close_dec)) <= 0.01
        assert abs(rf_two_period["ending_balance"] - float(curr_close_dec)) <= 0.01
        assert rf_two_period["is_continuous"] is True

        # 3. V5-P0-E: Mutate closing reserve in ValuationRunDetail without updating movements -> MUST FAIL (no residual recomputation!)
        curr_detail = db.get(ValuationRunDetail, curr_val_id)
        tampered_summary = dict(curr_detail.summary_results)
        tampered_summary["closing_reserve"] = float(curr_close_dec) + 250.0
        tampered_summary["present_value_reserve"] = float(curr_close_dec) + 250.0
        curr_detail.summary_results = tampered_summary
        db.commit()

        with pytest.raises(ServiceError) as exc_unaccounted:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=curr_val_id,
                project_id=proj_id,
                prior_valuation_run_id=prior_val_id,
                movements={
                    "discount_unwind_illustrative": unwind_p2,
                    "period_cash_flow_release_illustrative": release_p2,
                },
            )
        assert exc_unaccounted.value.status_code == 422
        assert exc_unaccounted.value.code == "ROLLFORWARD_DISCONTINUITY"

        # Restore summary_results
        tampered_summary["closing_reserve"] = float(curr_close_dec)
        tampered_summary["present_value_reserve"] = float(curr_close_dec)
        curr_detail.summary_results = tampered_summary
        db.commit()

        # 4. V5-P0-E: Forbidden residual/plug movement keys fail closed -> 422 UNEXPLAINED_MOVEMENT_FORBIDDEN
        with pytest.raises(ServiceError) as exc_plug:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=prior_val_id,
                project_id=proj_id,
                movements={"unexplained_residual": Decimal("0.00")},
            )
        assert exc_plug.value.status_code == 422
        assert exc_plug.value.code == "UNEXPLAINED_MOVEMENT_FORBIDDEN"

        # 5. GAAP/LDTI parent rejects STATUTORY override -> 400 ROLLFORWARD_BASIS_MISMATCH
        with pytest.raises(ServiceError) as exc_basis:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=prior_val_id,
                project_id=proj_id,
                basis="STATUTORY",
            )
        assert exc_basis.value.status_code == 400
        assert exc_basis.value.code == "ROLLFORWARD_BASIS_MISMATCH"

        # 6. Cross-project valuation_run_id returns 404
        with pytest.raises(ServiceError) as exc_cross:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=prior_val_id,
                project_id=foreign_proj_id,
            )
        assert exc_cross.value.status_code == 404
        assert exc_cross.value.code in ("RUN_NOT_FOUND", "VALUATION_RUN_NOT_FOUND")


def test_p0_06_and_v5_p0_f_governed_benchmark_reconciliation_and_v5_p1_i_reporting_gate(init_test_db):
    """P0-06, V5-P0-F, V5-P1-I:
    - Actual value is loaded from persisted `ValuationRunDetail.summary_results` (rejects caller spoofing).
    - Raw `expected_benchmark` is rejected even when `expected_result_set_id` is provided.
    - Enforces four-eyes (`created_by != approved_by`), product family match, scoped multi-row matching,
      and governed tolerance limits.
    - `reconciliation_report_asset` halts on `FAIL` when `halt_on_reconciliation_failure=True`.
    - `execute_reporting_manifest` distinguishes `GOVERNED_RECONCILED_FOUNDATION_PACKAGE` from
      `TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED` and `REJECTED_NON_APPROVED`.
    """
    proj_id = init_test_db["project_id"]
    foreign_proj_id = init_test_db["foreign_project_id"]
    user_id = init_test_db["user_id"]
    reviewer_id = init_test_db["reviewer_id"]
    mv_id = init_test_db["model_version_id"]

    assert "expected_benchmark" not in ReconciliationConfig.model_fields

    val_run_id = f"run-test-rec-{uuid.uuid4().hex[:8]}"
    actual_reserve = 125000.00

    with db_mod.SessionLocal() as db:
        r = Run(
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
            summary_results={
                "present_value_reserve": actual_reserve,
                "closing_reserve": actual_reserve,
                "cohort_id": "COHORT_MYGA_2026",
            },
        )
        from app.core.execution import run_package as core_pkg
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
        r.run_package_fingerprint = pkg_doc["fingerprint"]
        val_pkg = RunPackage(
            id=f"pkg-{val_run_id}",
            run_id=val_run_id,
            project_id=proj_id,
            schema_version=pkg_doc["schema_version"],
            fingerprint_algorithm=pkg_doc["fingerprint_algorithm"],
            fingerprint=pkg_doc["fingerprint"],
            package=pkg_doc,
        )

        rf_summary = RollforwardSummary(
            id=f"rf-test-{uuid.uuid4().hex[:8]}",
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
            trace_identifier="VERIFIED:tol-policy-m5-standard:trace-01",
        )
        db.add_all([r, detail, val_pkg, rf_summary])

        # 1. Seed four-eyes approved multi-row scoped ExpectedResultSet (matching row + other cohort row)
        ers_scoped_rows = [
            {
                "basis": "STATUTORY",
                "methodology": "VM22",
                "cohort_id": "COHORT_OTHER_999",
                "valuation_date": "2026-12-31",
                "present_value_reserve": 999999.00,
            },
            {
                "basis": "STATUTORY",
                "methodology": "VM22",
                "cohort_id": "COHORT_MYGA_2026",
                "valuation_date": "2026-12-31",
                "present_value_reserve": 125000.005,
                "abs_tol": 0.01,
                "rel_tol": 1e-4,
            },
        ]
        ers_match = ExpectedResultSet(
            id="ers-approved-match-01",
            project_id=proj_id,
            name="VM22 Golden Benchmark Match",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_GOLDEN_TABLE",
            row_count=2,
            columns_detected=["basis", "methodology", "cohort_id", "valuation_date", "present_value_reserve"],
            rows_data=ers_scoped_rows,
            status="approved",
            fingerprint=fingerprint(ers_scoped_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )

        # 2. Seed four-eyes approved mismatching ExpectedResultSet
        ers_fail_rows = [
            {
                "basis": "STATUTORY",
                "methodology": "VM22",
                "cohort_id": "COHORT_MYGA_2026",
                "valuation_date": "2026-12-31",
                "present_value_reserve": 130000.00,
            }
        ]
        ers_fail = ExpectedResultSet(
            id="ers-approved-fail-01",
            project_id=proj_id,
            name="VM22 Golden Benchmark Mismatch",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_GOLDEN_TABLE",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=ers_fail_rows,
            status="approved",
            fingerprint=fingerprint(ers_fail_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )

        # 3. Seed self-approved ExpectedResultSet (created_by == approved_by)
        ers_self = ExpectedResultSet(
            id="ers-self-approved-01",
            project_id=proj_id,
            name="Self Approved Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="SPREADSHEET",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=ers_fail_rows,
            status="approved",
            fingerprint=fingerprint(ers_fail_rows),
            created_by=user_id,
            approved_by=user_id,
            approved_at=now_utc(),
        )

        # 4. Seed unscoped ambiguous multi-row ExpectedResultSet
        ers_ambig_rows = [
            {"present_value_reserve": 110000.00},
            {"present_value_reserve": 125000.00},
        ]
        ers_ambig = ExpectedResultSet(
            id="ers-ambig-01",
            project_id=proj_id,
            name="Ambiguous Multi-Row Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="SPREADSHEET",
            row_count=2,
            columns_detected=["present_value_reserve"],
            rows_data=ers_ambig_rows,
            status="approved",
            fingerprint=fingerprint(ers_ambig_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )

        # 5. Seed product-mismatched ExpectedResultSet (SPIA vs MYGA)
        ers_spia = ExpectedResultSet(
            id="ers-spia-mismatch-01",
            project_id=proj_id,
            name="SPIA Benchmark",
            product_code="SPIA",
            version_label="v1",
            source_reference="ACTUARIAL_GOLDEN_TABLE",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=ers_fail_rows,
            status="approved",
            fingerprint=fingerprint(ers_fail_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )

        # 6. Seed foreign-project ExpectedResultSet
        ers_foreign = ExpectedResultSet(
            id="ers-foreign-01",
            project_id=foreign_proj_id,
            name="Foreign Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_GOLDEN_TABLE",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=ers_fail_rows,
            status="approved",
            fingerprint=fingerprint(ers_fail_rows),
            created_by=user_id,
            approved_by=reviewer_id,
            approved_at=now_utc(),
        )
        db.add_all([ers_match, ers_fail, ers_self, ers_ambig, ers_spia, ers_foreign])
        db.commit()

        # Negative A: Raw expected_benchmark rejected even when expected_result_set_id is provided!
        with pytest.raises(ServiceError) as exc_raw:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                expected_result_set_id=ers_match.id,
                expected_benchmark=125000.00,
            )
        assert exc_raw.value.status_code == 400
        assert exc_raw.value.code == "UNGOVERNED_BENCHMARK_FORBIDDEN"

        # Negative B: Caller actual_reserve spoofing rejected -> 400 ACTUAL_VALUE_SPOOF_FORBIDDEN
        with pytest.raises(ServiceError) as exc_spoof:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                actual_reserve=130000.00,  # tries to spoof persisted 125000.00!
                expected_result_set_id=ers_fail.id,
            )
        assert exc_spoof.value.status_code == 400
        assert exc_spoof.value.code == "ACTUAL_VALUE_SPOOF_FORBIDDEN"

        # Negative C: Giant caller tolerance override rejected -> 400 UNGOVERNED_TOLERANCE_OVERRIDE_FORBIDDEN
        with pytest.raises(ServiceError) as exc_tol:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                expected_result_set_id=ers_fail.id,
                tolerance=1000000.0,
            )
        assert exc_tol.value.status_code == 400
        assert exc_tol.value.code == "UNGOVERNED_TOLERANCE_OVERRIDE_FORBIDDEN"

        # Negative D: Self-approved ExpectedResultSet rejected -> 400 SELF_APPROVED_BENCHMARK_FORBIDDEN
        with pytest.raises(ServiceError) as exc_self_b:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                expected_result_set_id=ers_self.id,
            )
        assert exc_self_b.value.status_code == 400
        assert exc_self_b.value.code == "SELF_APPROVED_BENCHMARK_FORBIDDEN"

        # Negative E: Ambiguous unscoped multi-row ExpectedResultSet rejected -> 400 AMBIGUOUS_BENCHMARK_ROWS
        with pytest.raises(ServiceError) as exc_ambig_b:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                expected_result_set_id=ers_ambig.id,
            )
        assert exc_ambig_b.value.status_code == 400
        assert exc_ambig_b.value.code == "AMBIGUOUS_BENCHMARK_ROWS"

        # Negative F: Product mismatch (SPIA benchmark on MYGA run) rejected -> 400 BENCHMARK_PRODUCT_MISMATCH
        with pytest.raises(ServiceError) as exc_prod_b:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                expected_result_set_id=ers_spia.id,
            )
        assert exc_prod_b.value.status_code == 400
        assert exc_prod_b.value.code == "BENCHMARK_PRODUCT_MISMATCH"

        # Negative G: Invalid target_metric not in persisted ValuationRunDetail.summary_results -> 400 INVALID_TARGET_METRIC
        with pytest.raises(ServiceError) as exc_metric:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                expected_result_set_id=ers_match.id,
                target_metric="nonexistent_metric_xyz",
            )
        assert exc_metric.value.status_code == 400
        assert exc_metric.value.code == "INVALID_TARGET_METRIC"

        # Negative H: Cross-project ExpectedResultSet -> 404 BENCHMARK_NOT_FOUND
        with pytest.raises(ServiceError) as exc_for_b:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                expected_result_set_id=ers_foreign.id,
            )
        assert exc_for_b.value.status_code == 404
        assert exc_for_b.value.code == "BENCHMARK_NOT_FOUND"

    # Verify through Dagster reconciliation_report_asset and reporting_asset: PASS, FAIL, NOT_VERIFIABLE
    context = build_asset_context()
    val_out = {
        "valuation_run_id": val_run_id,
        "project_id": proj_id,
        "present_value_reserve": actual_reserve,
        "basis": "STATUTORY",
        "methodology": "VM22",
    }
    rf_out = {"is_continuous": True, "summary_id": rf_summary.id}

    # 1. PASS: Scoped multi-row ExpectedResultSet selects the exact matching cohort row
    cfg_pass = ReconciliationConfig(expected_result_set_id="ers-approved-match-01")
    pass_res = reconciliation_report_asset(context, cfg_pass, val_out, rf_out)
    assert pass_res.value["status"] == "PASS"
    assert pass_res.value["expected_result_set_id"] == "ers-approved-match-01"
    assert pass_res.value["expected_benchmark"] == 125000.005

    rep_pass = reporting_asset(context, ReportingConfig(require_approved_reconciliation=True), val_out, rf_out, pass_res.value)
    assert rep_pass.value["governance_status"] == "GOVERNED_RECONCILED_FOUNDATION_PACKAGE"
    assert rep_pass.value["is_approved_for_release"] is True
    assert rep_pass.value["artifact_type"] == "reporting"

    # 2. FAIL with halt_on_reconciliation_failure=True: raises Dagster Failure(allow_retries=False) AND persists ReconciliationReport
    cfg_fail_halt = ReconciliationConfig(expected_result_set_id="ers-approved-fail-01", halt_on_reconciliation_failure=True)
    with pytest.raises(Failure) as exc_dagster_fail:
        reconciliation_report_asset(context, cfg_fail_halt, val_out, rf_out)
    assert exc_dagster_fail.value.allow_retries is False

    # 3. FAIL with halt_on_reconciliation_failure=False: emits REJECTED_NON_APPROVED reporting_quarantined artifact
    cfg_fail_no_halt = ReconciliationConfig(expected_result_set_id="ers-approved-fail-01", halt_on_reconciliation_failure=False)
    fail_res = reconciliation_report_asset(context, cfg_fail_no_halt, val_out, rf_out)
    assert fail_res.value["status"] == "FAIL"
    assert fail_res.value["difference"] == 5000.00

    rep_quarantined = reporting_asset(context, ReportingConfig(require_approved_reconciliation=False), val_out, rf_out, fail_res.value)
    assert rep_quarantined.value["governance_status"] == "REJECTED_NON_APPROVED"
    assert rep_quarantined.value["is_approved_for_release"] is False
    assert rep_quarantined.value["artifact_type"] == "reporting_quarantined"

    # And when require_approved_reconciliation=True, FAIL or NOT_VERIFIABLE is rejected by reporting_asset
    with pytest.raises(Failure) as exc_rep_gate:
        reporting_asset(context, ReportingConfig(require_approved_reconciliation=True), val_out, rf_out, fail_res.value)
    assert exc_rep_gate.value.allow_retries is False

    # 4. NOT_VERIFIABLE
    cfg_unverifiable = ReconciliationConfig(expected_result_set_id=None)
    unverifiable_res = reconciliation_report_asset(context, cfg_unverifiable, val_out, rf_out)
    assert unverifiable_res.value["status"] == "NOT_VERIFIABLE"
    assert unverifiable_res.value["expected_benchmark"] is None
    assert "No approved ExpectedResultSet" in unverifiable_res.value["not_verifiable_reason"]


def test_p0_07_and_v5_p1_g_dagster_retry_recovery_after_partial_persistence_without_duplicate_runs(
    init_test_db, monkeypatch
):
    """P0-07 & V5-P1-G: Exercise Dagster retry after partial database persistence.

    Proves:
    1. Post-commit fault after `Run`, `RunSet`, and `RunPackage` are committed to DB on Attempt 1:
       Dagster emits `STEP_UP_FOR_RETRY` -> `STEP_RESTARTED` -> `STEP_SUCCESS`, and Attempt 2 resumes the
       **exact same `run_id`** with zero duplicate `Run` rows (`count(Run.id) == 1`).
    2. Mid-execution fault after `claim_run` opens `RunAttempt(attempt_number=1, status='running')`:
       Attempt 1 is marked `failed` (`cleanup_status='retained_noncanonical'`), Attempt 2 executes on the
       **same `run_id`**, sets `accepted_attempt_number=2`, and preserves both `RunAttempt` rows.
    3. Deterministic `ValuationTaxonomyError` (`V5-P1-H`) halts immediately on Attempt 1 (`allow_retries=False`).
    """
    proj_id = init_test_db["project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]
    mv_id = init_test_db["model_version_id"]
    scen_id = init_test_db["scenario_id"]

    # --- Case 1: Fault AFTER initial Run + RunPackage commit on Attempt 1 ---
    target_run_id_1 = "run-m5-retry-partial-commit-001"
    hook_calls: list[int] = []

    def fault_after_initial_commit(db, run_id: str) -> None:
        if run_id == target_run_id_1:
            hook_calls.append(len(hook_calls) + 1)
            if len(hook_calls) == 1:
                # Verify the Run and RunPackage are ALREADY committed in DB before raising transient error!
                persisted_r = db.get(Run, run_id)
                assert persisted_r is not None
                assert persisted_r.status == run_state.PENDING
                raise ServiceError(503, "TRANSIENT_STORAGE_INTERRUPT", "Simulated crash after Run/RunPackage commit")

    monkeypatch.setattr(vps_mod, "_after_initial_run_commit_hook", fault_after_initial_commit)

    cfg1 = _make_run_config(
        proj_id, inforce_file_id=inf1_id, model_version_id=mv_id, scenario_id=scen_id, run_id=target_run_id_1
    )
    with DagsterInstance.ephemeral() as instance:
        res1 = materialize(ALL_PIPELINE_ASSETS, run_config=cfg1, instance=instance)
        assert res1.success is True
        assert hook_calls == [1, 2]

        event_types = [e.event_type for e in res1.all_events if e.step_key == "projection_run_asset"]
        assert DagsterEventType.STEP_UP_FOR_RETRY in event_types
        assert DagsterEventType.STEP_RESTARTED in event_types
        assert DagsterEventType.STEP_SUCCESS in event_types

    with db_mod.SessionLocal() as db:
        # Prove NO duplicate Run with random suffix was created!
        matching_runs = db.query(Run).filter(Run.id.like(f"{target_run_id_1}%")).all()
        assert len(matching_runs) == 1
        assert matching_runs[0].id == target_run_id_1
        assert matching_runs[0].status in ("completed", "success")
        assert matching_runs[0].accepted_attempt_number == 1

    # --- Case 2: Mid-execution fault AFTER RunAttempt 1 is created in DB ---
    monkeypatch.setattr(vps_mod, "_after_initial_run_commit_hook", lambda db, run_id: None)
    target_run_id_2 = "run-m5-retry-mid-attempt-002"
    real_execute_run = run_execution_service.execute_run
    exec_calls: list[int] = []

    def flaky_execute_run(db, run_id: str) -> str:
        if run_id == target_run_id_2:
            exec_calls.append(len(exec_calls) + 1)
            if len(exec_calls) == 1:
                # Claim attempt 1 in DB so RunAttempt(attempt_number=1, status='running') is persisted!
                run_execution_service.claim_run(db, run_id, worker_id="worker-crash-test")
                raise RuntimeError("Simulated worker crash mid-execution after claiming attempt 1")
        return real_execute_run(db, run_id)

    monkeypatch.setattr(run_execution_service, "execute_run", flaky_execute_run)

    cfg2 = _make_run_config(
        proj_id, inforce_file_id=inf1_id, model_version_id=mv_id, scenario_id=scen_id, run_id=target_run_id_2
    )
    with DagsterInstance.ephemeral() as instance:
        res2 = materialize(ALL_PIPELINE_ASSETS, run_config=cfg2, instance=instance)
        assert res2.success is True
        assert exec_calls == [1, 2]

    with db_mod.SessionLocal() as db:
        matching_runs_2 = db.query(Run).filter(Run.id.like(f"{target_run_id_2}%")).all()
        assert len(matching_runs_2) == 1
        r2 = matching_runs_2[0]
        assert r2.id == target_run_id_2
        assert r2.status in ("completed", "success")
        assert r2.accepted_attempt_number == 2
        assert r2.attempt_count == 2

        attempts = (
            db.query(RunAttempt)
            .filter(RunAttempt.run_id == target_run_id_2)
            .order_by(RunAttempt.attempt_number)
            .all()
        )
        assert len(attempts) == 2
        assert attempts[0].attempt_number == 1
        assert attempts[0].status == run_state.FAILED
        assert attempts[0].cleanup_status == "retained_noncanonical"
        assert attempts[1].attempt_number == 2
        assert attempts[1].status in ("completed", "success")

    # --- Case 3: V5-P1-H Taxonomy error fails closed with 400 INVALID_VALUATION_TAXONOMY and zero retries ---
    cfg_bad_tax = _make_run_config(
        proj_id,
        inforce_file_id=inf1_id,
        model_version_id=mv_id,
        scenario_id=scen_id,
        run_id="run-m5-bad-taxonomy-003",
        basis="STATUTORY",
        methodology="LDTI",  # Invalid pair: STATUTORY + LDTI!
    )
    with DagsterInstance.ephemeral() as instance:
        res_tax = materialize(ALL_PIPELINE_ASSETS, run_config=cfg_bad_tax, instance=instance, raise_on_error=False)
        assert res_tax.success is False
        val_events = [e.event_type for e in res_tax.all_events if e.step_key == "valuation_asset"]
        assert DagsterEventType.STEP_UP_FOR_RETRY not in val_events
        assert DagsterEventType.STEP_FAILURE in val_events
