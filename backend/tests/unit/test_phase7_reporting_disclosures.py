import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core.artifacts import get_artifact_store
from app.db.database import Base
from app.db.models.audit_log import AuditLog
from app.db.models.governance import Report
from app.db.models.project import Project
from app.db.models.project_member import ProjectMember
from app.db.models.run import Run
from app.db.models.user import User
from app.services import governance_service, reporting_service
from app.services.common import ServiceError


@pytest.fixture()
def reporting_db(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    user = User(id="report-user", email="report@example.com", full_name="Report User", password_hash="unused")
    project = Project(id="report-project", name="Report Project", created_by=user.id)
    run = Run(
        id="report-run", project_id=project.id, status="success", illustrative=True,
        accepted_attempt_number=1, run_package_fingerprint="a" * 64,
    )
    report = Report(
        id="report-id", project_id=project.id, name="Disclosure support",
        report_type="disclosure_supporting", definition={"source_run_id": run.id},
        created_by=user.id, updated_by=user.id,
    )
    db.add_all([user, project, run, report])
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner", created_by=user.id))
    db.commit()
    monkeypatch.setattr(settings, "artifact_storage_path", tmp_path / "artifacts")
    try:
        yield db, user, project, run, report
    finally:
        db.close()
        engine.dispose()


def test_report_payload_is_honest_when_optional_persisted_evidence_is_absent(reporting_db):
    db, _user, _project, run, report = reporting_db
    configuration = {
        "model": {"product_code": "VA"},
        "scenario": {"id": "BASE"},
        "build": {"source_commit": "abc123", "engine_version": "m3-cpu-dsl-1"},
    }
    rows = [
        {"variable_name": "account_value_close", "projection_month": 1, "value_json": "80000"},
        {"variable_name": "total_fees_collected", "projection_month": 1, "value_json": "100"},
    ]
    payload = reporting_service._payload(
        db, report, run, configuration, rows, {"outputs:artifact": "b" * 64}
    )
    assert payload["schema_version"] == "mentoramp.report/v1"
    assert payload["metadata"]["is_synthetic"] is True
    assert payload["metadata"]["is_production_approved"] is False
    assert payload["metadata"]["valuation_basis"] is None
    assert payload["metadata"]["methodology"] is None
    assert payload["balance_sheet_components"]["ending_account_value"] == 80_000.0
    assert payload["balance_sheet_components"]["valuation_reserve_summary"] is None
    assert payload["rollforward_and_attribution"] == {
        "status": "UNAVAILABLE",
        "reason": "No rollforward summary persisted for this run.",
        "items": [],
        "recomputed_continuity_verified": None,
        "is_tampered_or_forged": False,
    }
    assert payload["reconciliation_attestation"]["status"] == "NOT_VERIFIABLE"
    assert payload["asu_2018_12_disclosure_supporting"]["actuarial_review_status"] == (
        "PENDING_ACT_DEC_09_AND_CAS_17"
    )


def test_report_ending_balances_sum_all_policies_at_latest_month():
    totals, endings = reporting_service._output_summaries([
        {"policy_id": "A", "variable_name": "reserve", "projection_month": 1, "value_json": "10"},
        {"policy_id": "A", "variable_name": "reserve", "projection_month": 2, "value_json": "80"},
        {"policy_id": "B", "variable_name": "reserve", "projection_month": 2, "value_json": "20"},
    ])
    assert totals["reserve"] == 110
    assert endings["reserve"] == 100


def test_generated_report_cannot_be_edited_in_place(reporting_db):
    db, user, _project, _run, report = reporting_db
    report.artifact_uri = "local://reports/report.json"
    report.artifact_fingerprint = "b" * 64
    db.commit()

    with pytest.raises(ServiceError) as generated:
        governance_service.update_versioned(
            db, Report, report.id, {"name": "Mutated generated report"}, SimpleNamespace(id=user.id)
        )
    assert generated.value.status_code == 409
    assert "create a new version" in generated.value.message


def test_report_payload_and_csv_fail_closed_on_physical_tamper(reporting_db):
    db, user, _project, _run, report = reporting_db
    store = get_artifact_store()
    value = {
        "schema_version": "mentoramp.report/v1",
        "metadata": {"report_id": report.id, "is_synthetic": True},
    }
    stored_json = store.write_bytes(
        report.id, "reports", "report.json",
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
    )
    stored_csv = store.write_bytes(report.id, "reports", "report.csv", b"section,line_item,value,status\n")
    report.artifact_uri = stored_json.uri
    report.artifact_fingerprint = stored_json.checksum_sha256
    report.definition = {
        **report.definition,
        "generated": {
            "csv_artifact_uri": stored_csv.uri,
            "csv_artifact_fingerprint": stored_csv.checksum_sha256,
        },
    }
    db.commit()
    assert reporting_service.payload(db, report.id) == value
    content, _ = reporting_service.export_csv(db, report.id, SimpleNamespace(id=user.id))
    assert content.startswith(b"section,line_item")
    assert db.query(AuditLog).filter(AuditLog.action == "report.exported").count() == 1

    store.path_for_uri(stored_json.uri).write_bytes(b"x" + store.read_bytes(stored_json.uri))
    with pytest.raises(ServiceError) as json_error:
        reporting_service.payload(db, report.id)
    assert json_error.value.code == "ARTIFACT_CHECKSUM_MISMATCH"

    store.path_for_uri(stored_csv.uri).write_bytes(b"tampered")
    with pytest.raises(ServiceError) as csv_error:
        reporting_service.export_csv(db, report.id, SimpleNamespace(id=user.id))
    assert csv_error.value.code == "ARTIFACT_CHECKSUM_MISMATCH"


def test_published_report_cannot_be_generated_and_cross_project_is_hidden(reporting_db):
    db, user, project, _run, report = reporting_db
    report.status = "published"
    db.commit()
    with pytest.raises(ServiceError) as published:
        reporting_service.generate(db, project.id, report.id, SimpleNamespace(id=user.id))
    assert published.value.status_code == 409

    with pytest.raises(ServiceError) as cross_project:
        reporting_service.generate(db, "another-project", report.id, SimpleNamespace(id=user.id))
    assert cross_project.value.status_code == 404
