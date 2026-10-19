"""Governed metadata is scoped, versioned, audited, and softly archived."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.database import Base, get_db
from app.db.models.audit_log import AuditLog
from app.db.models.formula import FormulaRegistry
from app.db.models.governance import DerivedDataset, Report, RollforwardTemplate
from app.db.models.modeling import ModelVersion
from app.db.models.project import Project
from app.db.models.project_member import ProjectMember
from app.db.models.user import User
from app.db.models.variable import VariableRegistry
from app.main import app


@pytest.fixture()
def governed_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    actor = User(id="governance-actor", email="gov@example.com", full_name="Governance Actor",
                 password_hash="unused")
    project = Project(id="governance-project", name="Governed project", created_by=actor.id)
    db.add_all([actor, project])
    db.flush()
    db.add(ProjectMember(
        project_id=project.id, user_id=actor.id, role="owner", created_by=actor.id
    ))
    db.commit()

    def override_db():
        yield db

    principal = SimpleNamespace(id=actor.id, roles=[SimpleNamespace(
        name="admin", permissions=[
            SimpleNamespace(name="projects:read"), SimpleNamespace(name="registries:read"),
            SimpleNamespace(name="registries:write"),
            SimpleNamespace(name="runs:read"),
        ],
    )])
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        with TestClient(app) as client:
            yield client, db, actor, project
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_model_versions_are_whole_model_versions_and_published_versions_are_immutable(governed_client):
    client, db, actor, project = governed_client
    created = client.post(f"/v1/projects/{project.id}/models", json={
        "name": "Cashflow model", "product_code": "SPIA",
    })
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]

    first = client.post(f"/v1/models/{model_id}/versions", json={
        "version_label": "2026.1", "basis": "GAAP", "configuration": {"currency": "USD"},
    })
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    published = client.post(f"/v1/model-versions/{first_id}/publish")
    assert published.status_code == 409
    assert published.json()["error"]["code"] == "MODEL_VERSION_NOT_APPROVED"
    stored_version = db.get(ModelVersion, first_id)
    stored_version.status = "approved"
    stored_version.published_at = stored_version.created_at
    db.add(VariableRegistry(
        name="governed_output", data_type="number", source_type="formula"
    ))
    db.flush()
    formula = FormulaRegistry(
        name="Governed formula", output_variable="governed_output",
        function_ref="spia.reserve", model_version_id=first_id,
        created_by=actor.id, updated_by=actor.id,
    )
    db.add(formula)
    db.commit()
    assert client.patch(f"/v1/model-versions/{first_id}", json={"notes": "late edit"}).status_code == 409
    changed = client.put(f"/v1/formulas/{formula.id}", json={"status": "active"})
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"
    removed = client.delete(f"/v1/formulas/{formula.id}")
    assert removed.status_code == 409, removed.text
    assert removed.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"

    second = client.post(f"/v1/models/{model_id}/versions", json={
        "parent_version_id": first_id, "change_summary": "Updated assumptions",
    })
    assert second.status_code == 201, second.text
    assert second.json()["version_number"] == 2
    assert second.json()["configuration"] == {"currency": "USD"}
    assert db.query(AuditLog).filter(AuditLog.actor_user_id == actor.id).count() >= 3


def test_rollforward_report_and_dataset_lifecycles(governed_client):
    client, db, _actor, project = governed_client
    model = client.post(f"/v1/projects/{project.id}/models", json={
        "name": "Workflow model", "product_code": "SPIA",
    }).json()
    version = client.post(f"/v1/models/{model['id']}/versions", json={"basis": "GAAP"}).json()

    template = client.post(f"/internal/experimental/projects/{project.id}/rollforward-templates", json={
        "name": "Quarter close", "model_version_id": version["id"],
        "steps": [{"step_key": "project", "name": "Project", "step_type": "run"}],
    })
    assert template.status_code == 201, template.text
    template_id = template.json()["id"]
    assert len(template.json()["steps"]) == 1
    assert client.post(f"/internal/experimental/rollforward-templates/{template_id}/publish").status_code == 200
    assert client.patch(f"/internal/experimental/rollforward-templates/{template_id}", json={
        "description": "must version",
    }).status_code == 409

    job = client.post(f"/internal/experimental/projects/{project.id}/rollforward-jobs", json={
        "template_id": template_id, "name": "2026 Q3 close",
        "from_date": "2026-06-30", "to_date": "2026-09-30",
    })
    assert job.status_code == 201, job.text
    assert job.json()["steps"][0]["status"] == "pending"

    report = client.post(f"/internal/experimental/projects/{project.id}/reports", json={
        "name": "Reserve movement", "report_type": "movement", "definition": {"group_by": ["product"]},
    })
    assert report.status_code == 201, report.text
    report_id = report.json()["id"]
    assert client.post(f"/internal/experimental/reports/{report_id}/publish").status_code == 200
    assert client.patch(f"/internal/experimental/reports/{report_id}", json={"description": "late"}).status_code == 409

    dataset = client.post(f"/internal/experimental/projects/{project.id}/derived-datasets", json={
        "name": "Movement detail", "dataset_type": "report_source",
        "schema": {"reserve": "decimal"}, "source_run_ids": [],
        "storage_uri": "artifacts/movement.parquet", "file_format": "parquet",
    })
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["id"]
    assert dataset.json()["schema"] == {"reserve": "decimal"}
    assert client.delete(f"/internal/experimental/derived-datasets/{dataset_id}").status_code == 204
    assert client.get(f"/internal/experimental/derived-datasets/{dataset_id}").status_code == 404
    stored = db.get(DerivedDataset, dataset_id)
    assert stored.deleted_at is not None
    assert db.get(RollforwardTemplate, template_id).published_at is not None
    assert db.get(Report, report_id).published_at is not None
    assert db.get(ModelVersion, version["id"]) is not None


def test_deferred_governance_is_not_a_public_v1_contract(governed_client):
    client, _db, _actor, project = governed_client
    assert client.get(f"/v1/projects/{project.id}/rollforward-templates").status_code == 404

    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/runs/{run_id}/steps" in paths
    assert not any(
        fragment in path
        for path in paths
        for fragment in ("rollforward", "derived-datasets", "/reports")
    )
