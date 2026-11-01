"""M5 PostgreSQL Governance & Immutability Verification.

Runs only when MENTORAMP_TEST_POSTGRES_URL points to a disposable PostgreSQL database.

Verifies:
1. Zero -> Head upgrade on PostgreSQL (c9f4e2d8a615).
2. Downgrade c9f4e2d8a615 -> b8f3d1e7c504 and re-upgrade to head.
3. Foreign key constraints:
   - source_model_id -> models.id
   - source_model_version_id -> model_versions.id
4. Immutability trigger on derived_datasets (Directive 8):
   - Approved dataset cannot be deleted.
   - Approved dataset material fields cannot be mutated.
   - Superseded dataset cannot be deleted.
   - Superseded dataset material fields cannot be mutated.
   - Superseded dataset status cannot be reverted.
5. Immutability trigger on valuation_run_details (Directive 9):
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
from sqlalchemy import create_engine, text

from app.config import settings

RAW_URL = os.environ.get("MENTORAMP_TEST_POSTGRES_URL", "postgresql://bema_erp:bema_erp_local_dev_only@localhost:5432/mentoramp_m5_disposable_test")
pytestmark = pytest.mark.skipif(
    not os.environ.get("MENTORAMP_TEST_POSTGRES_URL"),
    reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database"
)

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

    # 1. Reset public schema
    conn = psycopg2.connect(postgres_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
        cur.execute("CREATE SCHEMA public;")
        cur.execute("GRANT ALL ON SCHEMA public TO bema_erp;")
        cur.execute("GRANT ALL ON SCHEMA public TO public;")
    conn.close()

    # 2. Run Alembic upgrade zero -> head
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", MIGRATIONS_DIR)
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_url)
    
    command.upgrade(alembic_cfg, "head")

    # 3. Test Downgrade and Re-upgrade
    command.downgrade(alembic_cfg, "b8f3d1e7c504")
    command.upgrade(alembic_cfg, "head")

    # 4. Verify Foreign Key constraints (Directive 13)
    engine = create_engine(postgres_url)
    u_id = str(uuid.uuid4())
    p_id = str(uuid.uuid4())
    m_id = str(uuid.uuid4())
    mv_id = str(uuid.uuid4())
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
            text("INSERT INTO runs (id, project_id, status, run_type, basis, methodology, created_at) VALUES (:id, :pid, 'success', 'valuation', 'STATUTORY', 'VM22', NOW())"),
            {"id": run_id, "pid": p_id},
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

    # 4a. Invalid source_model_id -> FK violation
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
                    "mid": str(uuid.uuid4()),  # Nonexistent model
                    "mvid": mv_id,
                },
            )
            trans.commit()
            raise AssertionError("Invalid source_model_id did not fail FK check!")
        except Exception as e:
            trans.rollback()
            assert "foreign key constraint" in str(e).lower()

    # 4b. Invalid source_model_version_id -> FK violation
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
                    "mvid": str(uuid.uuid4()),  # Nonexistent model version
                },
            )
            trans.commit()
            raise AssertionError("Invalid source_model_version_id did not fail FK check!")
        except Exception as e:
            trans.rollback()
            assert "foreign key constraint" in str(e).lower()

    # 5. Verify Immutability Trigger on derived_datasets (Directive 8)
    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET status = 'approved', approved_by = :u, approved_at = NOW() WHERE id = :id"),
            {"id": ds_id, "u": u_id},
        )

    # 5a. Attempt to DELETE approved dataset -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM derived_datasets WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Approved dataset deletion was not blocked by PostgreSQL trigger!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be deleted" in str(e)

    # 5b. Attempt to MUTATE material field (fingerprint) on approved dataset -> MUST FAIL
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

    # 5c. Attempt exploit: set status = 'superseded' WHILE tampering fingerprint -> MUST FAIL
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

    # 5d. Legitimate supersede: change ONLY lifecycle status -> SUCCEEDS
    with engine.begin() as c:
        c.execute(
            text("UPDATE derived_datasets SET status = 'superseded', updated_at = NOW() WHERE id = :id"),
            {"id": ds_id},
        )

    # 5e. Attempt to DELETE superseded dataset -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM derived_datasets WHERE id = :id"), {"id": ds_id})
            trans.commit()
            raise AssertionError("Superseded dataset deletion was not blocked by PostgreSQL trigger!")
        except Exception as e:
            trans.rollback()
            assert "Approved or superseded derived_datasets cannot be deleted" in str(e)

    # 5f. Attempt to MUTATE material field on superseded dataset -> MUST FAIL
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

    # 5g. Attempt to REVERT superseded status back to approved -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(
                text("UPDATE derived_datasets SET status = 'approved' WHERE id = :id"),
                {"id": ds_id},
            )
            trans.commit()
            raise AssertionError("Reverting superseded status was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "Superseded derived_datasets status cannot be changed" in str(e)

    # 6. Verify Immutability Trigger on valuation_run_details (Directive 9)
    with engine.begin() as c:
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

    # 6a. Attempt to DELETE valuation_run_details -> MUST FAIL
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("DELETE FROM valuation_run_details WHERE run_id = :rid"), {"rid": run_id})
            trans.commit()
            raise AssertionError("valuation_run_details delete was not blocked!")
        except Exception as e:
            trans.rollback()
            assert "valuation_run_details execution evidence cannot be deleted" in str(e)

    # 6b. Attempt to UPDATE valuation_run_details on completed run -> MUST FAIL
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
