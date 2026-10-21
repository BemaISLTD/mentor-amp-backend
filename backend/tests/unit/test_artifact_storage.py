"""Tests for Parquet-backed projection artifacts."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import artifacts
from app.core.artifacts import (
    IDENTITY_NEUTRAL_SCHEMA,
    LocalParquetArtifactStore,
    RunArtifactBuffer,
    read_attempt_artifact_rows,
    verified_parquet_rows,
)
from app.core.output import storage as output_storage
from app.core.projection_engine import runner
from app.core.variable_registry.resolver import _prior_period_lookup
from app.db.database import Base
from app.db.immutability import ImmutableRecordError
from app.core.formula_engine.formulas import (
    FORMULA_REGISTRY,
    RegisteredFormulaFunction,
    implementation_fingerprint,
)
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.models.schemas import FormulaDefinition, ProjectionContext, ProjectionRunDefinition
from app.services.common import ServiceError
from app.services import artifact_reconciliation


def test_output_buffer_writes_parquet_and_reads_results(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    RunArtifact.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    store = LocalParquetArtifactStore(tmp_path)
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: store)

    buffer = RunArtifactBuffer("run-001", store=store)
    buffer.add_output("P001", "base", 1, "reserve", 100.5, product="fia")
    buffer.add_output("P001", "base", 2, "reserve", 110.25, product="fia")
    written_artifacts = buffer.flush_policy(db, "P001")
    db.commit()

    assert len(written_artifacts) == 1
    assert written_artifacts[0].row_count == 2
    assert len(written_artifacts[0].checksum_sha256) == 64
    relative = written_artifacts[0].storage_uri.removeprefix("local://")
    assert (Path(tmp_path) / relative).exists()
    assert output_storage.get_output(db, "run-001", "P001", "base", 2, "reserve") == 110.25
    assert output_storage.get_run_results(db, "run-001") == [
        {
            "policy_id": "P001",
            "scenario_id": "base",
            "month": 1,
            "variable": "reserve",
            "value": 100.5,
        },
        {
            "policy_id": "P001",
            "scenario_id": "base",
            "month": 2,
            "variable": "reserve",
            "value": 110.25,
        },
    ]
    summary = buffer.summary()
    assert summary.policy_count == 1
    assert summary.period_count == 2
    assert summary.calculated_variable_count == 2

    db.close()
    engine.dispose()


def test_local_store_rejects_path_traversal(tmp_path):
    store = LocalParquetArtifactStore(tmp_path)

    try:
        store.read_rows("local://../outside.parquet")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("Path traversal should be rejected.")


def test_identity_neutral_parquet_is_bitwise_reproducible_and_run_bound(tmp_path):
    store = LocalParquetArtifactStore(tmp_path)
    payload = [{
        "policy_id": "P001", "scenario_id": "base", "projection_month": 1,
        "variable_name": "reserve", "value_json": "100.5", "product": "MYGA",
    }]
    first = store.write_rows("run-first", "outputs", payload)
    second = store.write_rows("run-second", "outputs", payload)
    assert store.read_bytes(first.uri) == store.read_bytes(second.uri)
    assert first.checksum_sha256 == second.checksum_sha256

    rows = verified_parquet_rows(
        store, first.uri, first.checksum_sha256, first.row_count,
        IDENTITY_NEUTRAL_SCHEMA, supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
        expected_run_id="run-first", expected_attempt_number=1,
    )
    assert rows == [{"run_id": "run-first", "attempt_number": 1, **payload[0]}]

    with pytest.raises(ServiceError) as wrong_run:
        verified_parquet_rows(
            store, first.uri, first.checksum_sha256, first.row_count,
            IDENTITY_NEUTRAL_SCHEMA, supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
            expected_run_id="run-second", expected_attempt_number=1,
        )
    assert wrong_run.value.code == "ARTIFACT_IDENTITY_MISMATCH"

    with pytest.raises(ServiceError) as missing_attempt:
        verified_parquet_rows(
            store, first.uri, first.checksum_sha256, first.row_count,
            IDENTITY_NEUTRAL_SCHEMA, supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
            expected_run_id="run-first",
        )
    assert missing_attempt.value.code == "ARTIFACT_IDENTITY_MISMATCH"

    malformed = store.write_rows("run-first", "outputs", [
        {**payload[0], "run_id": "run-first"},
    ])
    with pytest.raises(ServiceError) as embedded_identity:
        verified_parquet_rows(
            store, malformed.uri, malformed.checksum_sha256, malformed.row_count,
            IDENTITY_NEUTRAL_SCHEMA, supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
            expected_run_id="run-first", expected_attempt_number=1,
        )
    assert embedded_identity.value.code == "ARTIFACT_IDENTITY_MISMATCH"


def test_orphan_reconciliation_never_deletes_registered_evidence(tmp_path, monkeypatch):
    store = LocalParquetArtifactStore(tmp_path)
    retained = store.write_rows("retained", "outputs", [{"value": 1}])
    orphan = store.write_rows("orphan", "outputs", [{"value": 2}])
    monkeypatch.setattr(
        artifact_reconciliation,
        "registered_local_uris",
        lambda _db: {retained.uri},
    )
    future = max(
        store.path_for_uri(retained.uri).stat().st_mtime,
        store.path_for_uri(orphan.uri).stat().st_mtime,
    ) + 10

    dry_run = artifact_reconciliation.reconcile_local_artifacts(
        object(), store=store, now=future, min_age_seconds=0
    )
    assert dry_run.orphan_uris == (orphan.uri,)
    assert dry_run.deleted_uris == ()
    assert store.path_for_uri(orphan.uri).exists()

    deleted = artifact_reconciliation.reconcile_local_artifacts(
        object(), store=store, delete=True, now=future, min_age_seconds=0
    )
    assert deleted.deleted_uris == (orphan.uri,)
    assert store.path_for_uri(retained.uri).exists()
    assert not store.path_for_uri(orphan.uri).exists()


def test_registered_artifact_checksum_is_enforced(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    RunArtifact.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    store = LocalParquetArtifactStore(tmp_path)
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: store)
    buffer = RunArtifactBuffer("run-tampered", store=store)
    buffer.add_output("P001", "base", 1, "reserve", 100.5)
    artifact = buffer.flush_policy(db, "P001")[0]
    db.commit()

    store.path_for_uri(artifact.storage_uri).write_bytes(b"replaced")
    with pytest.raises(ServiceError) as raised:
        output_storage.get_run_results(db, "run-tampered")
    assert raised.value.code == "ARTIFACT_CHECKSUM_MISMATCH"

    db.close()
    engine.dispose()


def test_selected_attempt_query_uses_filtered_verified_scan(tmp_path):
    engine = create_engine("sqlite://")
    RunArtifact.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    store = LocalParquetArtifactStore(tmp_path)
    rows = [
        {
            "run_id": "run-filtered",
            "attempt_number": 1,
            "policy_id": f"P{number:04d}",
            "scenario_id": "base",
            "projection_month": 1,
            "variable_name": "reserve",
            "value_json": str(number),
            "product": "SPIA",
        }
        for number in range(1000)
    ]
    stored = store.write_rows("run-filtered", "outputs", rows)
    db.add(RunArtifact(
        run_id="run-filtered",
        attempt_number=1,
        artifact_type="outputs",
        storage_uri=stored.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version="m1-v1",
        row_count=stored.row_count,
        checksum_sha256=stored.checksum_sha256,
        partition={"attempt_number": 1},
    ))
    db.commit()

    selected = read_attempt_artifact_rows(
        db,
        "run-filtered",
        "outputs",
        1,
        store,
        filters={"policy_id": "P0500"},
    )
    assert len(selected) == 1
    assert selected[0]["policy_id"] == "P0500"

    db.close()
    engine.dispose()


def test_run_artifact_orm_refuses_update_and_delete(tmp_path):
    engine = create_engine("sqlite://")
    RunArtifact.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    store = LocalParquetArtifactStore(tmp_path)
    buffer = RunArtifactBuffer("run-retained", store=store)
    artifact = buffer.flush_policy(db, "P001")
    assert artifact == []
    buffer.add_output("P001", "base", 1, "reserve", 1.0)
    row = buffer.flush_policy(db, "P001")[0]
    db.commit()

    row.row_count = 2
    with pytest.raises(ImmutableRecordError):
        db.commit()
    db.rollback()
    db.delete(row)
    with pytest.raises(ImmutableRecordError):
        db.flush()

    db.rollback()
    db.close()
    engine.dispose()


def test_prior_period_fallback_reads_parquet_artifact(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    RunArtifact.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    store = LocalParquetArtifactStore(tmp_path)
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: store)

    buffer = RunArtifactBuffer("run-prior", store=store)
    buffer.add_output("P001", "base", 1, "reserve", 125.5)
    buffer.flush_policy(db, "P001")
    db.commit()

    value = _prior_period_lookup(
        db,
        "reserve",
        ProjectionContext(
            run_id="run-prior",
            policy_id="P001",
            scenario_id="base",
            projection_month=2,
        ),
    )

    assert value == 125.5
    db.close()
    engine.dispose()


def test_legacy_run_tables_are_absent_from_model_metadata():
    assert "run_outputs" not in Base.metadata.tables
    assert "trace_logs" not in Base.metadata.tables


def test_projection_runner_writes_outputs_without_run_output_rows(
    tmp_path, monkeypatch
):
    engine = create_engine("sqlite://")
    Run.__table__.create(engine)
    RunArtifact.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    store = LocalParquetArtifactStore(tmp_path)

    formula = FormulaDefinition(
        id="formula-1",
        name="Constant Reserve",
        output_variable="reserve",
        function_ref="test_constant_reserve",
        dependencies=[],
        status="active",
    )
    monkeypatch.setattr(runner, "load_policies", lambda db, ids: [{"policy_id": "P1", "data": {}}])
    monkeypatch.setattr(runner, "load_formulas", lambda db: [formula])
    monkeypatch.setattr(runner, "create_run_manifest", lambda db, run_def: None)
    monkeypatch.setattr(
        runner,
        "RunArtifactBuffer",
        lambda run_id: RunArtifactBuffer(run_id, store=store),
    )
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: store)
    def constant_reserve():
        return 42.0

    monkeypatch.setitem(
        FORMULA_REGISTRY,
        "test_constant_reserve",
        RegisteredFormulaFunction(
            key="test_constant_reserve",
            func=constant_reserve,
            module=constant_reserve.__module__,
            qualname=constant_reserve.__qualname__,
            implementation_version=None,
            implementation_fingerprint=implementation_fingerprint(constant_reserve),
        ),
    )

    result = runner.run_projection(
        ProjectionRunDefinition(
            id="run-artifact-test",
            name="Artifact Test",
            project_id="project-1",
            formula_database_id="formula-db-1",
            scenario_ids=["base"],
            projection_length_months=2,
            selected_output_variables=["reserve"],
        ),
        db,
    )

    assert result.status == "success"
    assert result.summary.calculated_variable_count == 2
    assert db.query(RunArtifact).count() == 1
    assert output_storage.get_run_results(db, "run-artifact-test") == [
        {
            "policy_id": "P1",
            "scenario_id": "base",
            "month": 1,
            "variable": "reserve",
            "value": 42.0,
        },
        {
            "policy_id": "P1",
            "scenario_id": "base",
            "month": 2,
            "variable": "reserve",
            "value": 42.0,
        },
    ]

    db.close()
    engine.dispose()
