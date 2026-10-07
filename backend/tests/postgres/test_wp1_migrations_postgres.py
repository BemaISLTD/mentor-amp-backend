"""Work Package 1 — Alembic migrations and concurrency on real PostgreSQL (opt-in).

Runs only when MENTORAMP_TEST_POSTGRES_URL points to a DISPOSABLE database whose name contains
"test": every test drops and recreates its ``public`` schema. Example (Git Bash, from backend/):

    MENTORAMP_TEST_POSTGRES_URL=postgresql://user:pass@host/mentoramp_wp1_test \\
        .venv/Scripts/python -m pytest -q tests/postgres

Covers: empty -> head -> down -> up; refusal to upgrade from the M1 head while
unexported legacy data remains; upgrade from the backlog head; one canonical head; write-once
triggers, retained evidence, and two concurrent workers claiming one run.
"""

import hashlib
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
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.config import settings

RAW_URL = os.environ.get("MENTORAMP_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not RAW_URL, reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database"
)
HEAD, WP1_HEAD, MERGE = "f3b8d6a1e240", "b7e4d2a9c613", "a3c5e7f9b1d2"
M1_HEAD, DEV_HEAD = "4d8e2f6a1c90", "c48a2d7159be"
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
    assert "model_variable_definitions" in tables
    command.downgrade(config, "f8c2d4e6a901")
    assert set(current(engine)) == {"f8c2d4e6a901", "c9e2a4b6d8f1"}
    command.upgrade(config, "head")
    assert current(engine) == [HEAD]


@pytest.mark.parametrize("branch_head", ["c6e1a4b9d203", "c9e2a4b6d8f1"])
def test_either_branch_head_upgrades_to_the_join_without_losing_projects(pg, branch_head):
    engine, config = pg
    reset(engine)
    command.upgrade(config, branch_head)
    project_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects (id, name, created_at, updated_at) "
                 "VALUES (:id, 'Retained project', now(), now())"),
            {"id": project_id},
        )
    command.upgrade(config, "head")
    assert current(engine) == [HEAD]
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT name FROM projects WHERE id = :id"), {"id": project_id}
        ).scalar_one() == "Retained project"


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


# =============================================================================
# WP1 correction pass
# =============================================================================

def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sequence(items) -> str:
    digest = hashlib.sha256(b"[")
    for index, item in enumerate(items):
        if index:
            digest.update(b",")
        digest.update(_canonical(item).encode("utf-8"))
    digest.update(b"]")
    return digest.hexdigest()


def seed_spia_like_m1_data(engine) -> dict:
    """What neondb holds at the M1 head: a model version, formulas, registry variables, a
    scenario overriding the discount rate, a Projection Set and a v1-fingerprinted inforce file."""
    ids = {key: str(uuid.uuid4()) for key in ("project", "model", "version", "formula", "sset", "base", "low",
                                               "ps", "file", "bad_file")}
    variables = {
        "monthly_payment": ("input", {"type": "input", "column": "monthly_payment"}),
        "survival_probability": ("formula", {"type": "formula"}),
        "expected_payment": ("formula", {"type": "formula"}),
        "discount_rate_annual": ("manual", {"type": "manual", "value": 0.045}),
        "reserve": ("output", {"type": "valuation", "cash_flow": "expected_payment", "rate": "discount_rate_annual"}),
        "unrelated": ("manual", {"type": "manual", "value": 1}),
    }
    records = [("P2", {"policy_id": "P2", "monthly_payment": "900"}), ("P1", {"policy_id": "P1", "monthly_payment": "1650"})]
    with engine.begin() as c:
        c.execute(text("INSERT INTO projects (id, name, created_at, updated_at) VALUES (:id, 'SPIA', now(), now())"),
                  {"id": ids["project"]})
        c.execute(text("INSERT INTO models (id, project_id, name, product_code, status, created_at, updated_at) "
                       "VALUES (:id, :p, 'SPIA model', 'SPIA', 'validated', now(), now())"),
                  {"id": ids["model"], "p": ids["project"]})
        c.execute(text("INSERT INTO model_versions (id, model_id, version_label, basis, status, created_at, updated_at) "
                       "VALUES (:id, :m, 'v0.1', 'Illustrative', 'validated', now(), now())"),
                  {"id": ids["version"], "m": ids["model"]})
        for name, (kind, source) in variables.items():
            c.execute(text(
                "INSERT INTO variable_registry (id, name, data_type, source_type, lookup_keys, default_value, required, "
                "product_applicability, basis_applicability, version, created_at, updated_at, source) VALUES "
                "(:id, :n, 'number', :k, CAST('[]' AS json), CAST(:d AS json), true, ARRAY['SPIA']::varchar[], "
                "ARRAY[]::varchar[], 'v1', now(), now(), CAST(:s AS json))"
            ), {"id": str(uuid.uuid4()), "n": name, "k": kind, "s": json.dumps(source),
                "d": json.dumps({"value": source["value"]}) if "value" in source else None})
        c.execute(text(
            "INSERT INTO formula_registry (id, name, output_variable, function_ref, product_applicability, "
            "basis_applicability, version, status, created_at, updated_at, model_version_id, illustrative) VALUES "
            "(:id, 'Expected payment', 'expected_payment', 'spia_illustrative.expected_payment', ARRAY['SPIA']::varchar[], "
            "ARRAY[]::varchar[], 'v1', 'validated', now(), now(), :v, true)"
        ), {"id": ids["formula"], "v": ids["version"]})
        for dependency in ("monthly_payment", "survival_probability"):
            c.execute(text("INSERT INTO formula_dependencies (id, formula_id, depends_on_variable) VALUES (:id, :f, :d)"),
                      {"id": str(uuid.uuid4()), "f": ids["formula"], "d": dependency})
        c.execute(text("INSERT INTO model_published_outputs (id, model_version_id, variable_name, display_name, "
                       "aggregation, sort_order) VALUES (:id, :v, 'reserve', 'Reserve', 'end_of_period', 0)"),
                  {"id": str(uuid.uuid4()), "v": ids["version"]})
        c.execute(text("INSERT INTO scenario_sets (id, project_id, name, created_at) VALUES (:id, :p, 'S', now())"),
                  {"id": ids["sset"], "p": ids["project"]})
        for key, overrides in (("base", []), ("low", [{"target_variable": "discount_rate_annual", "operation": "set",
                                                        "value": 0.03}])):
            c.execute(text("INSERT INTO scenario_tables (id, set_id, scenario_name, overrides, created_at, fingerprint) "
                           "VALUES (:id, :s, :n, CAST(:o AS json), now(), :fp)"),
                      {"id": ids[key], "s": ids["sset"], "n": key, "o": json.dumps(overrides), "fp": "x" * 64})
        c.execute(text(
            "INSERT INTO projection_sets (id, project_id, name, model_version_id, inforce_file_ids, assumption_table_ids, "
            "scenario_ids, valuation_date, horizon_months, output_variables, trace_scope, parameters, created_at, "
            "updated_at) VALUES (:id, :p, 'PS', :v, CAST('[]' AS json), CAST('[]' AS json), CAST('[]' AS json), "
            "'2026-12-31', 12, CAST(:o AS json), CAST('{}' AS json), CAST('{}' AS json), now(), now())"
        ), {"id": ids["ps"], "p": ids["project"], "v": ids["version"], "o": json.dumps(["reserve", "expected_payment"])})
        v1 = _sequence(data for _pid, data in sorted(records))  # WP1 scheme: data only, by policy_id
        for key, fingerprint, rows in (("file", v1, records),
                                       ("bad_file", v1, records + [("P1", {"policy_id": "P1", "monthly_payment": "5"})])):
            c.execute(text("INSERT INTO inforce_files (id, project_id, filename, file_type, row_count, uploaded_at, "
                           "fingerprint, status) VALUES (:id, :p, :n, 'csv', :r, now(), :fp, 'validated')"),
                      {"id": ids[key], "p": ids["project"], "n": key, "r": len(rows), "fp": fingerprint})
            for policy_id, data in rows:
                c.execute(text("INSERT INTO inforce_records (file_id, policy_id, data, created_at) "
                               "VALUES (:f, :p, CAST(:d AS json), now())"),
                          {"f": ids[key], "p": policy_id, "d": json.dumps(data)})
    ids["records"] = records
    return ids


def test_upgrade_from_the_m1_head_backfills_definitions_and_inforce_v2(pg):
    engine, config = pg
    reset(engine)
    command.upgrade(config, M1_HEAD)
    ids = seed_spia_like_m1_data(engine)
    command.upgrade(config, "head")
    with engine.connect() as c:
        rows = {row.variable_name: row for row in c.execute(text(
            "SELECT variable_name, kind, source, default_value, allow_scenario_override FROM model_variable_definitions "
            "WHERE model_version_id = :v"), {"v": ids["version"]})}
        # Closure of the formulas + outputs (reserve -> expected_payment, discount_rate_annual);
        # 'unrelated' is not needed by the model and is not copied.
        assert set(rows) == {"monthly_payment", "survival_probability", "expected_payment", "discount_rate_annual",
                             "reserve"}
        assert rows["discount_rate_annual"].allow_scenario_override is True  # a scenario overrides it
        assert rows["discount_rate_annual"].default_value == 0.045
        assert not any(rows[name].allow_scenario_override for name in rows if name != "discount_rate_annual")
        assert rows["reserve"].source["type"] == "valuation"
        files = {row.id: row for row in c.execute(text(
            "SELECT id, fingerprint, fingerprint_scheme, status FROM inforce_files"))}
        expected_v2 = _sequence({"policy_id": pid, "data": data} for pid, data in sorted(ids["records"]))
        assert files[ids["file"]].fingerprint_scheme == "inforce-v2"
        assert files[ids["file"]].fingerprint == expected_v2 and files[ids["file"]].status == "validated"
        # Duplicate policy IDs: never silently re-fingerprinted.
        assert files[ids["bad_file"]].fingerprint_scheme == "inforce-v1"
        assert files[ids["bad_file"]].status == "needs_review"
    command.downgrade(config, WP1_HEAD)
    with engine.connect() as c:
        restored = c.execute(text("SELECT fingerprint FROM inforce_files WHERE id = :id"), {"id": ids["file"]}).scalar()
    assert restored == _sequence(data for _pid, data in sorted(ids["records"]))  # v1 restored
    command.upgrade(config, "head")
    assert current(engine) == [HEAD]


def test_upgrade_from_the_wp1_head_backfills_run_set_resolution(pg):
    engine, config = pg
    reset(engine)
    command.upgrade(config, WP1_HEAD)
    project, run_set, ps = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("INSERT INTO projects (id, name, created_at, updated_at) VALUES (:id, 'P', now(), now())"),
                  {"id": project})
        c.execute(text(
            "INSERT INTO projection_sets (id, project_id, name, inforce_file_ids, assumption_table_ids, factor_table_ids, "
            "scenario_ids, valuation_date, horizon_months, output_variables, trace_scope, parameters, created_at, "
            "updated_at) VALUES (:id, :p, 'PS', CAST('[]' AS json), CAST('[]' AS json), CAST('[]' AS json), "
            "CAST('[]' AS json), '2026-12-31', 12, CAST('[]' AS json), CAST('{}' AS json), CAST('{}' AS json), now(), now())"
        ), {"id": ps, "p": project})
        c.execute(text("INSERT INTO run_sets (id, project_id, name, projection_set_ids, scenario_ids, status, created_at) "
                       "VALUES (:id, :p, 'RS', CAST(:ps AS json), CAST('[]' AS json), 'success', now())"),
                  {"id": run_set, "p": project, "ps": json.dumps([ps])})
        for index, scenario in enumerate(("base", "low")):
            c.execute(text("INSERT INTO runs (id, project_id, status, created_at, run_set_id, projection_set_id, "
                           "scenario_id, name) VALUES (:id, :p, 'pending', now() + make_interval(secs => :i), :rs, "
                           ":ps, :s, :n)"),
                      {"id": str(uuid.uuid4()), "p": project, "rs": run_set, "ps": ps, "s": scenario,
                       "n": scenario, "i": index})
    command.upgrade(config, "head")
    with engine.connect() as c:
        row = c.execute(text("SELECT scenario_ids, resolution FROM run_sets WHERE id = :id"), {"id": run_set}).one()
    assert row.scenario_ids == ["base", "low"]  # previously [] although two scenarios ran
    assert row.resolution["projection_sets"][0]["scenario_source"] == "projection_set"
    assert row.resolution["backfilled_from_runs"] is True


def insert_finished_run(c, project_id: str, run_id: str, rows: list[tuple[str, int, float]], store) -> None:
    """A legacy-style finished run with consistent terminal evidence and the given output rows."""
    manifest = {"schema_version": "m1", "output_variables": ["expected_payment"]}
    fingerprint = hashlib.sha256(run_id.encode()).hexdigest()
    c.execute(text("INSERT INTO runs (id, project_id, status, created_at, started_at, completed_at, horizon_months, "
                   "valuation_date, accepted_attempt_number, attempt_count, final_manifest_fingerprint) VALUES "
                   "(:id, :p, 'success', now(), now(), now(), 2, '2026-12-31', 1, 1, :fp)"),
              {"id": run_id, "p": project_id, "fp": fingerprint})
    c.execute(text("INSERT INTO run_attempts (id, run_id, attempt_number, status, created_at) "
                   "VALUES (:id, :r, 1, 'success', now())"), {"id": str(uuid.uuid4()), "r": run_id})
    c.execute(text("INSERT INTO run_manifests (run_id, fingerprint, manifest, created_at, schema_version, attempt_number) "
                   "VALUES (:r, :fp, CAST(:m AS jsonb), now(), 'mentoramp.m1_manifest/legacy', 1)"),
              {"r": run_id, "fp": fingerprint, "m": json.dumps(manifest)})
    encoded = [
        {"run_id": run_id, "attempt_number": 1, "policy_id": policy_id, "scenario_id": "s",
         "projection_month": month, "variable_name": "expected_payment",
         "value_json": json.dumps({"value": value}), "product": "spia_lite"}
        for policy_id, month, value in rows
    ]
    stored = store.write_rows(run_id, "outputs", encoded)
    c.execute(text(
        "INSERT INTO run_artifacts (id, run_id, attempt_number, artifact_type, storage_uri, "
        "storage_backend, file_format, schema_version, row_count, checksum_sha256, partition, created_at) "
        "VALUES (:id, :r, 1, 'outputs', :uri, 'local', 'parquet', 'm1-v1', :count, :checksum, "
        "CAST(:partition AS json), now())"
    ), {"id": str(uuid.uuid4()), "r": run_id, "uri": stored.uri, "count": stored.row_count,
        "checksum": stored.checksum_sha256, "partition": json.dumps({"attempt_number": 1})})


def test_aggregates_are_exact_and_independent_of_row_order(pg, tmp_path, monkeypatch):
    from app.core import artifacts
    from app.core.artifacts import LocalParquetArtifactStore
    from app.services import results_service

    engine, config = pg
    reset(engine)
    command.upgrade(config, "head")
    # Values whose float sum depends on the order they are added: (1e16 + 1) - 1e16 = 0 in
    # binary floating point, but 1e16 - 1e16 + 1 = 1.
    values = [("P1", 1, 1e16), ("P2", 1, 1.0), ("P3", 1, -1e16), ("P1", 2, 0.1), ("P2", 2, 0.2), ("P3", 2, 0.3)]
    store = LocalParquetArtifactStore(tmp_path)
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: store)
    project, forward, backward = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("INSERT INTO projects (id, name, created_at, updated_at) VALUES (:id, 'P', now(), now())"),
                  {"id": project})
        insert_finished_run(c, project, forward, values, store)
        insert_finished_run(c, project, backward, list(reversed(values)), store)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        results = {run_id: results_service.aggregates(db, run_id, group_by="projection_month",
                                                      variables="expected_payment")["rows"]
                   for run_id in (forward, backward)}
    assert results[forward] == results[backward]
    month_one = results[forward][1]["values"]["expected_payment"]
    assert month_one == 1.0  # the exact sum, whatever the order
    assert results[forward][2]["values"]["expected_payment"] == 0.6
    # Sequential float addition loses the unit; the exact aggregation must not.
    naive = 0.0
    for _policy, month, value in values:
        if month == 1:
            naive += value
    assert naive != 1.0


def test_retained_run_evidence_cannot_be_deleted_by_accident(pg):
    engine, config = pg
    reset(engine)
    command.upgrade(config, "head")
    project, run_id, package_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("INSERT INTO projects (id, name, created_at, updated_at) VALUES (:id, 'P', now(), now())"),
                  {"id": project})
        c.execute(text("INSERT INTO runs (id, project_id, status, created_at) VALUES (:id, :p, 'pending', now())"),
                  {"id": run_id, "p": project})
        c.execute(text("INSERT INTO run_packages (id, run_id, project_id, schema_version, fingerprint_algorithm, "
                       "fingerprint, package, created_at) VALUES (:id, :r, :p, 'v2', 'a', :fp, CAST('{}' AS json), now())"),
                  {"id": package_id, "r": run_id, "p": project, "fp": "f" * 64})
        c.execute(text("INSERT INTO run_manifests (run_id, fingerprint, manifest, created_at, run_package_id) "
                       "VALUES (:r, :fp, CAST('{}' AS jsonb), now(), :pk)"),
                  {"r": run_id, "fp": "m" * 64, "pk": package_id})
    for statement in ("DELETE FROM runs WHERE id = :id", "DELETE FROM projects WHERE id = :p"):
        with pytest.raises(DBAPIError, match="foreign key|violates"):
            with engine.begin() as c:
                c.execute(text(statement), {"id": run_id, "p": project})
    for table in ("run_packages", "run_manifests"):
        with pytest.raises(DBAPIError, match="retained run evidence"):
            with engine.begin() as c:
                c.execute(text(f"DELETE FROM {table}"))
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM run_packages")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM run_manifests")).scalar() == 1
        rules = {
            (row.table, row.column): row.rule
            for row in c.execute(text(
                "SELECT tc.table_name AS table, kcu.column_name AS column, rc.delete_rule AS rule "
                "FROM information_schema.referential_constraints rc "
                "JOIN information_schema.table_constraints tc ON tc.constraint_name = rc.constraint_name "
                "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = rc.constraint_name "
                "WHERE tc.table_name IN ('run_packages', 'run_manifests', 'run_attempts')"))
        }
    assert rules[("run_packages", "run_id")] == "RESTRICT"
    assert rules[("run_packages", "project_id")] == "RESTRICT"
    assert rules[("run_manifests", "run_id")] == "RESTRICT"
    assert rules[("run_manifests", "run_package_id")] == "RESTRICT"
    assert rules[("run_attempts", "run_id")] == "RESTRICT"
    # The explicit retention override (future workflow) is the only way through.
    with engine.begin() as c:
        c.execute(text("SET LOCAL mentoramp.allow_evidence_deletion = 'on'"))
        c.execute(text("DELETE FROM run_manifests WHERE run_id = :id"), {"id": run_id})
        c.execute(text("DELETE FROM run_packages WHERE id = :id"), {"id": package_id})
        c.execute(text("DELETE FROM runs WHERE id = :id"), {"id": run_id})
