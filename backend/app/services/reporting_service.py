"""Governed disclosure-supporting report materialization from persisted evidence only."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.artifacts import (
    RUN_ARTIFACT_SCHEMAS,
    get_artifact_store,
    verified_artifact_bytes,
    verified_parquet_rows,
)
from app.core.audit import record_audit
from app.core.execution import run_package as run_package_contract
from app.db.models.governance import Report
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunPackage
from app.db.models.valuation_detail import ValuationRunDetail
from app.services import access, results_service
from app.services.common import ServiceError, conflict, iso, not_found

REPORT_SCHEMA_VERSION = "mentoramp.report/v1"
CLASSIFICATION = "DISCLOSURE_SUPPORTING_INFRASTRUCTURE_NON_FINAL"
GOVERNANCE_NOTICE = (
    "Illustrative disclosure-supporting infrastructure only. Presentation layouts and "
    "accounting conclusions remain pending ACT-DEC-09 and CAS Section 17 approval."
)


def _report(db: Session, report_id: str) -> Report:
    row = db.query(Report).filter(Report.id == report_id, Report.deleted_at.is_(None)).first()
    if row is None:
        raise not_found(f"Report '{report_id}' not found.")
    return row


def _verify_package(db: Session, run) -> tuple[RunPackage, dict[str, Any]]:
    package = db.query(RunPackage).filter(RunPackage.run_id == run.id).first()
    if package is None or not run.run_package_fingerprint:
        raise ServiceError(409, "RUN_PACKAGE_NOT_AVAILABLE", "The source run has no frozen run package.")
    if package.project_id != run.project_id or package.fingerprint != run.run_package_fingerprint:
        raise ServiceError(409, "PACKAGE_IDENTITY_MISMATCH", "Run-package identity does not match the source run.")
    try:
        configuration = run_package_contract.verify(package.package, package.fingerprint)
    except run_package_contract.RunPackageError as error:
        raise ServiceError(409, error.code, error.message, error.details) from error
    return package, configuration


def _verified_rows(db: Session, run) -> tuple[list[dict[str, Any]], dict[str, str]]:
    artifacts = db.query(RunArtifact).filter(
        RunArtifact.run_id == run.id,
        RunArtifact.attempt_number == run.accepted_attempt_number,
        RunArtifact.artifact_type.in_(("outputs", "traces")),
    ).order_by(RunArtifact.artifact_type, RunArtifact.storage_uri).all()
    output_rows: list[dict[str, Any]] = []
    checksums: dict[str, str] = {}
    store = get_artifact_store()
    for artifact in artifacts:
        rows = verified_parquet_rows(
            store,
            artifact.storage_uri,
            artifact.checksum_sha256,
            artifact.row_count,
            artifact.schema_version,
            supported_schemas=RUN_ARTIFACT_SCHEMAS,
            expected_run_id=run.id,
            expected_attempt_number=run.accepted_attempt_number,
        )
        checksums[f"{artifact.artifact_type}:{artifact.id}"] = artifact.checksum_sha256
        if artifact.artifact_type == "outputs":
            output_rows.extend(rows)
    if not output_rows:
        raise ServiceError(409, "OUTPUT_ARTIFACT_NOT_AVAILABLE", "No verified output artifact exists for the accepted attempt.")
    return output_rows, checksums


def _value(row: dict[str, Any]) -> Decimal | None:
    raw = row.get("value_json")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict) and "value" in parsed:
            parsed = parsed["value"]
        if parsed is None or isinstance(parsed, bool):
            return None
        return Decimal(str(parsed))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _output_summaries(rows: list[dict[str, Any]]) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    totals: dict[str, Decimal] = {}
    endings: dict[str, tuple[int, Decimal]] = {}
    for row in rows:
        name = str(row.get("variable_name") or "")
        value = _value(row)
        if not name or value is None:
            continue
        totals[name] = totals.get(name, Decimal("0")) + value
        month = int(row.get("projection_month") or 0)
        if name not in endings or month >= endings[name][0]:
            endings[name] = (month, value)
    return totals, {name: item[1] for name, item in endings.items()}


def _first(mapping: dict[str, Decimal], names: tuple[str, ...]) -> Decimal | None:
    return next((mapping[name] for name in names if name in mapping), None)


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _reconciliation(db: Session, run_id: str) -> dict[str, Any]:
    row = db.query(ReconciliationReport).filter(
        ReconciliationReport.run_id == run_id
    ).order_by(ReconciliationReport.created_at.desc()).first()
    if row is None:
        return {
            "status": "NOT_VERIFIABLE", "expected_result_set_id": None,
            "expected_result_status": None, "approved_by": None, "approved_at": None,
            "reason": "No reconciliation report is persisted for this run.",
        }
    expected = db.get(ExpectedResultSet, row.expected_result_set_id) if row.expected_result_set_id else None
    approved = bool(expected and expected.status == "approved" and expected.approved_by)
    return {
        "status": row.status if approved else "NOT_VERIFIABLE",
        "reconciliation_report_id": row.id,
        "expected_result_set_id": expected.id if expected else None,
        "expected_result_status": expected.status if expected else None,
        "approved_by": expected.approved_by if expected else None,
        "approved_at": iso(expected.approved_at) if expected and expected.approved_at else None,
        "summary": row.summary if approved else None,
        "reason": None if approved else "Expected results are absent or not four-eyes approved.",
    }


def _rollforward(db: Session, run_id: str) -> dict[str, Any]:
    summary = results_service.rollforward_summary(db, run_id)
    if summary["count"] == 0:
        return {
            "status": "UNAVAILABLE",
            "reason": "No rollforward summary persisted for this run.",
            "items": [],
            "recomputed_continuity_verified": None,
            "is_tampered_or_forged": False,
        }
    return {"status": "AVAILABLE", **summary}


def _csv_bytes(payload: dict[str, Any]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("section", "line_item", "value", "status"))
    for section_name in ("balance_sheet_components", "income_statement_components"):
        section = payload[section_name]
        for key, value in sorted(section.items()):
            if key in {"status", "reason"}:
                continue
            writer.writerow((section_name, key, "" if value is None else value, section.get("status", "AVAILABLE")))
    for item in payload["rollforward_and_attribution"].get("items", []):
        writer.writerow(("rollforward_and_attribution", item.get("component_name"), item.get("movement_amount"), payload["rollforward_and_attribution"]["status"]))
    return buffer.getvalue().encode("utf-8")


def _payload(db: Session, report: Report, run, configuration: dict[str, Any],
             output_rows: list[dict[str, Any]], checksums: dict[str, str]) -> dict[str, Any]:
    totals, endings = _output_summaries(output_rows)
    valuation = db.get(ValuationRunDetail, run.id)
    rollforward = _rollforward(db, run.id)
    reconciliation = _reconciliation(db, run.id)
    product_code = ((configuration.get("model") or {}).get("product_code"))
    scenario_id = (configuration.get("scenario") or {}).get("id")
    build = configuration.get("build") or {}

    ending_reserve = _first(endings, ("reserve", "statutory_reserve", "gaap_reserve"))
    ending_av = _first(endings, ("account_value_close", "total_account_value_close"))
    benefit_liability = _first(endings, ("benefit_base_close", "gmwb_remaining_close"))
    balance_available = any(value is not None for value in (ending_reserve, ending_av, benefit_liability))
    balance_sheet = {
        "status": "AVAILABLE" if balance_available else "UNAVAILABLE",
        "reason": None if balance_available else "No applicable persisted balance outputs are available.",
        "opening_reserve": None,
        "ending_reserve": _number(ending_reserve),
        "ending_account_value": _number(ending_av),
        "ending_benefit_liability": _number(benefit_liability),
        "valuation_reserve_summary": valuation.summary_results if valuation else None,
    }
    income_values = {
        "premiums": _number(_first(totals, ("expected_premium_inflow", "premium_cash_flow"))),
        "investment_or_credited_interest": _number(_first(totals, ("interest_credited",))),
        "fees_and_charges": _number(_first(totals, ("total_fees_collected", "fee"))),
        "benefits_claims_and_surrenders": _number(_first(totals, (
            "total_prt_outflow", "expected_claim_benefit_outgo", "liability_outgo",
            "rider_claims_incurred", "expected_payment",
        ))),
        "expenses": _number(_first(totals, ("expected_expense_outgo", "expected_admin_expense", "maintenance_expense"))),
        "reserve_delta": None,
    }
    income_available = any(value is not None for value in income_values.values())
    income_statement = {
        "status": "AVAILABLE" if income_available else "UNAVAILABLE",
        "reason": None if income_available else "No applicable persisted income-statement outputs are available.",
        **income_values,
    }
    disclosure_items = [{
        "basis": item.get("basis"), "methodology": item.get("methodology"),
        "product_code": product_code, "cohort_id": item.get("cohort_id"),
        "scenario_id": scenario_id, "projection_year": None,
        "component_name": item.get("component_name"),
        "movement_amount": item.get("movement_amount"),
        "closing_balance": item.get("closing_balance"),
    } for item in rollforward.get("items", [])]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "metadata": {
            "project_id": report.project_id, "report_id": report.id,
            "report_version": report.version_number, "source_run_id": run.id,
            "run_package_fingerprint": run.run_package_fingerprint,
            "artifact_checksums": checksums, "code_build_identity": build,
            "is_synthetic": bool(run.illustrative), "is_production_approved": False,
            "classification": CLASSIFICATION, "governance_notice": GOVERNANCE_NOTICE,
            "valuation_basis": run.basis if valuation else None,
            "methodology": run.methodology if valuation else None,
        },
        "balance_sheet_components": balance_sheet,
        "income_statement_components": income_statement,
        "rollforward_and_attribution": rollforward,
        "asu_2018_12_disclosure_supporting": {
            "status": "AVAILABLE" if disclosure_items else "UNAVAILABLE",
            "actuarial_review_status": "PENDING_ACT_DEC_09_AND_CAS_17",
            "items": disclosure_items,
        },
        "reconciliation_attestation": reconciliation,
    }


def generate(db: Session, project_id: str, report_id: str, user: Any) -> dict[str, Any]:
    report = _report(db, report_id)
    if report.project_id != project_id:
        raise not_found(f"Report '{report_id}' not found in project '{project_id}'.")
    access.require_project_access(db, user, project_id, write=True)
    if report.status == "published":
        raise conflict("Published report versions are immutable and cannot be regenerated.")
    if report.artifact_uri or report.artifact_fingerprint:
        raise conflict("This report version is already materialized; create a new version.")
    run_id = str((report.definition or {}).get("source_run_id") or "").strip()
    if not run_id:
        raise ServiceError(422, "SOURCE_RUN_REQUIRED", "Report definition requires source_run_id.")
    run = results_service.result_run(db, run_id, include_partial=True)
    if run.project_id != project_id:
        raise not_found(f"Source run '{run_id}' not found in project '{project_id}'.")
    _package, configuration = _verify_package(db, run)
    output_rows, checksums = _verified_rows(db, run)
    payload = _payload(db, report, run, configuration, output_rows, checksums)
    json_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    csv_bytes = _csv_bytes(payload)
    store = get_artifact_store()
    json_artifact = store.write_bytes(report.id, "reports", f"report-v{report.version_number}.json", json_bytes)
    csv_artifact = store.write_bytes(report.id, "reports", f"report-v{report.version_number}.csv", csv_bytes)
    report.artifact_uri = json_artifact.uri
    report.artifact_fingerprint = json_artifact.checksum_sha256
    report.definition = {
        **(report.definition or {}),
        "generated": {
            "schema_version": REPORT_SCHEMA_VERSION,
            "source_run_id": run.id,
            "is_synthetic": payload["metadata"]["is_synthetic"],
            "csv_artifact_uri": csv_artifact.uri,
            "csv_artifact_fingerprint": csv_artifact.checksum_sha256,
        },
    }
    report.updated_by = user.id
    db.flush()
    record_audit(
        db, actor_user_id=user.id, action="report.generated", entity_type="report",
        entity_id=report.id, after_state={
            "artifact_uri": report.artifact_uri,
            "artifact_fingerprint": report.artifact_fingerprint,
            "source_run_id": run.id,
        },
        context={"is_production_approved": False, "classification": CLASSIFICATION},
    )
    db.commit()
    return payload


def payload(db: Session, report_id: str) -> dict[str, Any]:
    report = _report(db, report_id)
    if not report.artifact_uri or not report.artifact_fingerprint:
        raise ServiceError(409, "REPORT_NOT_GENERATED", "The report has no generated payload.")
    content = verified_artifact_bytes(
        get_artifact_store(), report.artifact_uri, report.artifact_fingerprint
    )
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ServiceError(409, "REPORT_ARTIFACT_INVALID", "The report artifact is not valid JSON.") from error
    if (value.get("metadata") or {}).get("report_id") != report.id:
        raise ServiceError(409, "REPORT_ARTIFACT_IDENTITY_MISMATCH", "Report artifact identity does not match its record.")
    return value


def export_csv(db: Session, report_id: str, user: Any) -> tuple[bytes, Report]:
    report = _report(db, report_id)
    generated = (report.definition or {}).get("generated") or {}
    uri = generated.get("csv_artifact_uri")
    checksum = generated.get("csv_artifact_fingerprint")
    if not uri or not checksum:
        raise ServiceError(409, "REPORT_NOT_GENERATED", "The report has no generated CSV export.")
    content = verified_artifact_bytes(get_artifact_store(), uri, checksum)
    record_audit(
        db, actor_user_id=user.id, action="report.exported", entity_type="report",
        entity_id=report.id, after_state={"csv_artifact_fingerprint": checksum},
    )
    db.commit()
    return content, report
