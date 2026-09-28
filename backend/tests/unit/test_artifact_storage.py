"""Tests for Parquet-backed projection artifacts."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.artifacts import LocalParquetArtifactStore, RunArtifactBuffer
from app.core.output import storage as output_storage
from app.core.projection_engine import runner
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.models.schemas import FormulaDefinition, ProjectionRunDefinition


def test_output_buffer_writes_parquet_and_reads_results(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    RunArtifact.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    store = LocalParquetArtifactStore(tmp_path)
    monkeypatch.setattr(output_storage, "get_artifact_store", lambda: store)

    buffer = RunArtifactBuffer("run-001", store=store)
    buffer.add_output("P001", "base", 1, "reserve", 100.5, product="fia")
    buffer.add_output("P001", "base", 2, "reserve", 110.25, product="fia")
    artifacts = buffer.flush_policy(db, "P001")
    db.commit()

    assert len(artifacts) == 1
    assert artifacts[0].row_count == 2
    assert len(artifacts[0].checksum_sha256) == 64
    relative = artifacts[0].storage_uri.removeprefix("local://")
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
    monkeypatch.setattr(output_storage, "get_artifact_store", lambda: store)
    monkeypatch.setitem(FORMULA_FUNCTIONS, "test_constant_reserve", lambda: 42.0)

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
