"""WP2 Data Manager lifecycle across all governed dataset categories."""

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.config import settings
from app.db.database import Base, get_db
from app.db.models.audit_log import AuditLog
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.modeling import Model, ModelVersion
from app.db.models.project import Project
from app.db.models.project_member import ProjectMember
from app.db.models.user import User
from app.db.models.variable import VariableRegistry
from app.main import app


def principal(user_id: str, role: str = "admin", permissions: tuple[str, ...] = (
    "imports:read", "imports:write", "imports:approve", "projects:read",
)):
    return SimpleNamespace(id=user_id, roles=[SimpleNamespace(
        name=role, permissions=[SimpleNamespace(name=name) for name in permissions],
    )])


@pytest.fixture()
def data_manager(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    creator = User(id="import-creator", email="creator@example.com", full_name="Creator",
                   password_hash="unused")
    reviewer = User(id="import-reviewer", email="reviewer@example.com", full_name="Reviewer",
                    password_hash="unused")
    project = Project(id="data-project", name="Data project", created_by=creator.id)
    db.add_all([creator, reviewer, project])
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=reviewer.id, role="viewer"))
    db.commit()
    current = {"user": principal(creator.id)}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    monkeypatch.setattr(settings, "artifact_storage_path", tmp_path / "artifacts")
    try:
        with TestClient(app) as client:
            yield client, db, project, current, creator, reviewer
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def run_flow(client, project_id: str, category: str, name: str, csv: str,
             mapping: list[dict], options: dict | None = None, replaces: str | None = None):
    opened = client.post("/v1/import-sessions", json={
        "project_id": project_id, "category": category, "name": name,
        "options": options or {}, "replaces_dataset_id": replaces,
    })
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    uploaded = client.post(
        f"/v1/import-sessions/{session_id}/files",
        files={"file": (f"{name}.csv", csv.encode(), "text/csv")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["file_id"] == session_id
    assert uploaded.json()["raw_fingerprint"]
    preview = client.get(f"/v1/import-sessions/{session_id}/preview")
    assert preview.status_code == 200 and preview.json()["sample_rows"]
    mapped = client.put(f"/v1/import-sessions/{session_id}/mapping", json={"fields": mapping})
    assert mapped.status_code == 200, mapped.text
    validated = client.post(f"/v1/import-sessions/{session_id}/validate")
    assert validated.status_code == 201, validated.text
    assert validated.json()["error_count"] == 0, validated.text
    committed = client.post(f"/v1/import-sessions/{session_id}/commit")
    assert committed.status_code == 201, committed.text
    return session_id, validated.json(), committed.json()


def test_inforce_mapping_version_approval_and_comparison(data_manager):
    client, db, project, current, creator, reviewer = data_manager
    csv = (
        "Policy Number,Product,Issue Date,Age,Sex,Single Premium,Monthly Benefit\n"
        "P-1,SPIA,2024-01-01,65,F,100000,700\n"
        "P-2,SPIA,2024-02-01,70,M,120000,850\n"
    )
    mapping = [
        {"source_column": "Policy Number", "target_field": "policy_id", "transform": "strip"},
        {"source_column": "Product", "target_field": "product_type", "transform": "upper"},
        {"source_column": "Issue Date", "target_field": "issue_date", "transform": "date_iso"},
        {"source_column": "Age", "target_field": "issue_age", "transform": "integer"},
        {"source_column": "Sex", "target_field": "gender", "transform": "upper"},
        {"source_column": "Single Premium", "target_field": "premium", "transform": "number"},
        {"source_column": "Monthly Benefit", "target_field": "monthly_payment", "transform": "number"},
    ]
    session_id, validation, first = run_flow(
        client, project.id, "liability_inforce", "SPIA policies", csv, mapping,
        {"valuation_date": "2026-01-01", "expected_record_count": 2},
    )
    raw = client.get(f"/v1/import-sessions/{session_id}/file")
    assert raw.status_code == 200
    assert hashlib.sha256(raw.content).hexdigest() == first["raw_fingerprint"]
    assert "attachment" in raw.headers["content-disposition"]
    assert first["fingerprint_scheme"] == "inforce-v3"
    assert first["status"] == "validated"
    denied = client.post(f"/v1/datasets/inforce/{first['id']}/approve", json={"reason": "checked"})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"

    current["user"] = principal(
        reviewer.id, "reviewer", ("imports:read", "imports:approve", "projects:read"),
    )
    approved = client.post(f"/v1/datasets/inforce/{first['id']}/approve",
                           json={"reason": "Independent review complete"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    current["user"] = principal(creator.id)

    changed_csv = csv.replace("120000,850", "125000,875") + "P-3,SPIA,2024-03-01,68,F,90000,650\n"
    _second_session, second_validation, second = run_flow(
        client, project.id, "liability_inforce", "SPIA policies", changed_csv, mapping,
        {"valuation_date": "2026-01-01", "expected_record_count": 3}, replaces=first["id"],
    )
    assert second["version_number"] == 2
    versions = client.get(f"/v1/datasets/inforce/{second['id']}/versions").json()["versions"]
    assert [item["version_number"] for item in versions] == [2, 1]
    comparison = client.get(
        f"/v1/datasets/inforce/{first['id']}/compare", params={"other_id": second["id"]}
    )
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["added_count"] == 1
    assert comparison.json()["changed_count"] == 1
    events = client.get(f"/v1/import-sessions/{session_id}/log").json()["events"]
    assert [event["action"] for event in events] == ["opened", "uploaded", "mapped", "validated", "committed"]
    assert validation["canonical_fingerprint"] != second_validation["canonical_fingerprint"]
    audit = db.query(AuditLog).filter(
        AuditLog.entity_type == "import_session", AuditLog.entity_id == session_id,
    ).order_by(AuditLog.id).all()
    assert [entry.action for entry in audit] == [
        "import_session.opened", "import_session.uploaded", "import_session.mapped",
        "import_session.validated", "import_session.committed",
    ]
    assert all(entry.actor_user_id == creator.id and entry.after_state for entry in audit)
    assert audit[0].before_state is None
    assert all(entry.before_state for entry in audit[1:])


@pytest.mark.parametrize("category,name,csv,mapping,options,kind", [
    (
        "assumption_table", "Mortality",
        "age,gender,qx\n65,F,0.010\n65,M,0.012\n",
        [{"source_column": key, "target_field": key} for key in ("age", "gender", "qx")],
        {"table_type": "mortality", "lookup_keys": ["age", "gender"], "value_column": "qx"},
        "assumption",
    ),
    (
        "factor_table", "Crediting factors",
        "product,duration,factor\nSPIA,1,1.02\nSPIA,2,1.01\n",
        [{"source_column": key, "target_field": key} for key in ("product", "duration", "factor")],
        {"table_type": "crediting", "lookup_keys": ["product", "duration"], "value_column": "factor"},
        "factor",
    ),
    (
        "scenario", "Low rate",
        "scenario_name,target_variable,operation,value,applies_from_period,applies_to_period\n"
        "Low rate,discount_rate_annual,set,0.03,0,1200\n",
        [{"source_column": key, "target_field": key} for key in (
            "scenario_name", "target_variable", "operation", "value",
            "applies_from_period", "applies_to_period",
        )],
        {}, "scenario",
    ),
])
def test_each_non_inforce_category_is_persisted(data_manager, category, name, csv, mapping,
                                                 options, kind):
    client, _db, project, _current, _creator, _reviewer = data_manager
    _session, validation, dataset = run_flow(
        client, project.id, category, name, csv, mapping, options,
    )
    assert validation["status"] == "passed"
    assert dataset["kind"] == kind
    assert dataset["row_count"] == (1 if category == "scenario" else 2)


def test_validation_keeps_issues_and_rejected_records(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    opened = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "liability_inforce", "name": "Bad policies",
        "options": {"valuation_date": "2026-01-01"},
    }).json()
    csv = (
        "policy_id,product_type,issue_date,issue_age,gender,premium,monthly_payment\n"
        "DUP,SPIA,2024-01-01,65,F,100000,700\n"
        "DUP,SPIA,2027-01-01,abc,X,-5,\n"
    )
    assert client.post(f"/v1/import-sessions/{opened['id']}/files",
                       files={"file": ("bad.csv", csv.encode(), "text/csv")}).status_code == 200
    fields = [{"source_column": key, "target_field": key} for key in (
        "policy_id", "product_type", "issue_date", "issue_age", "gender", "premium", "monthly_payment",
    )]
    assert client.put(f"/v1/import-sessions/{opened['id']}/mapping",
                      json={"fields": fields}).status_code == 200
    validation = client.post(f"/v1/import-sessions/{opened['id']}/validate")
    assert validation.status_code == 201
    body = validation.json()
    assert body["error_count"] >= 3 and body["rejected_count"] == 1
    issues = client.get(f"/v1/validation-runs/{body['id']}/issues").json()["issues"]
    assert {issue["code"] for issue in issues} >= {
        "DUPLICATE_KEY", "TYPE_MISMATCH", "INVALID_GENDER", "MISSING_VALUE",
        "EFFECTIVE_DATE_INVALID",
    }
    rejected = client.get(
        f"/v1/validation-runs/{body['id']}/rejected-records"
    ).json()["rejected_records"]
    assert rejected[0]["raw_record"]["policy_id"] == "DUP"
    refused = client.post(f"/v1/import-sessions/{opened['id']}/commit")
    assert refused.status_code == 409


def test_validation_warnings_are_visible_in_project_issue_feed(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    csv = (
        "policy_id,product_type,issue_date,issue_age,gender,premium,monthly_payment\n"
        "P-1,SPIA,2024-01-01,130,F,-5,700\n"
    )
    fields = [{"source_column": key, "target_field": key} for key in (
        "policy_id", "product_type", "issue_date", "issue_age", "gender", "premium",
        "monthly_payment",
    )]
    _session, validation, dataset = run_flow(
        client, project.id, "liability_inforce", "Warning policies", csv, fields,
    )
    assert validation["warning_count"] == 2
    assert dataset["status"] == "needs_review"

    response = client.get(f"/v1/projects/{project.id}/validation-issues")
    assert response.status_code == 200, response.text
    imported = [issue for issue in response.json()["issues"] if issue.get("code") == "RANGE_WARNING"]
    assert len(imported) == 2
    assert {issue["column_name"] for issue in imported} == {"issue_age", "premium"}
    assert all(issue["target"] == {"type": "inforce_dataset", "id": dataset["id"]}
               for issue in imported)


def test_mapping_profiles_are_versioned_and_reusable(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    fields = [{"source_column": key, "target_field": key} for key in (
        "policy_id", "product_type", "issue_date", "issue_age", "gender", "premium",
        "monthly_payment",
    )]
    first = client.post("/v1/mapping-profiles", json={
        "project_id": project.id, "category": "liability_inforce",
        "name": "Standard inforce", "fields": fields,
    })
    second = client.post("/v1/mapping-profiles", json={
        "project_id": project.id, "category": "liability_inforce",
        "name": "Standard inforce", "fields": [*fields[:-1], {
            **fields[-1], "transform": "number",
        }],
    })
    assert first.status_code == second.status_code == 201
    assert first.json()["version_number"] == 1
    assert second.json()["version_number"] == 2
    assert second.json()["parent_profile_id"] == first.json()["id"]

    profiles = client.get("/v1/mapping-profiles", params={
        "project_id": project.id, "category": "liability_inforce",
    }).json()["mapping_profiles"]
    assert [profile["version_number"] for profile in profiles] == [2, 1]
    opened = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "liability_inforce", "name": "Profile import",
    }).json()
    content = (
        b"policy_id,product_type,issue_date,issue_age,gender,premium,monthly_payment\n"
        b"P-1,SPIA,2024-01-01,65,F,100000,700\n"
    )
    assert client.post(f"/v1/import-sessions/{opened['id']}/files",
                       files={"file": ("inforce.csv", content)}).status_code == 200
    mapped = client.put(f"/v1/import-sessions/{opened['id']}/mapping",
                        json={"profile_id": second.json()["id"]})
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["mapping_profile_id"] == second.json()["id"]


def test_canonical_fingerprint_is_independent_of_upload_format(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    columns = ["age", "gender", "qx"]
    rows = [{"age": 65, "gender": "F", "qx": 0.01},
            {"age": 65, "gender": "M", "qx": 0.012}]
    csv = b"age,gender,qx\n65,F,0.01\n65,M,0.012\n"
    tsv = b"age\tgender\tqx\n65\tF\t0.01\n65\tM\t0.012\n"
    parquet_buffer = BytesIO()
    pl.DataFrame(rows).write_parquet(parquet_buffer)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(columns)
    for row in rows:
        sheet.append([row[column] for column in columns])
    excel_buffer = BytesIO()
    workbook.save(excel_buffer)
    files = [
        ("rates.csv", csv), ("rates.tsv", tsv),
        ("rates.parquet", parquet_buffer.getvalue()),
        ("rates.xlsx", excel_buffer.getvalue()),
    ]
    mapping = [{"source_column": key, "target_field": key} for key in columns]
    fingerprints = []
    raw_fingerprints = []
    for filename, content in files:
        opened = client.post("/v1/import-sessions", json={
            "project_id": project.id, "category": "assumption_table", "name": filename,
            "options": {"lookup_keys": ["age", "gender"], "value_column": "qx"},
        }).json()
        uploaded = client.post(f"/v1/import-sessions/{opened['id']}/files",
                               files={"file": (filename, content)})
        assert uploaded.status_code == 200, uploaded.text
        assert client.put(f"/v1/import-sessions/{opened['id']}/mapping",
                          json={"fields": mapping}).status_code == 200
        validated = client.post(f"/v1/import-sessions/{opened['id']}/validate")
        assert validated.status_code == 201, validated.text
        assert validated.json()["error_count"] == 0
        fingerprints.append(validated.json()["canonical_fingerprint"])
        raw_fingerprints.append(uploaded.json()["raw_fingerprint"])
    assert len(set(fingerprints)) == 1
    assert len(set(raw_fingerprints)) == 4


def test_scenario_validation_enforces_model_variable_governance(data_manager):
    client, db, project, _current, creator, _reviewer = data_manager
    model = Model(project_id=project.id, name="SPIA", product_code="SPIA", created_by=creator.id)
    db.add(model)
    db.flush()
    version = ModelVersion(model_id=model.id, version_label="v1", basis="best_estimate")
    variable = VariableRegistry(name="discount_rate_annual", data_type="number",
                                source_type="manual")
    db.add_all([version, variable])
    db.flush()
    db.add(ModelVariableDefinition(
        model_version_id=version.id, variable_name=variable.name, kind="manual",
        data_type="number", source={"type": "manual"}, allow_scenario_override=False,
    ))
    db.commit()
    csv = (
        "scenario_name,target_variable,operation,value,applies_from_period,applies_to_period\n"
        "Stress,discount_rate_annual,set,0.03,0,1200\n"
        "Stress,unknown_rate,set,0.02,0,1200\n"
        "Stress,discount_rate_annual,set,0.025,12,24\n"
    )
    opened = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "scenario", "name": "Governed stress",
        "options": {"model_version_id": version.id},
    }).json()
    assert client.post(f"/v1/import-sessions/{opened['id']}/files",
                       files={"file": ("stress.csv", csv)}).status_code == 200
    fields = [{"source_column": key, "target_field": key} for key in (
        "scenario_name", "target_variable", "operation", "value",
        "applies_from_period", "applies_to_period",
    )]
    assert client.put(f"/v1/import-sessions/{opened['id']}/mapping",
                      json={"fields": fields}).status_code == 200
    validation = client.post(f"/v1/import-sessions/{opened['id']}/validate").json()
    issues = client.get(f"/v1/validation-runs/{validation['id']}/issues").json()["issues"]
    assert {issue["code"] for issue in issues} >= {
        "SCENARIO_TARGET_NOT_OVERRIDABLE", "SCENARIO_TARGET_UNKNOWN",
        "SCENARIO_OVERRIDE_OVERLAP",
    }


def test_required_columns_table_ranges_and_unsupported_files_are_refused(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    opened = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "liability_inforce", "name": "Missing identity",
    }).json()
    missing = b"product_type,issue_date,issue_age,gender,premium,monthly_payment\nSPIA,2024-01-01,65,F,1,1\n"
    assert client.post(
        f"/v1/import-sessions/{opened['id']}/files", files={"file": ("missing.csv", missing)},
    ).status_code == 200
    fields = [{"source_column": key, "target_field": key} for key in (
        "product_type", "issue_date", "issue_age", "gender", "premium", "monthly_payment",
    )]
    assert client.put(f"/v1/import-sessions/{opened['id']}/mapping", json={"fields": fields}).status_code == 200
    validation = client.post(f"/v1/import-sessions/{opened['id']}/validate").json()
    codes = {item["code"] for item in client.get(
        f"/v1/validation-runs/{validation['id']}/issues"
    ).json()["issues"]}
    assert "REQUIRED_FIELD_NOT_MAPPED" in codes
    assert client.post(f"/v1/import-sessions/{opened['id']}/commit").status_code == 409

    table = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "assumption_table", "name": "Invalid mortality",
        "options": {"lookup_keys": ["age", "gender"], "value_column": "qx"},
    }).json()
    content = b"age,gender,qx\n65,F,1.4\n65,F,0.01\n"
    assert client.post(f"/v1/import-sessions/{table['id']}/files",
                       files={"file": ("mortality.csv", content)}).status_code == 200
    mapping = [{"source_column": key, "target_field": key} for key in ("age", "gender", "qx")]
    assert client.put(f"/v1/import-sessions/{table['id']}/mapping",
                      json={"fields": mapping}).status_code == 200
    table_validation = client.post(f"/v1/import-sessions/{table['id']}/validate").json()
    table_codes = {item["code"] for item in client.get(
        f"/v1/validation-runs/{table_validation['id']}/issues"
    ).json()["issues"]}
    assert table_codes >= {"PROBABILITY_RANGE", "DUPLICATE_KEY"}
    assert client.post(f"/v1/import-sessions/{table['id']}/commit").status_code == 409

    unsupported = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "scenario", "name": "Unsupported",
    }).json()
    response = client.post(f"/v1/import-sessions/{unsupported['id']}/files",
                           files={"file": ("input.pdf", b"not a dataset")})
    assert response.status_code == 400


def test_viewer_cannot_write_and_archived_project_rejects_import(data_manager):
    client, db, project, current, _creator, reviewer = data_manager
    current["user"] = principal(
        reviewer.id, "reviewer", ("imports:read", "imports:write", "projects:read"),
    )
    denied = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "scenario", "name": "Not allowed",
    })
    assert denied.status_code == 403
    current["user"] = principal("import-creator")
    project.archived_at = datetime.now(timezone.utc)
    db.commit()
    archived = client.post("/v1/import-sessions", json={
        "project_id": project.id, "category": "scenario", "name": "Read only",
    })
    assert archived.status_code == 409


def test_legacy_uploads_are_governed_one_shot_wrappers_and_store_every_category(data_manager):
    client, _db, project, _current, _creator, _reviewer = data_manager
    assumption = client.post(
        "/v1/imports/assumptions",
        params={"project_id": project.id, "table_name": "Mortality", "table_type": "mortality",
                "lookup_keys": "age,gender", "value_column": "qx"},
        files={"file": ("mortality.csv", b"age,gender,qx\n65,F,0.01\n65,M,0.012\n", "text/csv")},
    )
    assert assumption.status_code == 201, assumption.text
    assert assumption.json()["dataset_id"] and assumption.json()["stored_count"] == 2

    factor = client.post(
        "/v1/imports/factors", params={"project_id": project.id},
        files={"file": ("factor.csv", b"product,duration,factor\nSPIA,1,1.02\n", "text/csv")},
    )
    assert factor.status_code == 201, factor.text
    assert factor.json()["dataset_id"]

    scenarios = client.post(
        "/v1/imports/scenarios", params={"project_id": project.id},
        files={"file": (
            "scenarios.csv",
            b"scenario_id,target_variable,operation,value\nBase,rate,set,0.04\nStress,rate,set,0.03\n",
            "text/csv",
        )},
    )
    assert scenarios.status_code == 201, scenarios.text
    assert len(scenarios.json()["dataset_ids"]) == 2

    empty = client.post(
        "/v1/imports/inforce", params={"project_id": project.id},
        files={"file": ("empty.csv", b"policy_id\n", "text/csv")},
    )
    assert empty.status_code == 422
