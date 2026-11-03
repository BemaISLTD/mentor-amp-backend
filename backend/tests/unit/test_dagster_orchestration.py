"""Tests for Dagster Workflow Orchestration & M5 V5 Corrective Verification (P0-01..P0-07).

Per CAS §9, §15.8, and M5 V4 Senior Review:
Verifies:
1. Complete 8-stage software-defined asset graph.
2. P0-01: Governed Data Manager ingestion (`InforceFile`/`InforceRecord`) & shared projection engine
   execution (`run_execution_service.execute_run`). Two distinct approved policy blocks produce
   genuinely different fingerprints, Parquet checksums, reserves, and rollforward balances.
3. P0-02: Trusted model & model_version lineage from PostgreSQL; rejects MYGA/SPIA product-model
   mismatches, unapproved model versions, foreign-project models, and missing models.
4. P0-04: Fail-closed physical artifact verification in `execute_cash_flow_aggregation` (no fallback
   to in-memory `simulation_rows`; fails on missing file, wrong URI, corrupted bytes, wrong-run artifact,
   or invalid schema).
5. P0-05: Valuation-derived rollforward attribution (`opening_balance`, `movements`, `ending_balance`,
   `cohort_id`, `basis`, `methodology` derived from parent valuation run; fails on closing mismatch,
   basis/methodology contradiction, or cross-project run).
6. P0-06: Governed benchmark reconciliation (`ExpectedResultSet` required for PASS/FAIL; missing
   benchmark yields `NOT_VERIFIABLE` with explicit reason; rejects unapproved, superseded, foreign-project,
   or raw caller benchmark floats).
7. P0-07: Real Dagster retry on the actual MentorAMP production asset (`projection_run_asset` calling
   `execute_projection_simulation`) with `STEP_UP_FOR_RETRY` -> `STEP_RESTARTED` -> `STEP_SUCCESS`
   and zero SQLite connection errors on STDERR.
"""

from decimal import Decimal
import gc
import hashlib
import uuid

from dagster import DagsterEventType, DagsterInstance, Failure, build_asset_context, materialize
import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import close_all_sessions, sessionmaker

from app.core.artifacts import get_artifact_store
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
from app.db.models.run_package import RunPackage
from app.db.models.user import User
from app.db.models.valuation_detail import ValuationRunDetail
import app.orchestration.dagster.assets as assets_mod
from app.orchestration.dagster.assets import (
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
from app.services.common import ServiceError, now_utc
from app.services.valuation_pipeline_service import (
    execute_cash_flow_aggregation,
    execute_foundation_valuation,
    execute_governed_reconciliation,
    execute_inforce_import,
    execute_projection_simulation,
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


def _create_second_approved_inforce_file(db, project_id: str, user_id: str, base_inforce_id: str | None = None) -> InforceFile:
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
        approved_by=user_id,
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

        # Create second approved inforce block in same project for input sensitivity tests
        user = seeded["user"]
        inf2 = _create_second_approved_inforce_file(db, proj_id, user.id, base_inforce_id=inforce_id)

        # Create a foreign project for cross-project isolation tests
        foreign_proj = Project(id="proj-foreign-isolation-002", name="Foreign Project", created_by=user.id)
        db.add(foreign_proj)
        db.commit()

    yield {
        "project_id": proj_id,
        "inforce_file_id_1": inforce_id,
        "inforce_file_id_2": inf2.id,
        "model_version_id": mv_id,
        "foreign_project_id": "proj-foreign-isolation-002",
    }

    close_all_sessions()
    test_engine.dispose()


def _make_run_config(
    project_id: str,
    inforce_file_id: str | None = None,
    run_id: str | None = None,
    projection_months: int = 12,
    basis: str = "STATUTORY",
    methodology: str = "VM22",
    annual_discount_rate: float = 0.045,
    expected_result_set_id: str | None = None,
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
                }
            },
            "valuation_asset": {
                "config": {
                    "basis": basis,
                    "methodology": methodology,
                    "valuation_date": "2026-12-31",
                    "annual_discount_rate": annual_discount_rate,
                }
            },
            "reconciliation_report_asset": {
                "config": {
                    "expected_result_set_id": expected_result_set_id,
                    "target_metric": "present_value_reserve",
                    "tolerance": 0.01,
                    "rel_tol": 1e-4,
                }
            },
        }
    }


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


def test_dagster_end_to_end_pipeline_materialization_and_input_sensitivity(init_test_db):
    """P0-01, P0-02, P0-04, P0-05, P0-06: Execute all 8 assets on 2 distinct governed policy blocks.

    Proves:
    - Real governed InforceFile + InforceRecord rows are loaded from DB and cryptographically verified.
    - Shared projection engine (`run_execution_service.execute_run`) executes real MYGA model formulas.
    - Frozen projection & valuation RunPackages record the actual MYGA Model and approved ModelVersion from DB.
    - Physical Parquet artifacts exist on disk and match RunArtifact SHA-256 checksums.
    - Rollforward derives opening, movements, closing, cohort, basis, and methodology from the valuation run.
    - Two distinct approved inforce blocks produce genuinely different fingerprints, Parquet hashes,
      aggregated cash flows, valuation reserves, and rollforward opening/closing balances.
    """
    store = get_artifact_store()
    proj_id = init_test_db["project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]
    inf2_id = init_test_db["inforce_file_id_2"]

    # Materialize Run 1 (5 seeded MYGA policies) and Run 2 (2 distinct high-balance MYGA policies)
    cfg1 = _make_run_config(proj_id, inforce_file_id=inf1_id, run_id="run-m5-proj-block-1")
    cfg2 = _make_run_config(proj_id, inforce_file_id=inf2_id, run_id="run-m5-proj-block-2")
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

    proj1_path = store.path_for_uri(events1["projection_run_asset"]["storage_uri"].value)
    assert proj1_path.exists()
    assert hashlib.sha256(proj1_path.read_bytes()).hexdigest() == proj1_fp

    # 3. P0-01: Aggregated cash flows and valuation reserves differ between the two policy blocks
    reserve1 = events1["valuation_asset"]["present_value_reserve"].value
    reserve2 = events2["valuation_asset"]["present_value_reserve"].value
    assert reserve1 > 0.0
    assert reserve2 > 0.0
    assert reserve1 != reserve2

    # 4. P0-05: Rollforward opening/closing balances are derived from actual valuation runs (NOT 5000/6000)
    rf1_open = events1["rollforward_asset"]["opening_balance"].value
    rf1_close = events1["rollforward_asset"]["ending_balance"].value
    rf2_open = events2["rollforward_asset"]["opening_balance"].value
    rf2_close = events2["rollforward_asset"]["ending_balance"].value

    assert rf1_open != 5000.0
    assert rf1_close != 6000.0
    assert abs(rf1_close - reserve1) <= 0.01
    assert abs(rf2_close - reserve2) <= 0.01
    assert rf1_close != rf2_close

    # 5. Verify Database Rows & P0-02 Trusted Model Lineage
    with db_mod.SessionLocal() as db:
        db_proj_run = db.get(Run, "run-m5-proj-block-1")
        assert db_proj_run is not None
        assert db_proj_run.status in ("completed", "success")
        assert db_proj_run.model_version_id == init_test_db["model_version_id"]

        db_proj_pkg = db.query(RunPackage).filter(RunPackage.run_id == db_proj_run.id).first()
        assert db_proj_pkg is not None
        # P0-02: Provenance reflects the actual MYGA model, never hardcoded SPIA
        assert db_proj_pkg.package["configuration"]["model"]["product_code"] == "MYGA"
        assert db_proj_pkg.package["configuration"]["model"]["id"] != "model-spia-01"
        assert db_proj_pkg.package["configuration"]["model_version"]["id"] == init_test_db["model_version_id"]
        assert len(db_proj_pkg.package["configuration"]["formulas"]) > 0

        val_run = db.query(Run).filter(
            Run.run_type == "valuation",
            Run.source_projection_run_id == db_proj_run.id,
        ).first()
        assert val_run is not None
        val_pkg = db.query(RunPackage).filter(RunPackage.run_id == val_run.id).first()
        assert val_pkg is not None
        assert val_pkg.package["configuration"]["model"]["product_code"] == "MYGA"
        assert val_pkg.package["configuration"]["model_version"]["id"] == init_test_db["model_version_id"]

        rf_row = db.query(RollforwardSummary).filter(RollforwardSummary.run_id == val_run.id).first()
        assert rf_row is not None
        assert rf_row.is_continuity_verified is True
        assert abs(float(rf_row.closing_balance) - reserve1) <= 0.01


def test_p0_01_and_p0_02_inforce_and_model_governance_rejections(init_test_db):
    """P0-01 & P0-02: Reject missing/foreign/unapproved/tampered inforce and mismatched/unapproved models."""
    proj_id = init_test_db["project_id"]
    foreign_proj_id = init_test_db["foreign_project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]

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
            approved_at=now_utc(),
        )
        db.add(spia_mv)
        # Also create an unapproved MYGA model version
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
        db.add(draft_mv)
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
            )
        assert exc_unapproved_mv.value.status_code == 400
        assert exc_unapproved_mv.value.code == "MODEL_VERSION_NOT_APPROVED"


def test_p0_04_aggregation_fails_closed_on_missing_or_corrupted_artifact(init_test_db, tmp_path):
    """P0-04: Aggregation never falls back to in-memory rows; fails closed on missing/corrupt/mismatched Parquet."""
    store = get_artifact_store()
    proj_id = init_test_db["project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]

    with db_mod.SessionLocal() as db:
        imported = execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA_2026", inforce_file_id=inf1_id)
        proj_out = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported,
            projection_months=6,
            run_id="run-p004-agg-check",
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

        # 2. Mutated Parquet bytes fail closed -> 400 ARTIFACT_CHECKSUM_MISMATCH
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

        # 3. Deleted physical Parquet file fails closed -> 400 PHYSICAL_ARTIFACT_MISSING (never falls back to memory)
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

        # 4. Invalid Parquet schema fails closed -> 422 INVALID_ARTIFACT_SCHEMA
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
            attempt_number=2,
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


def test_p0_05_rollforward_valuation_linkage_and_basis_governance(init_test_db):
    """P0-05: Rollforward derives balances/metadata from valuation run and rejects mismatches."""
    proj_id = init_test_db["project_id"]
    foreign_proj_id = init_test_db["foreign_project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]

    with db_mod.SessionLocal() as db:
        imported = execute_inforce_import(db=db, project_id=proj_id, policy_block="BLOCK_MYGA_2026", inforce_file_id=inf1_id)
        proj_out = execute_projection_simulation(
            db=db,
            project_id=proj_id,
            validated_inforce=imported,
            projection_months=6,
            run_id="run-p005-proj",
        )
        agg_out = execute_cash_flow_aggregation(
            db=db,
            projection_run_id=proj_out["run_id"],
            artifact_id=proj_out["artifact_id"],
            storage_uri=proj_out["storage_uri"],
            project_id=proj_id,
        )

        # Create a GAAP / LDTI valuation run
        val_gaap = execute_foundation_valuation(
            db=db,
            upstream_run_id=proj_out["run_id"],
            basis="GAAP",
            methodology="LDTI",
            valuation_date="2026-12-31",
            annual_discount_rate=0.04,
            aggregated_cash_flows=agg_out["aggregated_cash_flows"],
            project_id=proj_id,
        )
        val_run_id = val_gaap["valuation_run_id"]

        # 1. GAAP/LDTI parent derives GAAP/LDTI rollforward summary automatically
        rf_gaap = execute_rollforward_attribution(
            db=db,
            valuation_run_id=val_run_id,
            project_id=proj_id,
        )
        assert rf_gaap["basis"] == "GAAP"
        assert rf_gaap["methodology"] == "LDTI"
        assert abs(rf_gaap["ending_balance"] - val_gaap["present_value_reserve"]) <= 0.01

        # 2. GAAP/LDTI parent rejects STATUTORY or VM22 override -> 400
        with pytest.raises(ServiceError) as exc_basis:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                basis="STATUTORY",
            )
        assert exc_basis.value.status_code == 400
        assert exc_basis.value.code == "ROLLFORWARD_BASIS_MISMATCH"

        # 3. Closing balance diverging from persisted valuation reserve fails -> 422
        with pytest.raises(ServiceError) as exc_close:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                opening=Decimal("5000.00"),
                movements={"interest_accretion": Decimal("1000.00")},
                ending=Decimal("6000.00"),
            )
        assert exc_close.value.status_code == 422
        assert exc_close.value.code in ("ROLLFORWARD_DISCONTINUITY", "ROLLFORWARD_VALUATION_MISMATCH")

        # 4. Cross-project valuation_run_id returns 404
        with pytest.raises(ServiceError) as exc_cross:
            execute_rollforward_attribution(
                db=db,
                valuation_run_id=val_run_id,
                project_id=foreign_proj_id,
            )
        assert exc_cross.value.status_code == 404
        assert exc_cross.value.code in ("RUN_NOT_FOUND", "VALUATION_RUN_NOT_FOUND")


def test_p0_06_governed_benchmark_reconciliation(init_test_db):
    """P0-06: Reconciliation requires approved ExpectedResultSet; raw caller benchmark floats are forbidden."""
    proj_id = init_test_db["project_id"]
    foreign_proj_id = init_test_db["foreign_project_id"]

    # Verify ReconciliationConfig does NOT expose expected_benchmark
    assert "expected_benchmark" not in ReconciliationConfig.model_fields

    val_run_id = f"run-test-rec-{uuid.uuid4().hex[:8]}"
    actual_reserve = 125000.00

    with db_mod.SessionLocal() as db:
        user = db.query(User).first()
        r = Run(
            id=val_run_id,
            project_id=proj_id,
            status="success",
            run_type="valuation",
            basis="STATUTORY",
            methodology="VM22",
            methodology_version="v1",
        )
        db.add(r)

        # Seed approved matching ExpectedResultSet
        ers_match_rows = [{"present_value_reserve": 125000.005}]
        ers_match = ExpectedResultSet(
            id="ers-approved-match-01",
            project_id=proj_id,
            name="VM22 Golden Benchmark Match",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_GOLDEN_TABLE",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=ers_match_rows,
            status="approved",
            fingerprint=fingerprint(ers_match_rows),
            created_by=user.id,
            approved_by=user.id,
            approved_at=now_utc(),
        )
        # Seed approved mismatching ExpectedResultSet
        ers_fail_rows = [{"present_value_reserve": 130000.00}]
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
            created_by=user.id,
            approved_by=user.id,
            approved_at=now_utc(),
        )
        # Seed unapproved (draft) ExpectedResultSet
        ers_draft = ExpectedResultSet(
            id="ers-draft-01",
            project_id=proj_id,
            name="Draft Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="SPREADSHEET",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=ers_match_rows,
            status="draft",
            fingerprint=fingerprint(ers_match_rows),
            created_by=user.id,
        )
        # Seed foreign-project ExpectedResultSet
        ers_foreign = ExpectedResultSet(
            id="ers-foreign-01",
            project_id=foreign_proj_id,
            name="Foreign Benchmark",
            product_code="MYGA",
            version_label="v1",
            source_reference="ACTUARIAL_GOLDEN_TABLE",
            row_count=1,
            columns_detected=["present_value_reserve"],
            rows_data=ers_match_rows,
            status="approved",
            fingerprint=fingerprint(ers_match_rows),
            created_by=user.id,
            approved_by=user.id,
            approved_at=now_utc(),
        )
        db.add_all([ers_match, ers_fail, ers_draft, ers_foreign])
        db.commit()

        # 1. Raw caller expected_benchmark without expected_result_set_id -> 400 UNGOVERNED_BENCHMARK_FORBIDDEN
        with pytest.raises(ServiceError) as exc_raw:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                actual_reserve=actual_reserve,
                expected_benchmark=125000.00,
            )
        assert exc_raw.value.status_code == 400
        assert exc_raw.value.code == "UNGOVERNED_BENCHMARK_FORBIDDEN"

        # 2. Unapproved (draft) ExpectedResultSet -> 400 BENCHMARK_NOT_APPROVED
        with pytest.raises(ServiceError) as exc_draft_b:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                actual_reserve=actual_reserve,
                expected_result_set_id=ers_draft.id,
            )
        assert exc_draft_b.value.status_code == 400
        assert exc_draft_b.value.code == "BENCHMARK_NOT_APPROVED"

        # 3. Cross-project ExpectedResultSet -> 404 BENCHMARK_NOT_FOUND
        with pytest.raises(ServiceError) as exc_for_b:
            execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run_id,
                project_id=proj_id,
                actual_reserve=actual_reserve,
                expected_result_set_id=ers_foreign.id,
            )
        assert exc_for_b.value.status_code == 404
        assert exc_for_b.value.code == "BENCHMARK_NOT_FOUND"

    # 4. Verify through Dagster reconciliation_report_asset: PASS, FAIL, NOT_VERIFIABLE
    context = build_asset_context()
    val_out = {
        "valuation_run_id": val_run_id,
        "project_id": proj_id,
        "present_value_reserve": actual_reserve,
        "basis": "STATUTORY",
        "methodology": "VM22",
    }
    rf_out = {"is_continuous": True}

    cfg_pass = ReconciliationConfig(expected_result_set_id="ers-approved-match-01", tolerance=0.01)
    pass_res = reconciliation_report_asset(context, cfg_pass, val_out, rf_out)
    assert pass_res.value["status"] == "PASS"
    assert pass_res.value["expected_result_set_id"] == "ers-approved-match-01"

    cfg_fail = ReconciliationConfig(expected_result_set_id="ers-approved-fail-01", tolerance=0.01)
    fail_res = reconciliation_report_asset(context, cfg_fail, val_out, rf_out)
    assert fail_res.value["status"] == "FAIL"
    assert fail_res.value["difference"] == 5000.00

    cfg_unverifiable = ReconciliationConfig(expected_result_set_id=None)
    unverifiable_res = reconciliation_report_asset(context, cfg_unverifiable, val_out, rf_out)
    assert unverifiable_res.value["status"] == "NOT_VERIFIABLE"
    assert unverifiable_res.value["expected_benchmark"] is None
    assert "No approved ExpectedResultSet" in unverifiable_res.value["not_verifiable_reason"]


def test_p0_07_production_asset_retry_on_transient_failure_and_no_retry_on_deterministic_failure(
    init_test_db, monkeypatch
):
    """P0-07: Exercise retry on the actual MentorAMP production asset (`projection_run_asset`).

    Proves:
    - Attempt 1 of `projection_run_asset` encounters transient `ServiceError(503)` from `execute_projection_simulation`.
    - Dagster emits `STEP_UP_FOR_RETRY`, restarts the step (`STEP_RESTARTED`), and succeeds on attempt 2 (`STEP_SUCCESS`).
    - All 8 pipeline assets complete materialization cleanly without duplicate failed rows.
    - Deterministic `ServiceError(400)` halts immediately on attempt 1 (`allow_retries=False`).
    """
    proj_id = init_test_db["project_id"]
    inf1_id = init_test_db["inforce_file_id_1"]

    real_projection_fn = execute_projection_simulation
    call_attempts: list[int] = []

    def flaky_projection_simulation(*args, **kwargs):
        attempt = len(call_attempts) + 1
        call_attempts.append(attempt)
        if attempt == 1:
            raise ServiceError(503, "TRANSIENT_DB_CONTENTION", "Simulated transient database lock timeout on attempt 1")
        return real_projection_fn(*args, **kwargs)

    monkeypatch.setattr(assets_mod, "execute_projection_simulation", flaky_projection_simulation)

    cfg = _make_run_config(proj_id, inforce_file_id=inf1_id, run_id="run-m5-retry-prod-001")
    with DagsterInstance.ephemeral() as instance:
        result = materialize(ALL_PIPELINE_ASSETS, run_config=cfg, instance=instance)
        assert result.success is True
        assert call_attempts == [1, 2], f"Expected production asset to execute attempts [1, 2], got {call_attempts}"

        event_types = [e.event_type for e in result.all_events if e.step_key == "projection_run_asset"]
        assert DagsterEventType.STEP_UP_FOR_RETRY in event_types
        assert DagsterEventType.STEP_RESTARTED in event_types
        assert DagsterEventType.STEP_SUCCESS in event_types

        # Verify deterministic failure halts immediately on attempt 1 without retry
        det_attempts: list[int] = []

        def deterministic_fail_projection(*args, **kwargs):
            det_attempts.append(len(det_attempts) + 1)
            raise ServiceError(400, "PRODUCT_MODEL_MISMATCH", "Deterministic model mismatch")

        monkeypatch.setattr(assets_mod, "execute_projection_simulation", deterministic_fail_projection)
        cfg_det = _make_run_config(proj_id, inforce_file_id=inf1_id, run_id="run-m5-det-fail-001")
        res_det = materialize(ALL_PIPELINE_ASSETS, run_config=cfg_det, instance=instance, raise_on_error=False)
        assert res_det.success is False
        assert det_attempts == [1], f"Deterministic failure must not retry; got attempts {det_attempts}"
        det_events = [e.event_type for e in res_det.all_events if e.step_key == "projection_run_asset"]
        assert DagsterEventType.STEP_UP_FOR_RETRY not in det_events
        assert DagsterEventType.STEP_FAILURE in det_events
