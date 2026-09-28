"""Integration tests for run management and analytical read APIs."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import runs as runs_api
from app.api.dependencies import get_current_user
from app.core import artifacts
from app.core.artifacts import LocalParquetArtifactStore, RunArtifactBuffer
from app.db.database import get_db
from app.db.models.audit_log import AuditLog
from app.db.models.project import Project
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact, RunManifest
from app.main import app
from app.models.schemas import ProjectionContext, VariableResolutionResult


@pytest.fixture()
def run_api_client(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        Project.__table__,
        Run.__table__,
        RunManifest.__table__,
        RunArtifact.__table__,
        AuditLog.__table__,
    ):
        table.create(engine)
    session = sessionmaker(bind=engine)()
    project = Project(id="project-1", name="Valuation")
    session.add(project)
    session.commit()

    store = LocalParquetArtifactStore(tmp_path)
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: store)
    monkeypatch.setattr(runs_api, "execute_queued_run", lambda definition: None)

    def fake_manifest(db, definition):
        record = RunManifest(
            run_id=definition.id,
            fingerprint="a" * 64,
            manifest={"run_definition": definition.model_dump(mode="json")},
        )
        db.add(record)
        db.flush()
        return record

    monkeypatch.setattr(runs_api, "create_run_manifest", fake_manifest)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="actuary-1", roles=[SimpleNamespace(name="actuary")]
    )
    try:
        with TestClient(app) as client:
            yield client, session, store
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def _queue_run(client: TestClient) -> str:
    response = client.post(
        "/v1/runs/",
        json={
            "name": "Quarter End",
            "project_id": "project-1",
            "formula_database_id": "formula-db-1",
            "dataset_ids": [],
            "scenario_ids": ["base"],
            "projection_length_months": 12,
            "selected_output_variables": ["reserve"],
            "debug_mode": True,
        },
    )
    assert response.status_code == 202
    return response.json()["id"]


def test_queue_list_detail_and_manifest(run_api_client):
    client, session, _ = run_api_client
    run_id = _queue_run(client)

    listing = client.get("/v1/runs/")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["runs"][0]["status"] == "pending"

    detail = client.get(f"/v1/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["project_id"] == "project-1"

    manifest = client.get(f"/v1/runs/{run_id}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["fingerprint"] == "a" * 64
    assert session.query(AuditLog).filter(AuditLog.action == "run.queued").count() == 1


def test_result_summary_and_trace_filters(run_api_client):
    client, session, store = run_api_client
    run_id = _queue_run(client)
    buffer = RunArtifactBuffer(run_id, store=store)
    buffer.add_output("P001", "base", 1, "reserve", 100.0)
    buffer.add_output("P001", "base", 2, "reserve", 110.0)
    buffer.add_trace(
        ProjectionContext(
            project_id="project-1",
            run_id=run_id,
            policy_id="P001",
            scenario_id="base",
            projection_month=2,
        ),
        VariableResolutionResult(
            variable_name="reserve",
            value=110.0,
            source_type="formula",
            lookup_keys={"projection_month": 2},
        ),
    )
    buffer.flush_policy(session, "P001")
    session.commit()

    results = client.get(f"/v1/runs/{run_id}/results?month_from=2")
    assert results.status_code == 200
    assert results.json()["total"] == 1
    assert results.json()["results"][0]["value"] == 110.0

    summary = client.get(f"/v1/runs/{run_id}/summary")
    assert summary.status_code == 200
    assert summary.json()["summary"]["calculated_variable_count"] == 2

    events = client.get(f"/v1/runs/{run_id}/events?variable=reserve")
    assert events.status_code == 200
    assert events.json()["total"] == 1
    assert events.json()["events"][0]["lookup_keys"] == {"projection_month": 2}


def test_queue_rejects_unknown_project(run_api_client):
    client, _, _ = run_api_client
    response = client.post(
        "/v1/runs/",
        json={
            "name": "Invalid",
            "project_id": "missing",
            "formula_database_id": "formula-db-1",
            "scenario_ids": ["base"],
            "projection_length_months": 12,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
