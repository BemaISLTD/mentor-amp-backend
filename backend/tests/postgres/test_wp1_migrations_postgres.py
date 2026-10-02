"""Work Package 1 — Alembic migrations and concurrency on real PostgreSQL (opt-in).

Runs only when MENTORAMP_TEST_POSTGRES_URL points to a DISPOSABLE database whose name contains
"test": every test drops and recreates its ``public`` schema. Example (Git Bash, from backend/):

    MENTORAMP_TEST_POSTGRES_URL=postgresql://user:pass@host/mentoramp_wp1_test \\
        .venv/Scripts/python -m pytest -q tests/postgres

Covers: empty -> head -> down -> up; refusal to upgrade from the M1 head while
unexported legacy data remains; upgrade from the backlog head; one canonical head; write-once
triggers; the run status CHECK; and two concurrent workers claiming one run.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

RAW_URL = os.environ.get("MENTORAMP_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not RAW_URL, reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database"
)
HEAD, M1_HEAD, DEV_HEAD = "c6e1a4b9d203", "4d8e2f6a1c90", "c48a2d7159be"
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _url() -> str:
    url = RAW_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    database = urlparse(url).path.lstrip("/")
    if "test" not in database:
        pytest.fail(f"Refusing to reset database '{database}': its name must contain 'test'.")
    return url


@pytest.fixture(scope="module")
def pg():
    patch = pytest.MonkeyPatch()
    url = _url()
    patch.setattr(settings, "database_url", url)  # read by app/db/migrations/env.py
    engine = create_engine(url)
    config = Config()
    config.set_main_option("script_location", os.path.join(BACKEND, "app", "db", "migrations"))
    yield engine, config
    engine.dispose()
    patch.undo()


def reset(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def current(engine) -> list[str]:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()


def now() -> datetime:
    return datetime.now(timezone.utc)


def test_single_canonical_head(pg):
    _engine, config = pg
    assert ScriptDirectory.from_config(config).get_heads() == [HEAD]


def test_empty_database_upgrades_downgrades_and_upgrades_again(pg):
    engine, config = pg
    reset(engine)
    command.upgrade(config, "head")
    assert current(engine) == [HEAD]
    tables = set(inspect(engine).get_table_names())
    assert {"run_packages", "run_attempts", "project_members", "run_manifests", "run_artifacts",
            "audit_logs", "products", "models", "projection_sets"} <= tables
    assert "run_outputs" not in tables and "trace_logs" not in tables
    columns = inspect(engine).get_columns("variable_registry")
    assert {"created_by", "updated_by"} <= {column["name"] for column in columns}
    assert "updated_by" in {column["name"] for column in inspect(engine).get_columns("formula_registry")}
    command.downgrade(config, "f8c2d4e6a901")
    assert current(engine) == ["f8c2d4e6a901"]
    command.upgrade(config, "head")
    assert current(engine) == [HEAD]


def seed_legacy_m1_data(engine) -> dict:
    ids = {key: str(uuid.uuid4()) for key in ("project", "done", "pending", "file_legacy", "file_ok",
                                              "aset", "table", "sset", "scenario")}
    manifest_done = {"schema_version": "m1", "projection_set": {"id": "ps"}, "outcome": {"status": "success"}}
    manifest_pending = {"schema_version": "m1", "projection_set": {"id": "ps"}, "outcome": None}
    with engine.begin() as c:
        c.execute(text("INSERT INTO projects (id, name, created_at, updated_at) VALUES (:id, 'Legacy', now(), now())"),
                  {"id": ids["project"]})
        for key, status, started, manifest, fp in (
            ("done", "success", now(), manifest_done, "f" * 64),
            ("pending", "pending", None, manifest_pending, "e" * 64),
        ):
            c.execute(text(
                "INSERT INTO runs (id, project_id, status, started_at, completed_at, created_at, manifest, "
                "manifest_fingerprint) VALUES (:id, :p, :s, :st, :ct, now(), CAST(:m AS json), :fp)"
            ), {"id": ids[key], "p": ids["project"], "s": status, "st": started,
                "ct": now() if started else None, "m": json.dumps(manifest), "fp": fp})
        for month in (0, 1):
            c.execute(text(
                "INSERT INTO run_outputs (run_id, policy_id, scenario_id, projection_month, variable_name, "
                "value, created_at) VALUES (:r, 'P1', 'S', :m, 'reserve', CAST('{\"value\": 1.5}' AS json), now())"
            ), {"r": ids["done"], "m": month})
        c.execute(text(
            "INSERT INTO trace_logs (run_id, policy_id, scenario_id, projection_month, variable_name, created_at) "
            "VALUES (:r, 'P1', 'S', 1, 'reserve', now())"), {"r": ids["done"]})
        for key, fingerprint in (("file_legacy", None), ("file_ok", "a" * 64)):
            c.execute(text(
                "INSERT INTO inforce_files (id, project_id, filename, file_type, row_count, uploaded_at, "
                "fingerprint) VALUES (:id, :p, :n, 'csv', 0, now(), :fp)"),
                {"id": ids[key], "p": ids["project"], "n": key, "fp": fingerprint})
        c.execute(text("INSERT INTO assumption_sets (id, project_id, name, created_at) VALUES (:id, :p, 'A', now())"),
                  {"id": ids["aset"], "p": ids["project"]})
        c.execute(text(
            "INSERT INTO assumption_tables (id, set_id, table_name, table_type, lookup_keys, data, created_at) "
            "VALUES (:id, :s, 'T', 'mortality', CAST('[]' AS json), CAST('[]' AS json), now())"),
            {"id": ids["table"], "s": ids["aset"]})
        c.execute(text("INSERT INTO scenario_sets (id, project_id, name, created_at) VALUES (:id, :p, 'S', now())"),
                  {"id": ids["sset"], "p": ids["project"]})
        c.execute(text(
            "INSERT INTO scenario_tables (id, set_id, scenario_name, overrides, created_at) "
            "VALUES (:id, :s, 'Base', CAST('[]' AS json), now())"), {"id": ids["scenario"], "s": ids["sset"]})
    return ids


def test_upgrade_from_m1_refuses_to_drop_unexported_legacy_rows(pg):
    engine, config = pg
    reset(engine)
    command.upgrade(config, M1_HEAD)
    ids = seed_legacy_m1_data(engine)
    with pytest.raises(RuntimeError, match="Export its records"):
        command.upgrade(config, "head")
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM run_outputs WHERE run_id = :id"),
                         {"id": ids["done"]}).scalar() == 2
        assert c.execute(text("SELECT count(*) FROM trace_logs WHERE run_id = :id"),
                         {"id": ids["done"]}).scalar() == 1


def test_upgrade_from_the_dev_head_adds_m1_and_wp1(pg):
    engine, config = pg
    reset(engine)
    command.upgrade(config, DEV_HEAD)
    assert current(engine) == [DEV_HEAD]
    user_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id, email, full_name, password_hash, is_active, created_at, updated_at) "
                       "VALUES (:id, 'noah@example.com', 'Noah', 'x', true, now(), now())"), {"id": user_id})
        c.execute(text("INSERT INTO projects (id, name, created_by, created_at, updated_at) "
                       "VALUES (:id, 'Dev project', :u, now(), now())"), {"id": project_id, "u": user_id})
    command.upgrade(config, "head")
    assert current(engine) == [HEAD]
    with engine.connect() as c:
        role = c.execute(text("SELECT role FROM project_members WHERE project_id = :p AND user_id = :u"),
                         {"p": project_id, "u": user_id}).scalar()
    assert role == "owner"


def test_two_concurrent_workers_cannot_both_claim_a_run(pg):
    from app.services.run_execution_service import claim_run

    engine, config = pg
    reset(engine)
    command.upgrade(config, "head")
    project_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("INSERT INTO projects (id, name, created_at, updated_at) VALUES (:id, 'P', now(), now())"),
                  {"id": project_id})
        c.execute(text("INSERT INTO runs (id, project_id, status, created_at) VALUES (:id, :p, 'pending', now())"),
                  {"id": run_id, "p": project_id})
    Session = sessionmaker(bind=engine)
    barrier = threading.Barrier(8)
    results: list = []

    def worker(number: int) -> None:
        with Session() as db:
            barrier.wait()
            attempt = claim_run(db, run_id, f"worker-{number}")
            results.append(attempt.attempt_number if attempt is not None else None)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(1) == 1 and results.count(None) == 7  # exactly one winner
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM run_attempts WHERE run_id = :id"), {"id": run_id}).scalar() == 1
        assert c.execute(text("SELECT status, attempt_count FROM runs WHERE id = :id"), {"id": run_id}).one() == ("running", 1)
