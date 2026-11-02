"""M5 PostgreSQL Governance, Immutability & Downgrade Fidelity Verification.

Runs only when MENTORAMP_TEST_POSTGRES_URL points to a disposable PostgreSQL database.

Verifies:
1. Zero -> Head upgrade on PostgreSQL (c9f4e2d8a615).
2. Schema inspection of head c9:
   - derived_datasets has source_model_version_id
   - valuation_run_details has redundant identity columns dropped
3. Downgrade c9f4e2d8a615 -> b8f3d1e7c504 with full fidelity inspection (Directive 5):
   - valuation_run_details columns, NOT NULL nullability, FKs, check constraints, and indexes restored
   - derived_datasets source_model_version_id dropped
   - prior b8 trigger behavior verified
4. Re-upgrade b8f3d1e7c504 -> c9f4e2d8a615.
5. Foreign key constraints:
   - source_model_id -> models.id
   - source_model_version_id -> model_versions.id
6. Hardened immutability triggers on derived_datasets (Directives 4 & 8):
   - Approved dataset cannot be deleted.
   - Approved dataset material fields cannot be mutated.
   - Status transitions strictly restricted:
     - approved -> draft BLOCKED
     - approved -> validated BLOCKED
     - approved -> arbitrary status BLOCKED
     - approved -> superseded ALLOWED
   - Terminal superseded status:
     - superseded -> approved BLOCKED
     - superseded -> draft BLOCKED
     - superseded material mutation BLOCKED
     - superseded deletion BLOCKED
   - Soft-delete protection:
     - deleted_at / deleted_by on approved or superseded BLOCKED
7. Immutability trigger on valuation_run_details (Directive 9):
   - Delete is blocked.
   - Update is blocked once parent Run is in completed/success status.
"""

import os
import sys
import uuid
from urllib.parse import urlparse

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import settings

RAW_URL = os.environ.get("MENTORAMP_TEST_POSTGRES_URL", "postgresql://bema_erp:bema_erp_local_dev_only@localhost:5432/mentoramp_m5_disposable_test")
pytestmark = pytest.mark.skipif(
    not os.environ.get("MENTORAMP_TEST_POSTGRES_URL"),
    reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database"
)

MIGRATIONS_DIR = os.path.join(BACKEND, "app", "db", "migrations")


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
        cur.execute("GRANT ALL ON SCHEMA public TO bema_erp;")
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
    # Redundant columns must be absent in c9
    assert "project_id" not in c9_vrd_cols, "c9 valuation_run_details must NOT have redundant project_id"
    assert "basis" not in c9_vrd_cols, "c9 valuation_run_details must NOT have redundant basis"
    assert "methodology" not in c9_vrd_cols, "c9 valuation_run_details must NOT have redundant methodology"

    # 3. Seed REAL production data under c9 BEFORE downgrade
    u_id = str(uuid.uuid4())
    p_id = str(uuid.uuid4())
    m_id = str(uuid.uuid4())
    mv_id = str(uuid.uuid4())
    proj_run_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    ds_id = str(uuid.uuid4())

    with engine.begin() as c:
        c.execute(
            text("INSERT INTO users (id, email, full_name, password_hash, is_active, created_at, updated_at) VALUES (:id, :email, :name, :hash, true, NOW(), NOW())"),
            {"id": u_id, "email": "actuary@test.com", "name": "Test Actuary", "hash": "h"},
        )
        c.execute(
            text("INSERT INTO projects (id, name, created_by, created_at, updated_at) VALUES (:id, :name, :user, NOW(), NOW())"),
            {"id": p_id, "name": "M5 PG Project", "user": u_id},
        )
        c.execute(
            text("INSERT INTO models (id, project_id, name, product_code, created_at, updated_at) VALUES (:id, :pid, :name, :pc, NOW(), NOW())"),
            {"id": m_id, "pid": p_id, "name": "Test Model", "pc": "SPIA"},
        )
        c.execute(
            text("INSERT INTO model_versions (id, model_id, version_label, basis, methodology, created_at, updated_at) VALUES (:id, :mid, :lbl, 'STATUTORY', 'VM22', NOW(), NOW())"),
            {"id": mv_id, "mid": m_id, "lbl": "v1"},
        )
        c.execute(
            text("INSERT INTO runs (id, project_id, status, run_type, created_at) VALUES (:id, :pid, 'success', 'projection', NOW())"),
            {"id": proj_run_id, "pid": p_id},
        )
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

    # 4. Test Downgrade c9 -> b8 WITH REAL DATA (Directive 5 / Blocker 3)
    command.downgrade(alembic_cfg, "b8f3d1e7c504")

    inspector_b8 = inspect(engine)
    b8_vrd_cols = {col["name"]: col for col in inspector_b8.get_columns("valuation_run_details")}

    # Verify restored columns
    for col_name in ["project_id", "basis", "methodology", "methodology_version", "source_projection_run_id", "derived_dataset_id"]:
        assert col_name in b8_vrd_cols, f"Downgraded b8 valuation_run_details missing column: {col_name}"

    # Verify restored nullability (project_id, basis, methodology, methodology_version must be NOT NULL)
    assert b8_vrd_cols["project_id"]["nullable"] is False, "b8 project_id must be NOT NULL"
    assert b8_vrd_cols["basis"]["nullable"] is False, "b8 basis must be NOT NULL"
    assert b8_vrd_cols["methodology"]["nullable"] is False, "b8 methodology must be NOT NULL"
    assert b8_vrd_cols["methodology_version"]["nullable"] is False, "b8 methodology_version must be NOT NULL"

    # Verify restored foreign keys on valuation_run_details
    b8_vrd_fks = {fk["referred_table"]: fk for fk in inspector_b8.get_foreign_keys("valuation_run_details")}
    assert "projects" in b8_vrd_fks, "b8 valuation_run_details missing FK to projects"
    assert "runs" in b8_vrd_fks, "b8 valuation_run_details missing FK to runs"
    assert "derived_datasets" in b8_vrd_fks, "b8 valuation_run_details missing FK to derived_datasets"

    # Verify restored check constraints on valuation_run_details
    b8_vrd_checks = {ck["name"] for ck in inspector_b8.get_check_constraints("valuation_run_details")}
    assert "ck_valuation_basis" in b8_vrd_checks, "b8 valuation_run_details missing ck_valuation_basis"
    assert "ck_valuation_methodology" in b8_vrd_checks, "b8 valuation_run_details missing ck_valuation_methodology"

    # Verify restored indexes on valuation_run_details
    b8_vrd_indexes = {ix["name"] for ix in inspector_b8.get_indexes("valuation_run_details")}
    assert "ix_valuation_run_details_project_id" in b8_vrd_indexes
    assert "ix_valuation_run_details_source_projection_run_id" in b8_vrd_indexes

    # PROVE: valuation_run_details row survived downgrade and backfilled correctly from parent run
    with engine.connect() as c:
        row = c.execute(
            text("SELECT project_id, source_projection_run_id, basis, methodology, methodology_version FROM valuation_run_details WHERE run_id = :rid"),
            {"rid": run_id},
        ).fetchone()
        assert row is not None, "valuation_run_details row did NOT survive downgrade!"
        assert row.project_id == p_id, f"Backfilled project_id mismatch: {row.project_id} vs {p_id}"
        assert row.source_projection_run_id == proj_run_id, f"Backfilled source_projection_run_id mismatch: {row.source_projection_run_id} vs {proj_run_id}"
        assert row.basis == "STATUTORY", f"Backfilled basis mismatch: {row.basis}"
        assert row.methodology == "VM22", f"Backfilled methodology mismatch: {row.methodology}"
        assert row.methodology_version in ("v1.0", "v1"), f"Backfilled methodology_version mismatch: {row.methodology_version}"

    # Verify source_model_version_id was removed from derived_datasets on downgrade
    b8_ds_cols = {col["name"]: col for col in inspector_b8.get_columns("derived_datasets")}
    assert "source_model_version_id" not in b8_ds_cols, "b8 derived_datasets must NOT have source_model_version_id"

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

    # 5. Re-upgrade b8 -> c9
    command.upgrade(alembic_cfg, "head")

    # Verify c9 schema is restored
    inspector_c9_again = inspect(engine)
    c9_ds_cols_again = {col["name"]: col for col in inspector_c9_again.get_columns("derived_datasets")}
    assert "source_model_version_id" in c9_ds_cols_again

    # Re-link source_model_version_id on test dataset after re-upgrade
    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET source_model_version_id = :mvid WHERE id = :id"),
            {"id": ds_id, "mvid": mv_id},
        )

    # 6. Verify Foreign Key constraints

    # 5a. Invalid source_model_id -> FK violation
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

    # 5b. Invalid source_model_version_id -> FK violation
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

    # 6. Verify Hardened Immutability Triggers on derived_datasets (Directives 4 & 8)
    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET status = 'approved', approved_by = :u, approved_at = NOW() WHERE id = :id"),
            {"id": ds_id, "u": u_id},
        )

    # 6a. Attempt to DELETE approved dataset -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM derived_datasets WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Approved dataset deletion was not blocked by PostgreSQL trigger!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be deleted" in str(e)

    # 6b. Attempt to MUTATE material field on approved dataset -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(
                text("UPDATE derived_datasets SET fingerprint = 'HACKED_FP' WHERE id = :id"),
                {"id": ds_id},
            )
            trans.commit()
            raise AssertionError("Approved dataset mutation was not blocked by PostgreSQL trigger!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets material evidence is immutable" in str(e)

    # 6c. Attempt exploit: set status = 'superseded' WHILE tampering fingerprint -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(
                text("UPDATE derived_datasets SET status = 'superseded', fingerprint = 'HACKED_FP' WHERE id = :id"),
                {"id": ds_id},
            )
            trans.commit()
            raise AssertionError("Exploit changing status=superseded + fingerprint was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets material evidence is immutable" in str(e)

    # 6d. Status transition loophole tests (Directive 4):
    # - approved -> draft BLOCKED
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET status = 'draft' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("approved -> draft was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets status can only remain approved or transition to superseded" in str(e)

    # - approved -> validated BLOCKED
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET status = 'validated' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("approved -> validated was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets status can only remain approved or transition to superseded" in str(e)

    # - approved -> arbitrary unknown status BLOCKED
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET status = 'unknown_status' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("approved -> unknown status was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets status can only remain approved or transition to superseded" in str(e)

    # - soft-delete on approved dataset BLOCKED (Directive 4)
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET deleted_at = NOW() WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("soft-delete deleted_at on approved dataset was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be soft-deleted" in str(e)

    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET deleted_by = :u WHERE id = :id"), {"id": ds_id, "u": u_id})
            trans.commit()
            raise AssertionError("soft-delete deleted_by on approved dataset was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be soft-deleted" in str(e)

    # 6e. Legitimate supersession: change ONLY lifecycle status -> SUCCEEDS
    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET status = 'superseded', updated_at = NOW() WHERE id = :id"),
            {"id": ds_id},
        )

    # 6f. Attempt to DELETE superseded dataset -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM derived_datasets WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Superseded dataset deletion was not blocked by PostgreSQL trigger!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be deleted" in str(e)

    # 6g. Attempt to MUTATE material field on superseded dataset -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(
                text("UPDATE derived_datasets SET storage_uri = '/tampered/path' WHERE id = :id"),
                {"id": ds_id},
            )
            trans.commit()
            raise AssertionError("Superseded dataset mutation was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Approved derived_datasets material evidence is immutable" in str(e)

    # 6h. Attempt to REVERT superseded status back to approved -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET status = 'approved' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Reverting superseded status was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Superseded derived_datasets status cannot be changed" in str(e)

    # 6i. Attempt to REVERT superseded status back to draft -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("UPDATE derived_datasets SET status = 'draft' WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Reverting superseded status to draft was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Superseded derived_datasets status cannot be changed" in str(e)

    # 7. Verify Immutability Trigger on valuation_run_details (Directive 9)
    # The valuation_run_details row survived downgrade c9->b8 and re-upgrade b8->c9
    # 7a. Attempt to DELETE valuation_run_details -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM valuation_run_details WHERE run_id = :rid"), {"rid": run_id})
            trans.commit()
            raise AssertionError("valuation_run_details delete was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "valuation_run_details execution evidence cannot be deleted" in str(e)

    # 7b. Attempt to UPDATE valuation_run_details on completed run -> MUST FAIL
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


def test_m5_postgres_governance_and_immutability():
    run_full_m5_postgres_verification()


if __name__ == "__main__":
    run_full_m5_postgres_verification()
    print("ALL POSTGRESQL VERIFICATION CHECKS PASSED WITH 100% SUCCESS!")
