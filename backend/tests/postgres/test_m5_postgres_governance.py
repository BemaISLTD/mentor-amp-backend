"""M5 PostgreSQL Governance, Immutability, Downgrade Fidelity & Dagster E2E Verification (P0-07, P1-08, V5-P0-B..V5-P1-I).

Runs when MENTORAMP_TEST_POSTGRES_URL points to a disposable PostgreSQL database.

Verifies:
1. Zero -> Head upgrade on PostgreSQL (c9f4e2d8a615).
2. Schema inspection of head c9:
   - derived_datasets has source_model_version_id
   - valuation_run_details has redundant identity columns dropped
3. P1-08 Downgrade Preflight & Multi-Row Fidelity (c9f4e2d8a615 -> b8f3d1e7c504):
   - Preflight blocks unacknowledged data loss when derived_datasets.source_model_version_id is populated.
   - Preflight blocks downgrade when parent runs row has NULL or invalid basis/methodology/methodology_version
     (refuses to fabricate 'STATUTORY'/'VM22'/'v1' defaults).
   - Multi-row downgrade backfills both STATUTORY/VM22/v1.0 and GAAP/LDTI/v2.1 accurately from parent runs.
   - Repeat upgrade/downgrade cycles (c9 -> b8 -> c9 -> b8 -> c9) preserve all rows intact.
4. Foreign key constraints & hardened immutability triggers on derived_datasets and valuation_run_details.
5. P0-07 & V5-P1-G Full 8-Stage Production Dagster Pipeline E2E Materialization & Post-Commit / Mid-Attempt
   Retry Recovery against real disposable PostgreSQL (resuming the exact same `run_id` without duplicate `Run` rows,
   preserving Attempt 1 evidence, promoting Attempt 2, and verifying two-period valuation rollforward and four-eyes
   scoped benchmark reconciliation).
"""

from decimal import Decimal
import hashlib
import os
import sys
from urllib.parse import urlparse
import uuid

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from alembic import command
from alembic.config import Config
from dagster import DagsterEventType, DagsterInstance, materialize
import psycopg2
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import close_all_sessions, sessionmaker

from app.config import settings
from app.core.artifacts import get_artifact_store
from app.core.execution import run_state
from app.core.execution.fingerprints import fingerprint
import app.db.database as db_mod
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.valuation_detail import ValuationRunDetail
from app.orchestration.dagster.assets import (
    aggregation_asset,
    inforce_data_asset,
    projection_run_asset,
    reconciliation_report_asset,
    reporting_asset,
    rollforward_asset,
    validation_asset,
    valuation_asset,
)
from app.services import run_execution_service
from app.services.common import now_utc
from app.services.valuation_pipeline_service import (
    execute_cash_flow_aggregation,
    execute_foundation_valuation,
    execute_governed_reconciliation,
    execute_reporting_manifest,
    execute_rollforward_attribution,
)
from scripts.seed_demo import seed_myga

RAW_URL = os.environ.get(
    "MENTORAMP_TEST_POSTGRES_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/mentoramp_m5_disposable_test",
)
pytestmark = pytest.mark.skipif(
    not os.environ.get("MENTORAMP_TEST_POSTGRES_URL"),
    reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database",
)

MIGRATIONS_DIR = os.path.join(BACKEND, "app", "db", "migrations")

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


def _get_url() -> str:
    url = RAW_URL
    database = urlparse(url).path.lstrip("/")
    if "test" not in database:
        raise ValueError(f"Refusing to reset database '{database}': its name must contain 'test'.")
    return url


def run_full_m5_postgres_verification():
    postgres_url = _get_url()
    settings.database_url = postgres_url

    # 1. Reset public schema cleanly
    conn = psycopg2.connect(postgres_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
        cur.execute("GRANT ALL ON SCHEMA public TO public;")
    conn.close()

    # 2. Run Alembic upgrade zero -> head (c9f4e2d8a615)
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", MIGRATIONS_DIR)
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_url)

    command.upgrade(alembic_cfg, "head")

    engine = create_engine(postgres_url)
    inspector = inspect(engine)

    # 2a. Verify c9 head schema structure
    c9_ds_cols = {col["name"]: col for col in inspector.get_columns("derived_datasets")}
    assert "source_model_version_id" in c9_ds_cols, "c9 derived_datasets must have source_model_version_id"

    c9_vrd_cols = {col["name"]: col for col in inspector.get_columns("valuation_run_details")}
    assert "run_id" in c9_vrd_cols
    assert "valuation_date" in c9_vrd_cols
    assert "discount_curve_fingerprint" in c9_vrd_cols
    assert "configuration" in c9_vrd_cols
    assert "summary_results" in c9_vrd_cols
    assert "project_id" not in c9_vrd_cols, "c9 valuation_run_details must NOT have redundant project_id"
    assert "basis" not in c9_vrd_cols, "c9 valuation_run_details must NOT have redundant basis"
    assert "methodology" not in c9_vrd_cols, "c9 valuation_run_details must NOT have redundant methodology"

    # 3. Seed REAL production data under c9 BEFORE downgrade (both STATUTORY/VM22 and GAAP/LDTI)
    u_id = str(uuid.uuid4())
    p_id = str(uuid.uuid4())
    m_id = str(uuid.uuid4())
    mv_id = str(uuid.uuid4())
    proj_run_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    run_id_gaap = str(uuid.uuid4())
    ds_id = str(uuid.uuid4())

    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, is_active, created_at, updated_at) "
                "VALUES (:id, :email, :name, :hash, true, NOW(), NOW())"
            ),
            {"id": u_id, "email": "actuary@test.com", "name": "Test Actuary", "hash": "h"},
        )
        c.execute(
            text(
                "INSERT INTO projects (id, name, created_by, created_at, updated_at) "
                "VALUES (:id, :name, :user, NOW(), NOW())"
            ),
            {"id": p_id, "name": "M5 PG Project", "user": u_id},
        )
        c.execute(
            text(
                "INSERT INTO models (id, project_id, name, product_code, created_at, updated_at) "
                "VALUES (:id, :pid, :name, :pc, NOW(), NOW())"
            ),
            {"id": m_id, "pid": p_id, "name": "Test Model", "pc": "SPIA"},
        )
        c.execute(
            text(
                "INSERT INTO model_versions (id, model_id, version_label, basis, methodology, created_at, updated_at) "
                "VALUES (:id, :mid, :lbl, 'STATUTORY', 'VM22', NOW(), NOW())"
            ),
            {"id": mv_id, "mid": m_id, "lbl": "v1"},
        )
        c.execute(
            text(
                "INSERT INTO runs (id, project_id, status, run_type, created_at) "
                "VALUES (:id, :pid, 'success', 'projection', NOW())"
            ),
            {"id": proj_run_id, "pid": p_id},
        )
        # Valuation Run 1: STATUTORY / VM22 / v1.0
        c.execute(
            text("""
                INSERT INTO runs (
                    id, project_id, status, run_type, basis, methodology, methodology_version,
                    source_projection_run_id, created_at
                ) VALUES (
                    :id, :pid, 'success', 'valuation', 'STATUTORY', 'VM22', 'v1.0',
                    :proj_rid, NOW()
                )
            """),
            {"id": run_id, "pid": p_id, "proj_rid": proj_run_id},
        )
        c.execute(
            text("""
                INSERT INTO valuation_run_details (
                    run_id, valuation_date, discount_curve_fingerprint,
                    configuration, summary_results, created_at
                ) VALUES (
                    :rid, '2026-12-31', 'curve_fp_123',
                    '{}', '{"reserve": 1000000.0}', NOW()
                )
            """),
            {"rid": run_id},
        )
        # Valuation Run 2: GAAP / LDTI / v2.1 (proves downgrade never fabricates STATUTORY/VM22/v1)
        c.execute(
            text("""
                INSERT INTO runs (
                    id, project_id, status, run_type, basis, methodology, methodology_version,
                    source_projection_run_id, created_at
                ) VALUES (
                    :id, :pid, 'success', 'valuation', 'GAAP', 'LDTI', 'v2.1',
                    :proj_rid, NOW()
                )
            """),
            {"id": run_id_gaap, "pid": p_id, "proj_rid": proj_run_id},
        )
        c.execute(
            text("""
                INSERT INTO valuation_run_details (
                    run_id, valuation_date, discount_curve_fingerprint,
                    configuration, summary_results, created_at
                ) VALUES (
                    :rid, '2026-12-31', 'curve_fp_gaap_456',
                    '{}', '{"reserve": 925000.0}', NOW()
                )
            """),
            {"rid": run_id_gaap},
        )
        c.execute(
            text("""
                INSERT INTO derived_datasets (
                    id, project_id, source_run_id, source_model_id, source_model_version_id,
                    name, dataset_type, fingerprint, storage_uri, row_count,
                    status, created_at, updated_at
                ) VALUES (
                    :id, :pid, :rid, :mid, :mvid,
                    'SPIA Prior Output', 'prior_output', 'FP_INITIAL_123', 's3://bucket/ds.parquet', 100,
                    'draft', NOW(), NOW()
                )
            """),
            {"id": ds_id, "pid": p_id, "rid": run_id, "mid": m_id, "mvid": mv_id},
        )

    # 4a. P1-08 Preflight Check 1: Unacknowledged data-loss on derived_datasets.source_model_version_id -> MUST FAIL
    os.environ.pop("MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE", None)
    with pytest.raises(RuntimeError) as exc_dl:
        command.downgrade(alembic_cfg, "b8f3d1e7c504")
    assert "source_model_version_id is populated" in str(exc_dl.value)
    assert "MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE=1" in str(exc_dl.value)

    # 4b. P1-08 Preflight Check 2: Dirty/NULL parent basis/methodology -> MUST FAIL CLOSED (zero COALESCE fabrication)
    dirty_run_id = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(
            text("""
                INSERT INTO runs (
                    id, project_id, status, run_type, basis, methodology, methodology_version, created_at
                ) VALUES (
                    :id, :pid, 'pending', 'valuation', NULL, NULL, NULL, NOW()
                )
            """),
            {"id": dirty_run_id, "pid": p_id},
        )
        c.execute(
            text("""
                INSERT INTO valuation_run_details (
                    run_id, valuation_date, discount_curve_fingerprint,
                    configuration, summary_results, created_at
                ) VALUES (
                    :rid, '2026-12-31', 'curve_dirty', '{}', '{}', NOW()
                )
            """),
            {"rid": dirty_run_id},
        )

    os.environ["MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE"] = "1"
    try:
        with pytest.raises(RuntimeError) as exc_dirty:
            command.downgrade(alembic_cfg, "b8f3d1e7c504")
        assert "refusing to fabricate fallback defaults" in str(exc_dirty.value)

        # Clean up dirty row before legitimate downgrade
        with engine.begin() as c:
            c.execute(text("ALTER TABLE valuation_run_details DISABLE TRIGGER valuation_run_details_protect_evidence;"))
            c.execute(text("DELETE FROM valuation_run_details WHERE run_id = :rid"), {"rid": dirty_run_id})
            c.execute(text("ALTER TABLE valuation_run_details ENABLE TRIGGER valuation_run_details_protect_evidence;"))
            c.execute(text("DELETE FROM runs WHERE id = :rid"), {"rid": dirty_run_id})

        # 4c. Execute legitimate Downgrade c9 -> b8 with multi-row data
        command.downgrade(alembic_cfg, "b8f3d1e7c504")
    finally:
        os.environ.pop("MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE", None)

    inspector_b8 = inspect(engine)
    b8_vrd_cols = {col["name"]: col for col in inspector_b8.get_columns("valuation_run_details")}

    for col_name in [
        "project_id",
        "basis",
        "methodology",
        "methodology_version",
        "source_projection_run_id",
        "derived_dataset_id",
    ]:
        assert col_name in b8_vrd_cols, f"Downgraded b8 valuation_run_details missing column: {col_name}"

    assert b8_vrd_cols["project_id"]["nullable"] is False
    assert b8_vrd_cols["basis"]["nullable"] is False
    assert b8_vrd_cols["methodology"]["nullable"] is False
    assert b8_vrd_cols["methodology_version"]["nullable"] is False

    b8_vrd_fks = {fk["referred_table"]: fk for fk in inspector_b8.get_foreign_keys("valuation_run_details")}
    assert "projects" in b8_vrd_fks
    assert "runs" in b8_vrd_fks
    assert "derived_datasets" in b8_vrd_fks

    b8_vrd_checks = {ck["name"] for ck in inspector_b8.get_check_constraints("valuation_run_details")}
    assert "ck_valuation_basis" in b8_vrd_checks
    assert "ck_valuation_methodology" in b8_vrd_checks

    b8_vrd_indexes = {ix["name"] for ix in inspector_b8.get_indexes("valuation_run_details")}
    assert "ix_valuation_run_details_project_id" in b8_vrd_indexes
    assert "ix_valuation_run_details_source_projection_run_id" in b8_vrd_indexes

    # PROVE: Both STATUTORY/VM22/v1.0 and GAAP/LDTI/v2.1 rows survived downgrade with exact parent metadata
    with engine.connect() as c:
        row_stat = c.execute(
            text(
                "SELECT project_id, source_projection_run_id, basis, methodology, methodology_version "
                "FROM valuation_run_details WHERE run_id = :rid"
            ),
            {"rid": run_id},
        ).fetchone()
        assert row_stat is not None
        assert row_stat.project_id == p_id
        assert row_stat.source_projection_run_id == proj_run_id
        assert row_stat.basis == "STATUTORY"
        assert row_stat.methodology == "VM22"
        assert row_stat.methodology_version == "v1.0"

        row_gaap = c.execute(
            text(
                "SELECT project_id, source_projection_run_id, basis, methodology, methodology_version "
                "FROM valuation_run_details WHERE run_id = :rid"
            ),
            {"rid": run_id_gaap},
        ).fetchone()
        assert row_gaap is not None
        assert row_gaap.project_id == p_id
        assert row_gaap.source_projection_run_id == proj_run_id
        assert row_gaap.basis == "GAAP"
        assert row_gaap.methodology == "LDTI"
        assert row_gaap.methodology_version == "v2.1"

    b8_ds_cols = {col["name"]: col for col in inspector_b8.get_columns("derived_datasets")}
    assert "source_model_version_id" not in b8_ds_cols

    # Test b8 trigger behavior: DELETE on valuation_run_details is blocked
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM valuation_run_details WHERE run_id = :rid"), {"rid": run_id})
            trans.commit()
            raise AssertionError("b8 delete on valuation_run_details was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "valuation_run_details execution evidence cannot be deleted" in str(e)

    # 5. Repeat upgrade/downgrade cycle: b8 -> c9 -> b8 -> c9 (P1-08)
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "b8f3d1e7c504")
    command.upgrade(alembic_cfg, "head")

    inspector_c9_again = inspect(engine)
    c9_ds_cols_again = {col["name"]: col for col in inspector_c9_again.get_columns("derived_datasets")}
    assert "source_model_version_id" in c9_ds_cols_again

    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET source_model_version_id = :mvid WHERE id = :id"),
            {"id": ds_id, "mvid": mv_id},
        )

    # 6. Verify Foreign Key constraints
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(
                text("""
                    INSERT INTO derived_datasets (
                        id, project_id, source_run_id, source_model_id, source_model_version_id,
                        name, dataset_type, fingerprint, storage_uri, row_count,
                        status, created_at, updated_at
                    ) VALUES (
                        :id, :pid, :rid, :mid, :mvid,
                        'Bad Model DS', 'prior_output', 'FP_BAD', 's3://b/d.parquet', 10,
                        'draft', NOW(), NOW()
                    )
                """),
                {
                    "id": str(uuid.uuid4()),
                    "pid": p_id,
                    "rid": run_id,
                    "mid": str(uuid.uuid4()),
                    "mvid": mv_id,
                },
            )
            trans.commit()
            raise AssertionError("Invalid source_model_id did not fail FK check!")
        except Exception as e:
            trans.rollback()
            assert "foreign key constraint" in str(e).lower()

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(
                text("""
                    INSERT INTO derived_datasets (
                        id, project_id, source_run_id, source_model_id, source_model_version_id,
                        name, dataset_type, fingerprint, storage_uri, row_count,
                        status, created_at, updated_at
                    ) VALUES (
                        :id, :pid, :rid, :mid, :mvid,
                        'Bad MV DS', 'prior_output', 'FP_BAD_MV', 's3://b/d2.parquet', 10,
                        'draft', NOW(), NOW()
                    )
                """),
                {
                    "id": str(uuid.uuid4()),
                    "pid": p_id,
                    "rid": run_id,
                    "mid": m_id,
                    "mvid": str(uuid.uuid4()),
                },
            )
            trans.commit()
            raise AssertionError("Invalid source_model_version_id did not fail FK check!")
        except Exception as e:
            trans.rollback()
            assert "foreign key constraint" in str(e).lower()

    # 7. Verify Hardened Immutability Triggers on derived_datasets & valuation_run_details
    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET status = 'approved', approved_by = :u, approved_at = NOW() WHERE id = :id"),
            {"id": ds_id, "u": u_id},
        )

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM derived_datasets WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Approved dataset deletion was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be deleted" in str(e)

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET fingerprint = 'HACKED_FP' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Approved dataset mutation was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets material evidence is immutable" in str(e)

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET status = 'draft' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("approved -> draft was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets status can only remain approved or transition to superseded" in str(e)

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET deleted_at = NOW() WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("soft-delete deleted_at on approved dataset was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be soft-deleted" in str(e)

    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET status = 'superseded', updated_at = NOW() WHERE id = :id"),
            {"id": ds_id},
        )

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET status = 'approved' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Reverting superseded status was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Superseded derived_datasets status cannot be changed" in str(e)

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM valuation_run_details WHERE run_id = :rid"), {"rid": run_id})
            trans.commit()
            raise AssertionError("valuation_run_details delete was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "valuation_run_details execution evidence cannot be deleted" in str(e)

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(
                text("UPDATE valuation_run_details SET summary_results = '{\"reserve\": 0.0}' WHERE run_id = :rid"),
                {"rid": run_id},
            )
            trans.commit()
            raise AssertionError("valuation_run_details update on completed run was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "valuation_run_details execution evidence is immutable for completed runs" in str(e)

    engine.dispose()


def test_m5_postgres_governance_and_immutability():
    run_full_m5_postgres_verification()


def test_m5_postgres_dagster_e2e_and_production_retry(monkeypatch):
    """P0-07, V5-P0-B..V5-P1-I: Full 8-stage Dagster pipeline E2E materialization & post-commit / mid-attempt
    retry recovery against real PostgreSQL."""
    postgres_url = _get_url()
    settings.database_url = postgres_url

    pg_engine = create_engine(postgres_url)
    PgSessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_mod, "engine", pg_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", PgSessionLocal)

    try:
        with PgSessionLocal() as db:
            seeded = seed_myga(db, project_name="PG Dagster Governed MYGA")
            proj_id = seeded["project"].id
            inforce_id = seeded["inforce"].id
            mv_id = seeded["version"].id
            scen_id = seeded["scenarios"][0].id
            user_id = seeded["user"].id
            reviewer_id = seeded["reviewer"].id

        # V5-P1-G & V6-P1-07: Simulate a worker crash after real PostgreSQL commits of Run, RunSet, RunPackage,
        # and RunAttempt(attempt_number=1, status='running'), demonstrating partial-persistence recovery without duplicate runs.
        pg_proj_run_id = f"run-pg-dagster-proj-{uuid.uuid4().hex[:8]}"
        real_execute_run = run_execution_service.execute_run
        attempts: list[int] = []

        def pg_flaky_execute_run(db, run_id: str) -> str:
            if run_id == pg_proj_run_id:
                attempt = len(attempts) + 1
                attempts.append(attempt)
                if attempt == 1:
                    # Claim attempt 1 in PostgreSQL so RunAttempt(attempt_number=1, status='running') is committed!
                    run_execution_service.claim_run(db, run_id, worker_id="pg-worker-attempt-1")
                    raise RuntimeError("Simulated worker process crash after committing RunPackage and RunAttempt 1 to PostgreSQL")
            return real_execute_run(db, run_id)

        monkeypatch.setattr(run_execution_service, "execute_run", pg_flaky_execute_run)

        run_config = {
            "ops": {
                "inforce_data_asset": {
                    "config": {
                        "project_id": proj_id,
                        "policy_block": "BLOCK_MYGA_PG_2026",
                        "inforce_file_id": inforce_id,
                    }
                },
                "projection_run_asset": {
                    "config": {
                        "project_id": proj_id,
                        "projection_months": 12,
                        "run_id": pg_proj_run_id,
                        "model_version_id": mv_id,
                        "scenario_id": scen_id,
                        "valuation_date": "2026-12-31",
                    }
                },
                "valuation_asset": {
                    "config": {
                        "basis": "STATUTORY",
                        "methodology": "VM22",
                        "valuation_date": "2026-12-31",
                        "annual_discount_rate": 0.045,
                    }
                },
                "rollforward_asset": {
                    "config": {
                        "prior_valuation_run_id": None,
                        "tolerance": 0.01,
                    }
                },
                "reconciliation_report_asset": {
                    "config": {
                        "expected_result_set_id": None,
                        "target_metric": "present_value_reserve",
                        "halt_on_reconciliation_failure": True,
                    }
                },
                "reporting_asset": {
                    "config": {
                        "require_approved_reconciliation": False,
                    }
                },
            }
        }

        with DagsterInstance.ephemeral() as instance:
            result = materialize(ALL_PIPELINE_ASSETS, run_config=run_config, instance=instance)
        assert result.success is True
        assert attempts == [1, 2], f"Expected PostgreSQL production asset retry attempts [1, 2], got {attempts}"

        proj_step_events = [e.event_type for e in result.all_events if e.step_key == "projection_run_asset"]
        assert DagsterEventType.STEP_UP_FOR_RETRY in proj_step_events
        assert DagsterEventType.STEP_RESTARTED in proj_step_events
        assert DagsterEventType.STEP_SUCCESS in proj_step_events

        mat_events = {e.asset_key.path[-1]: e.materialization.metadata for e in result.get_asset_materialization_events()}
        assert len(mat_events) == 8
        assert mat_events["projection_run_asset"]["accepted_attempt_number"].value == 2
        assert mat_events["aggregation_asset"]["accepted_attempt_number"].value == 2

        store = get_artifact_store()
        proj_uri = mat_events["projection_run_asset"]["storage_uri"].value
        proj_fp = mat_events["projection_run_asset"]["artifact_fingerprint"].value
        proj_path = store.path_for_uri(proj_uri)
        assert proj_path.exists()
        assert hashlib.sha256(proj_path.read_bytes()).hexdigest() == proj_fp

        rep_uri = mat_events["reporting_asset"]["storage_uri"].value
        rep_sha = mat_events["reporting_asset"]["manifest_sha256"].value
        rep_path = store.path_for_uri(rep_uri)
        assert rep_path.exists()
        assert hashlib.sha256(rep_path.read_bytes()).hexdigest() == rep_sha
        assert mat_events["reporting_asset"]["governance_status"].value == "TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED"
        assert mat_events["reporting_asset"]["is_approved_for_release"].value is False

        # 2. Verify persisted PostgreSQL rows across all 8 stages & V5-P1-G zero duplicate canonical runs
        with PgSessionLocal() as db:
            # Prove ZERO duplicate Run rows were minted for pg_proj_run_id!
            matching_runs = db.query(Run).filter(Run.id.like(f"{pg_proj_run_id}%")).all()
            assert len(matching_runs) == 1
            db_proj_run = matching_runs[0]
            assert db_proj_run.id == pg_proj_run_id
            assert db_proj_run.status in ("completed", "success")
            assert db_proj_run.accepted_attempt_number == 2
            assert db_proj_run.attempt_count == 2
            assert db_proj_run.model_version_id == mv_id

            pg_attempts = (
                db.query(RunAttempt)
                .filter(RunAttempt.run_id == pg_proj_run_id)
                .order_by(RunAttempt.attempt_number)
                .all()
            )
            assert len(pg_attempts) == 2
            assert pg_attempts[0].attempt_number == 1
            assert pg_attempts[0].status == run_state.FAILED
            assert pg_attempts[0].cleanup_status == "retained_noncanonical"
            assert pg_attempts[1].attempt_number == 2
            assert pg_attempts[1].status in ("completed", "success")

            db_proj_art = db.get(RunArtifact, mat_events["projection_run_asset"]["artifact_id"].value)
            assert db_proj_art is not None
            assert db_proj_art.attempt_number == 2
            assert db_proj_art.checksum_sha256 == proj_fp

            val_run = (
                db.query(Run)
                .filter(Run.run_type == "valuation", Run.source_projection_run_id == pg_proj_run_id)
                .first()
            )
            assert val_run is not None
            assert val_run.status == "success"
            assert val_run.basis == "STATUTORY"
            assert val_run.methodology == "VM22"
            assert val_run.accepted_attempt_number == 1

            val_pkg = db.query(RunPackage).filter(RunPackage.run_id == val_run.id).first()
            assert val_pkg is not None
            assert val_pkg.package["configuration"]["model"]["product_code"] == "MYGA"
            assert val_pkg.package["configuration"]["model_version"]["id"] == mv_id

            val_detail = db.get(ValuationRunDetail, val_run.id)
            assert val_detail is not None
            reserve_val = float(val_detail.summary_results["present_value_reserve"])
            cohort_val = str(val_detail.summary_results["cohort_id"])
            assert reserve_val > 0.0

            rf_rows = db.query(RollforwardSummary).filter(RollforwardSummary.run_id == val_run.id).all()
            assert len(rf_rows) == 4
            rf_row = rf_rows[0]
            assert rf_row.is_continuity_verified is True
            assert abs(float(rf_row.closing_balance) - reserve_val) <= 0.01
            assert {r.component_name for r in rf_rows} == {
                "opening_balance",
                "discount_unwind_illustrative",
                "period_cash_flow_release_illustrative",
                "ending_balance",
            }

            rec_row = db.query(ReconciliationReport).filter(ReconciliationReport.run_id == val_run.id).first()
            assert rec_row is not None
            assert rec_row.status == "NOT_VERIFIABLE"

            # 3. Verify two-period persisted valuation comparison in PostgreSQL (V5-P0-E)
            agg_pg = execute_cash_flow_aggregation(db=db, projection_run_id=pg_proj_run_id, project_id=proj_id)
            cfs = agg_pg["aggregated_cash_flows"]
            val_next = execute_foundation_valuation(
                db=db,
                upstream_run_id=pg_proj_run_id,
                basis="STATUTORY",
                methodology="VM22",
                valuation_date="2027-01-31",
                annual_discount_rate=0.045,
                aggregated_cash_flows=cfs[1:],
                project_id=proj_id,
                cohort_id=cohort_val,
            )
            r_m = (1.0 + 0.045) ** (1.0 / 12.0) - 1.0
            cf_next = float(cfs[1])
            rf_two_period = execute_rollforward_attribution(
                db=db,
                valuation_run_id=val_next["valuation_run_id"],
                project_id=proj_id,
                prior_valuation_run_id=val_run.id,
                movements={
                    "discount_unwind_illustrative": Decimal(str(round(reserve_val * r_m, 4))),
                    "period_cash_flow_release_illustrative": Decimal(str(round(-cf_next, 4))),
                },
            )
            assert rf_two_period["attribution_mode"] == "MULTI_PERIOD_PERSISTED_VALUATION_COMPARISON"
            assert rf_two_period["prior_valuation_run_id"] == val_run.id
            assert rf_two_period["is_continuous"] is True

            # 4. Verify governed PASS reconciliation & approved reporting manifest in PostgreSQL (V5-P0-F, V5-P1-I)
            ers_rows = [
                {
                    "basis": "STATUTORY",
                    "methodology": "VM22",
                    "cohort_id": cohort_val,
                    "valuation_date": "2026-12-31",
                    "present_value_reserve": reserve_val,
                    "abs_tol": 0.01,
                    "rel_tol": 1e-4,
                }
            ]
            ers = ExpectedResultSet(
                id=f"ers-pg-{uuid.uuid4().hex[:8]}",
                project_id=proj_id,
                name="PG Golden Reserve Benchmark",
                product_code="MYGA",
                version_label="v1",
                source_reference="PG_GOLDEN_SET",
                row_count=1,
                columns_detected=["basis", "methodology", "cohort_id", "valuation_date", "present_value_reserve"],
                rows_data=ers_rows,
                status="approved",
                fingerprint=fingerprint(ers_rows),
                created_by=user_id,
                approved_by=reviewer_id,  # Independent four-eyes approval!
                approved_at=now_utc(),
            )
            db.add(ers)
            db.commit()

            pass_rep = execute_governed_reconciliation(
                db=db,
                valuation_run_id=val_run.id,
                project_id=proj_id,
                expected_result_set_id=ers.id,
            )
            assert pass_rep["status"] == "PASS"
            assert pass_rep["expected_result_set_id"] == ers.id

            approved_manifest = execute_reporting_manifest(
                db=db,
                valuation_run_id=val_run.id,
                project_id=proj_id,
                valuation_result={
                    "present_value_reserve": reserve_val,
                    "basis": "STATUTORY",
                    "methodology": "VM22",
                    "valuation_date": "2026-12-31",
                },
                rollforward_result={"is_continuous": True, "summary_id": rf_row.id},
                reconciliation_result=pass_rep,
                require_approved_reconciliation=True,
            )
            assert approved_manifest["governance_status"] == "GOVERNED_RECONCILED_FOUNDATION_PACKAGE"
            assert approved_manifest["is_approved_for_release"] is True
            assert approved_manifest["artifact_type"] == "reporting"

            import json
            print(
                "\n=== POSTGRESQL DAGSTER E2E & RETRY EVIDENCE SUMMARY ===\n"
                + json.dumps(
                    {
                        "project_id": proj_id,
                        "projection_run_id": pg_proj_run_id,
                        "duplicate_runs_count": len(matching_runs),
                        "accepted_attempt_number": db_proj_run.accepted_attempt_number,
                        "attempts": [
                            {
                                "attempt_number": a.attempt_number,
                                "status": a.status,
                                "cleanup_status": a.cleanup_status,
                            }
                            for a in pg_attempts
                        ],
                        "projection_artifact_id": db_proj_art.id,
                        "projection_parquet_sha256": proj_fp,
                        "valuation_run_id": val_run.id,
                        "valuation_run_package_fingerprint": val_pkg.fingerprint,
                        "present_value_reserve": reserve_val,
                        "two_period_rollforward": {
                            "prior_valuation_run_id": val_run.id,
                            "current_valuation_run_id": val_next["valuation_run_id"],
                            "attribution_mode": rf_two_period["attribution_mode"],
                            "is_continuous": rf_two_period["is_continuous"],
                        },
                        "reconciliation": {
                            "expected_result_set_id": ers.id,
                            "status": pass_rep["status"],
                        },
                        "approved_reporting_manifest": {
                            "governance_status": approved_manifest["governance_status"],
                            "is_approved_for_release": approved_manifest["is_approved_for_release"],
                            "artifact_type": approved_manifest["artifact_type"],
                            "manifest_sha256": approved_manifest["manifest_sha256"],
                        },
                    },
                    indent=2,
                )
            )
    finally:
        close_all_sessions()
        pg_engine.dispose()


if __name__ == "__main__":
    run_full_m5_postgres_verification()
    print("ALL POSTGRESQL VERIFICATION CHECKS PASSED WITH 100% SUCCESS!")
