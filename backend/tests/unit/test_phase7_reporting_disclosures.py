import hashlib
import json
from types import SimpleNamespace

import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core.artifacts import IDENTITY_NEUTRAL_SCHEMA, get_artifact_store
from app.core.execution import run_package as run_package_contract
from app.db.database import Base
from app.db.models.audit_log import AuditLog
from app.db.models.governance import Report
from app.db.models.project import Project
from app.db.models.project_member import ProjectMember
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact, RunManifest
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.user import User
from app.services import governance_service, reporting_service
from app.services.common import ServiceError, now_utc


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
        "metadata": {
            "report_id": report.id,
            "report_version": report.version_number,
            "is_synthetic": True,
        },
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
            "report_id": report.id,
            "report_version": report.version_number,
            "csv_artifact_uri": stored_csv.uri,
            "csv_artifact_fingerprint": stored_csv.checksum_sha256,
        },
    }
    db.commit()
    assert reporting_service.payload(db, report.id) == value
    content, _ = reporting_service.export_csv(db, report.id, SimpleNamespace(id=user.id))
    assert content.startswith(b"section,line_item")
    assert db.query(AuditLog).filter(AuditLog.action == "report.exported").count() == 1

    # Tamper CSV first while JSON is intact
    orig_csv = store.read_bytes(stored_csv.uri)
    store.path_for_uri(stored_csv.uri).write_bytes(b"tampered")
    with pytest.raises(ServiceError) as csv_error:
        reporting_service.export_csv(db, report.id, SimpleNamespace(id=user.id))
    assert csv_error.value.code == "ARTIFACT_CHECKSUM_MISMATCH"
    store.path_for_uri(stored_csv.uri).write_bytes(orig_csv)

    # Tamper JSON -> both payload and export_csv fail closed
    store.path_for_uri(stored_json.uri).write_bytes(b"x" + store.read_bytes(stored_json.uri))
    with pytest.raises(ServiceError) as json_error:
        reporting_service.payload(db, report.id)
    assert json_error.value.code == "ARTIFACT_CHECKSUM_MISMATCH"
    with pytest.raises(ServiceError) as csv_after_json_tamper:
        reporting_service.export_csv(db, report.id, SimpleNamespace(id=user.id))
    assert csv_after_json_tamper.value.code == "ARTIFACT_CHECKSUM_MISMATCH"


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


def _materialize_governed_run(
    db,
    *,
    project_id: str,
    user_id: str,
    run_id: str,
    status: str = "success",
    error_count: int = 0,
    reserve_value: float = 12_500.0,
    fees_value: float = 250.0,
) -> tuple[Run, RunArtifact]:
    """Persist a checksum-verified Run + RunPackage + RunAttempt + RunManifest + Parquet artifact."""
    pkg_doc = run_package_contract.seal(
        identity={
            "package_id": f"pkg-{run_id}",
            "frozen_for_run_id": run_id,
            "project_id": project_id,
            "created_at": "2026-11-26T00:00:00Z",
            "created_by": {"id": user_id},
        },
        governance={"governance_mode": "sandbox"},
        configuration={
            "model": {"product_code": "VA"},
            "scenario": {"id": "BASE", "name": "Base"},
            "build": {"source_commit": "test-sha", "engine_version": "m3-cpu-dsl-1"},
        },
    )
    manifest_doc = {"run_id": run_id, "status": status, "attempt_number": 1}
    manifest_fp = hashlib.sha256(json.dumps(manifest_doc, sort_keys=True).encode()).hexdigest()
    run = Run(
        id=run_id,
        project_id=project_id,
        status=status,
        illustrative=True,
        accepted_attempt_number=1,
        attempt_count=1,
        error_count=error_count,
        warning_count=0,
        run_package_fingerprint=pkg_doc["fingerprint"],
        final_manifest_fingerprint=manifest_fp,
    )
    pkg = RunPackage(
        id=f"pkg-{run_id}",
        run_id=run_id,
        project_id=project_id,
        schema_version=pkg_doc["schema_version"],
        fingerprint_algorithm=pkg_doc["fingerprint_algorithm"],
        fingerprint=pkg_doc["fingerprint"],
        package=pkg_doc,
    )
    attempt = RunAttempt(
        run_id=run_id,
        attempt_number=1,
        status=status,
        worker_id="test-worker",
        started_at=now_utc(),
        completed_at=now_utc(),
    )
    manifest = RunManifest(
        run_id=run_id,
        attempt_number=1,
        fingerprint=manifest_fp,
        run_package_id=pkg.id,
        run_package_fingerprint=pkg.fingerprint,
        schema_version="m2-v1",
        manifest=manifest_doc,
    )
    rows = [
        {
            "policy_id": "POL-1",
            "scenario_id": "BASE",
            "projection_month": 1,
            "variable_name": "reserve",
            "value_json": json.dumps(reserve_value),
        },
        {
            "policy_id": "POL-1",
            "scenario_id": "BASE",
            "projection_month": 1,
            "variable_name": "expected_fees_collected",
            "value_json": json.dumps(fees_value),
        },
    ]
    store = get_artifact_store()
    written = store.write_rows(run_id, "outputs", rows, attempt_number=1, schema_version=IDENTITY_NEUTRAL_SCHEMA)
    artifact = RunArtifact(
        id=f"art-{run_id}",
        run_id=run_id,
        attempt_number=1,
        artifact_type="outputs",
        storage_uri=written.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version=IDENTITY_NEUTRAL_SCHEMA,
        row_count=len(rows),
        checksum_sha256=written.checksum_sha256,
    )
    db.add_all([run, pkg, attempt, manifest, artifact])
    db.commit()
    return run, artifact


def test_report_v1_to_v2_version_and_artifact_identity_regression(reporting_db):
    """Exact 8-step regression test required by Phase 7 review Section 1."""
    db, user, project, _unused_run, _unused_report = reporting_db
    actor = SimpleNamespace(id=user.id)
    run_v1, _ = _materialize_governed_run(
        db, project_id=project.id, user_id=user.id, run_id="run-v1", reserve_value=10_000.0
    )
    run_v2, _ = _materialize_governed_run(
        db, project_id=project.id, user_id=user.id, run_id="run-v2", reserve_value=25_000.0
    )

    # 1. Create, generate, and publish Report v1
    v1_created = governance_service.create_report(
        db,
        project.id,
        {
            "name": "Quarterly Disclosure Support",
            "report_type": "disclosure_supporting",
            "definition": {"source_run_id": run_v1.id, "layout": "summary"},
        },
        actor,
    )
    v1_id = v1_created["id"]
    # Ungenerated v1 cannot be published
    with pytest.raises(ServiceError) as ungen_v1_pub:
        governance_service.publish_versioned(db, Report, v1_id, actor)
    assert ungen_v1_pub.value.status_code == 409
    assert ungen_v1_pub.value.code == "REPORT_NOT_GENERATED"

    v1_payload = reporting_service.generate(db, project.id, v1_id, actor)
    v1_csv_before, _ = reporting_service.export_csv(db, v1_id, actor)
    v1_published = governance_service.publish_versioned(db, Report, v1_id, actor)
    v1_record_before = db.get(Report, v1_id)
    v1_published_at = v1_record_before.published_at
    v1_artifact_uri = v1_record_before.artifact_uri
    v1_artifact_fp = v1_record_before.artifact_fingerprint
    assert v1_published["status"] == "published"

    # 2. Create Report v2 from published v1
    v2_created = governance_service.version_versioned(db, Report, v1_id, actor)
    v2_id = v2_created["id"]
    assert v2_created["version_number"] == 2
    assert v2_created["definition"]["layout"] == "summary"
    assert v2_created["definition"]["source_run_id"] == run_v1.id
    assert "generated" not in v2_created["definition"]
    assert "artifact_uri" not in v2_created
    assert "artifact_fingerprint" not in v2_created

    # 3 & 4. Attempt v2 CSV export, payload read, and publish BEFORE generation -> 409 REPORT_NOT_GENERATED
    for action in (
        lambda: reporting_service.export_csv(db, v2_id, actor),
        lambda: reporting_service.payload(db, v2_id),
        lambda: governance_service.publish_versioned(db, Report, v2_id, actor),
    ):
        with pytest.raises(ServiceError) as exc:
            action()
        assert exc.value.status_code == 409
        assert exc.value.code == "REPORT_NOT_GENERATED"

    # Point v2 to run_v2 (allowed before v2 is generated) and verify reserved 'generated' key is rejected
    with pytest.raises(ServiceError) as reserved_exc:
        governance_service.update_versioned(
            db, Report, v2_id, {"definition": {"source_run_id": run_v2.id, "generated": {"csv_artifact_uri": "x"}}}, actor
        )
    assert reserved_exc.value.status_code == 422
    governance_service.update_versioned(
        db, Report, v2_id, {"definition": {"source_run_id": run_v2.id, "layout": "summary"}}, actor
    )

    # 5 & 6. Generate v2 and verify v2 source identity, checksum, and artifacts
    v2_payload = reporting_service.generate(db, project.id, v2_id, actor)
    v2_record = db.get(Report, v2_id)
    v2_csv, _ = reporting_service.export_csv(db, v2_id, actor)
    assert v2_payload["metadata"]["report_id"] == v2_id
    assert v2_payload["metadata"]["report_version"] == 2
    assert v2_payload["metadata"]["source_run_id"] == run_v2.id
    assert v2_payload["balance_sheet_components"]["ending_reserve"] == 25_000.0
    assert v2_record.artifact_uri != v1_artifact_uri
    assert v2_record.artifact_fingerprint != v1_artifact_fp
    assert v2_record.definition["generated"]["report_id"] == v2_id
    assert v2_record.definition["generated"]["report_version"] == 2
    assert v2_csv != v1_csv_before
    assert b"25000.0" in v2_csv
    assert reporting_service.payload(db, v2_id) == v2_payload

    # 7. Verify v1 is completely unchanged
    db.expire_all()
    v1_record_after = db.get(Report, v1_id)
    assert v1_record_after.artifact_uri == v1_artifact_uri
    assert v1_record_after.artifact_fingerprint == v1_artifact_fp
    assert v1_record_after.published_at == v1_published_at
    assert reporting_service.payload(db, v1_id) == v1_payload
    v1_csv_after, _ = reporting_service.export_csv(db, v1_id, actor)
    assert v1_csv_after == v1_csv_before

    # 8. Verify repeated publication and mutation cannot alter a published report
    with pytest.raises(ServiceError) as repeat_pub:
        governance_service.publish_versioned(db, Report, v1_id, actor)
    assert repeat_pub.value.status_code == 409
    assert repeat_pub.value.code == "ALREADY_PUBLISHED"
    with pytest.raises(ServiceError) as mutate_pub:
        governance_service.update_versioned(db, Report, v1_id, {"description": "tampered"}, actor)
    assert mutate_pub.value.status_code == 409
    assert db.get(Report, v1_id).published_at == v1_published_at


def test_partial_success_run_report_is_explicitly_marked_incomplete(reporting_db):
    db, user, project, _unused_run, _unused_report = reporting_db
    actor = SimpleNamespace(id=user.id)
    partial_run, _ = _materialize_governed_run(
        db,
        project_id=project.id,
        user_id=user.id,
        run_id="run-partial-1",
        status="partial_success",
        error_count=3,
        reserve_value=8_000.0,
    )
    created = governance_service.create_report(
        db,
        project.id,
        {"name": "Partial Report", "report_type": "disclosure_supporting", "definition": {"source_run_id": partial_run.id}},
        actor,
    )
    payload = reporting_service.generate(db, project.id, created["id"], actor)
    meta = payload["metadata"]
    assert meta["source_run_status"] == "partial_success"
    assert meta["accepted_attempt_number"] == 1
    assert meta["policy_error_count"] == 3
    assert meta["is_complete"] is False
    assert meta["completeness_status"] == "INCOMPLETE_PARTIAL_RUN"
    assert "incomplete" in meta["completeness_notice"].lower()
    assert payload["balance_sheet_components"]["status"] == "PARTIAL_INCOMPLETE"
    assert payload["balance_sheet_components"]["is_complete"] is False
    assert payload["income_statement_components"]["status"] == "PARTIAL_INCOMPLETE"
    assert payload["income_statement_components"]["is_complete"] is False
    csv_bytes, report_row = reporting_service.export_csv(db, created["id"], actor)
    assert b"INCOMPLETE_PARTIAL_RUN" in csv_bytes
    assert b"PARTIAL_INCOMPLETE" in csv_bytes
    assert report_row.definition["generated"]["is_complete"] is False


def test_rollforward_continuity_gates_unverified_failed_and_tampered_states(reporting_db):
    db, user, project, _unused_run, _unused_report = reporting_db
    actor = SimpleNamespace(id=user.id)

    # Case A: Unverified stored continuity flag (arithmetic matches, but stored flag is False)
    run_unv, _ = _materialize_governed_run(db, project_id=project.id, user_id=user.id, run_id="run-rf-unv")
    db.add_all([
        RollforwardSummary(
            id="rf-unv-1", run_id=run_unv.id, project_id=project.id, cohort_id="C1",
            methodology="LDTI", basis="GAAP", component_name="opening_balance",
            component_order=1, is_movement=False, opening_balance=100.0,
            movement_amount=0.0, closing_balance=100.0, is_continuity_verified=False,
            trace_identifier="UNVERIFIED:test-1",
        ),
        RollforwardSummary(
            id="rf-unv-2", run_id=run_unv.id, project_id=project.id, cohort_id="C1",
            methodology="LDTI", basis="GAAP", component_name="ending_balance",
            component_order=2, is_movement=False, opening_balance=100.0,
            movement_amount=0.0, closing_balance=100.0, is_continuity_verified=False,
            trace_identifier="UNVERIFIED:test-1",
        ),
    ])
    db.commit()
    rep_unv = governance_service.create_report(
        db, project.id, {"name": "RF Unverified", "report_type": "disclosure_supporting", "definition": {"source_run_id": run_unv.id}}, actor
    )
    payload_unv = reporting_service.generate(db, project.id, rep_unv["id"], actor)
    assert payload_unv["rollforward_and_attribution"]["status"] == "NOT_VERIFIABLE"
    assert payload_unv["asu_2018_12_disclosure_supporting"]["status"] == "NOT_VERIFIABLE"
    assert payload_unv["asu_2018_12_disclosure_supporting"]["items"] == []

    # Case B: Failed rollforward continuity with tampered stored_continuity_flag=True
    run_fail, _ = _materialize_governed_run(db, project_id=project.id, user_id=user.id, run_id="run-rf-fail")
    db.add_all([
        RollforwardSummary(
            id="rf-fail-1", run_id=run_fail.id, project_id=project.id, cohort_id="C1",
            methodology="LDTI", basis="GAAP", component_name="opening_balance",
            component_order=1, is_movement=False, opening_balance=100.0,
            movement_amount=0.0, closing_balance=100.0, is_continuity_verified=True,
            trace_identifier="VERIFIED:test-2",
        ),
        RollforwardSummary(
            id="rf-fail-2", run_id=run_fail.id, project_id=project.id, cohort_id="C1",
            methodology="LDTI", basis="GAAP", component_name="interest",
            component_order=2, is_movement=True, opening_balance=100.0,
            movement_amount=10.0, closing_balance=110.0, is_continuity_verified=True,
            trace_identifier="VERIFIED:test-2",
        ),
        RollforwardSummary(
            id="rf-fail-3", run_id=run_fail.id, project_id=project.id, cohort_id="C1",
            methodology="LDTI", basis="GAAP", component_name="ending_balance",
            component_order=3, is_movement=False, opening_balance=100.0,
            movement_amount=0.0, closing_balance=999.0, is_continuity_verified=True,
            trace_identifier="VERIFIED:test-2",
        ),
    ])
    db.commit()
    rep_fail = governance_service.create_report(
        db, project.id, {"name": "RF Failed", "report_type": "disclosure_supporting", "definition": {"source_run_id": run_fail.id}}, actor
    )
    payload_fail = reporting_service.generate(db, project.id, rep_fail["id"], actor)
    assert payload_fail["rollforward_and_attribution"]["status"] == "CONTINUITY_FAILED"
    assert payload_fail["rollforward_and_attribution"]["recomputed_continuity_verified"] is False
    assert payload_fail["rollforward_and_attribution"]["is_tampered_or_forged"] is True
    assert payload_fail["asu_2018_12_disclosure_supporting"]["status"] == "NOT_VERIFIABLE"
    assert payload_fail["asu_2018_12_disclosure_supporting"]["items"] == []


def test_unapproved_reconciliation_and_tampered_parquet_fail_closed(reporting_db):
    db, user, project, _unused_run, _unused_report = reporting_db
    actor = SimpleNamespace(id=user.id)
    run, parquet_art = _materialize_governed_run(db, project_id=project.id, user_id=user.id, run_id="run-rec-pq")

    # Unapproved expected result set -> reconciliation_attestation MUST remain NOT_VERIFIABLE
    ers = ExpectedResultSet(
        id="ers-unapproved",
        project_id=project.id,
        name="Unapproved expected",
        product_code="VA",
        version_label="v1",
        source_reference="TEST",
        row_count=1,
        rows_data=[{"reserve": 12_500.0}],
        status="validated",  # NOT approved!
        fingerprint="c" * 64,
        created_by=user.id,
    )
    rec = ReconciliationReport(
        id="rec-unapproved",
        project_id=project.id,
        run_id=run.id,
        expected_result_set_id=ers.id,
        variables_requested=["reserve"],
        status="PASS",
        summary={"passed": 1},
    )
    db.add_all([ers, rec])
    db.commit()

    rep_rec = governance_service.create_report(
        db, project.id, {"name": "Rec Check", "report_type": "disclosure_supporting", "definition": {"source_run_id": run.id}}, actor
    )
    payload = reporting_service.generate(db, project.id, rep_rec["id"], actor)
    assert payload["reconciliation_attestation"]["status"] == "NOT_VERIFIABLE"
    assert payload["reconciliation_attestation"]["summary"] is None

    # Physical tamper of source Parquet output artifact -> generate fails closed
    store = get_artifact_store()
    store.path_for_uri(parquet_art.storage_uri).write_bytes(b"corrupted-parquet-bytes")
    rep_pq = governance_service.create_report(
        db, project.id, {"name": "Parquet Tamper", "report_type": "disclosure_supporting", "definition": {"source_run_id": run.id}}, actor
    )
    with pytest.raises(ServiceError) as pq_err:
        reporting_service.generate(db, project.id, rep_pq["id"], actor)
    assert pq_err.value.code == "ARTIFACT_CHECKSUM_MISMATCH"


def test_report_publication_verifies_physical_json_and_csv_artifacts_without_export_audit(reporting_db):
    """Exact A-I regression test required by Phase 7 Conditional Technical Pass Section 1."""
    db, user, project, _unused_run, _unused_report = reporting_db
    actor = SimpleNamespace(id=user.id)
    run, _ = _materialize_governed_run(
        db, project_id=project.id, user_id=user.id, run_id="run-pub-safeguard", reserve_value=15_000.0
    )

    # A. Generate a valid report
    created = governance_service.create_report(
        db,
        project.id,
        {
            "name": "Publication Safeguard Report",
            "report_type": "disclosure_supporting",
            "definition": {"source_run_id": run.id},
        },
        actor,
    )
    report_id = created["id"]
    reporting_service.generate(db, project.id, report_id, actor)
    record = db.get(Report, report_id)
    store = get_artifact_store()
    json_path = store.path_for_uri(record.artifact_uri)
    csv_path = store.path_for_uri(record.definition["generated"]["csv_artifact_uri"])
    original_json = json_path.read_bytes()
    original_csv = csv_path.read_bytes()

    # B, C, D. Tamper with CSV bytes -> publication fails with 409; status stays draft; no export/publish audit
    csv_path.write_bytes(b"tampered-csv-content\n")
    with pytest.raises(ServiceError) as csv_pub_err:
        governance_service.publish_versioned(db, Report, report_id, actor)
    assert csv_pub_err.value.status_code == 409
    assert csv_pub_err.value.code == "ARTIFACT_CHECKSUM_MISMATCH"
    db.expire_all()
    assert db.get(Report, report_id).status == "draft"
    assert db.get(Report, report_id).published_at is None
    assert db.query(AuditLog).filter(AuditLog.entity_id == report_id, AuditLog.action.in_(("report.exported", "report.published"))).count() == 0

    # E, F. Restore CSV; tamper with JSON bytes -> publication fails with 409; status stays draft; no export/publish audit
    csv_path.write_bytes(original_csv)
    json_path.write_bytes(b"x" + original_json)
    with pytest.raises(ServiceError) as json_pub_err:
        governance_service.publish_versioned(db, Report, report_id, actor)
    assert json_pub_err.value.status_code == 409
    assert json_pub_err.value.code == "ARTIFACT_CHECKSUM_MISMATCH"
    db.expire_all()
    assert db.get(Report, report_id).status == "draft"
    assert db.get(Report, report_id).published_at is None
    assert db.query(AuditLog).filter(AuditLog.entity_id == report_id, AuditLog.action.in_(("report.exported", "report.published"))).count() == 0

    # Extra safeguard check: missing physical artifact file fails closed with 409 ARTIFACT_MISSING
    json_path.write_bytes(original_json)
    csv_path.unlink()
    with pytest.raises(ServiceError) as missing_err:
        governance_service.publish_versioned(db, Report, report_id, actor)
    assert missing_err.value.status_code == 409
    assert missing_err.value.code == "ARTIFACT_MISSING"
    db.expire_all()
    assert db.get(Report, report_id).status == "draft"

    # G, H. Restore original artifacts -> publish succeeds exactly once with ZERO report.exported audit events
    csv_path.write_bytes(original_csv)
    published = governance_service.publish_versioned(db, Report, report_id, actor)
    assert published["status"] == "published"
    published_at = db.get(Report, report_id).published_at
    assert published_at is not None
    assert db.query(AuditLog).filter(AuditLog.entity_id == report_id, AuditLog.action == "report.published").count() == 1
    assert db.query(AuditLog).filter(AuditLog.entity_id == report_id, AuditLog.action == "report.exported").count() == 0

    # I. Reject repeated publication without mutating published report or adding duplicate publish audits
    with pytest.raises(ServiceError) as repeat_err:
        governance_service.publish_versioned(db, Report, report_id, actor)
    assert repeat_err.value.status_code == 409
    assert repeat_err.value.code == "ALREADY_PUBLISHED"
    db.expire_all()
    assert db.get(Report, report_id).published_at == published_at
    assert db.query(AuditLog).filter(AuditLog.entity_id == report_id, AuditLog.action == "report.published").count() == 1
    assert db.query(AuditLog).filter(AuditLog.entity_id == report_id, AuditLog.action == "report.exported").count() == 0
