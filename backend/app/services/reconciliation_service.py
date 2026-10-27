"""Expected-result ingestion and tolerance-based run reconciliation."""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.run import Run
from app.db.models.inforce import InforceRecord
from app.services import results_service
from app.services.common import ServiceError, fingerprint, iso, not_found, now_utc, unwrap_value


COLUMN_ALIASES = {
    "month": "projection_month",
    "policy_month": "projection_month",
    "account_value_bom": "account_value_open",
    "av_bom": "account_value_open",
    "account_value_eom": "account_value_close",
    "av_eom": "account_value_close",
    "credited_interest": "interest_credited",
    "surrender_charge": "surrender_charge_on_withdrawal",
    "mgsv_eom": "mgsv_close",
    "cash_surrender_value_eom": "surrender_benefit",
    "csv_eom": "surrender_benefit",
    "death_benefit_eom": "death_benefit",
    "net_insurer_cash_flow": "net_cash_flow",
}
IDENTITY_COLUMNS = {
    "policy_id", "projection_month", "projection_year", "policy_year", "scenario_id",
}


def _value(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    try:
        number = float(text)
        return int(number) if number.is_integer() and not any(c in text.lower() for c in (".", "e")) else number
    except ValueError:
        return text


def normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized = []
    columns: set[str] = set()
    for source in rows:
        row: dict[str, Any] = {}
        for key, value in source.items():
            if key is None or not str(key).strip():
                raise ServiceError(
                    422, "EXPECTED_RESULTS_COLUMN", "Expected-result column names cannot be empty."
                )
            source_name = str(key).strip()
            canonical_name = COLUMN_ALIASES.get(source_name, source_name)
            if canonical_name in row:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_DUPLICATE_COLUMN",
                    f"Multiple columns normalize to '{canonical_name}'.",
                )
            normalized_value = _value(value)
            if isinstance(normalized_value, float) and not math.isfinite(normalized_value):
                raise ServiceError(
                    422, "EXPECTED_RESULTS_NONFINITE",
                    f"Column '{canonical_name}' contains a non-finite numeric value.",
                )
            row[canonical_name] = normalized_value
        if "projection_month" in row and row["projection_month"] is not None:
            raw_month = row["projection_month"]
            try:
                row["projection_month"] = int(raw_month)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_PERIOD", "projection_month must be an integer."
                ) from exc
            if isinstance(raw_month, bool) or row["projection_month"] != raw_month:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_PERIOD", "projection_month must be an integer."
                )
            if row["projection_month"] < 0:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_PERIOD", "projection_month cannot be negative."
                )
            row.setdefault("projection_year", max(1, math.ceil(row["projection_month"] / 12)))
        if "projection_year" in row and row["projection_year"] is not None:
            raw_year = row["projection_year"]
            try:
                row["projection_year"] = int(raw_year)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_PERIOD", "projection_year must be an integer."
                ) from exc
            if isinstance(raw_year, bool) or row["projection_year"] != raw_year:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_PERIOD", "projection_year must be an integer."
                )
            if row["projection_year"] < 1:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_PERIOD", "projection_year must be at least 1."
                )
        normalized.append(dict(sorted(row.items())))
        columns.update(row)
    normalized.sort(key=lambda row: (
        str(row.get("policy_id") or ""), int(row.get("projection_month") or 0),
        int(row.get("projection_year") or 0),
    ))
    return normalized, sorted(columns)


def parse_csv(content: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ServiceError(422, "EXPECTED_RESULTS_ENCODING", "CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ServiceError(422, "EXPECTED_RESULTS_EMPTY", "CSV requires a header row.")
    return normalize_rows(list(reader))


def expected_view(row: ExpectedResultSet, include_rows: bool = False) -> dict[str, Any]:
    result = {
        "id": row.id, "project_id": row.project_id, "name": row.name,
        "product_code": row.product_code, "version_label": row.version_label,
        "source_reference": row.source_reference, "timing_convention": row.timing_convention,
        "granularity": row.granularity, "row_count": row.row_count,
        "columns_detected": row.columns_detected, "fingerprint": row.fingerprint,
        "status": row.status, "created_by": row.created_by,
        "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
    }
    if include_rows:
        result["rows"] = row.rows_data
    return result


def create_expected_set(db: Session, project_id: str, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
    source_rows = payload.get("rows")
    if not isinstance(source_rows, list) or any(not isinstance(row, dict) for row in source_rows):
        raise ServiceError(
            422, "EXPECTED_RESULTS_ROWS", "'rows' must be a JSON array of row objects."
        )
    rows, columns = normalize_rows(source_rows)
    if not rows:
        raise ServiceError(422, "EXPECTED_RESULTS_EMPTY", "Expected results require at least one row.")
    granularity = payload.get("granularity") or "policy_month"
    if granularity not in {"policy_month", "aggregate_year"}:
        raise ServiceError(422, "EXPECTED_RESULTS_GRANULARITY", "Unsupported expected-result granularity.")
    if granularity == "policy_month" and any(
        not str(row.get("policy_id") or "").strip() or row.get("projection_month") is None
        for row in rows
    ):
        raise ServiceError(
            422, "EXPECTED_RESULTS_KEYS",
            "Policy-month expected results require policy_id and projection_month on every row.",
        )
    if granularity == "aggregate_year" and any(row.get("projection_year") is None for row in rows):
        raise ServiceError(
            422, "EXPECTED_RESULTS_KEYS",
            "Aggregate-year expected results require projection_year or projection_month on every row.",
        )
    if not any(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for row in rows for name, value in row.items() if name not in IDENTITY_COLUMNS
    ):
        raise ServiceError(
            422, "EXPECTED_RESULTS_VALUES", "Expected results require at least one numeric result value."
        )
    row = ExpectedResultSet(
        project_id=project_id, name=payload["name"], product_code=payload["product_code"],
        version_label=payload.get("version_label") or "v1",
        source_reference=payload.get("source_reference") or "json-upload",
        timing_convention=payload.get("timing_convention"), granularity=granularity,
        row_count=len(rows), columns_detected=columns, rows_data=rows,
        fingerprint=fingerprint(rows), status="validated", created_by=actor_id,
    )
    db.add(row)
    db.flush()
    record_audit(db, actor_user_id=actor_id, action="expected_result_set.created",
                 entity_type="expected_result_set", entity_id=row.id,
                 after_state=expected_view(row))
    db.commit()
    db.refresh(row)
    return expected_view(row)


def report_view(row: ReconciliationReport) -> dict[str, Any]:
    return {
        "id": row.id, "project_id": row.project_id, "run_id": row.run_id,
        "expected_result_set_id": row.expected_result_set_id,
        "policy_id_filter": row.policy_id_filter, "abs_tol": row.abs_tol, "rel_tol": row.rel_tol,
        "status": row.status, "disposition_status": row.disposition_status,
        "disposition_notes": row.disposition_notes, "disposition_by": row.disposition_by,
        "disposition_at": iso(row.disposition_at), "summary": row.summary,
        "failures": row.failures, "created_by": row.created_by, "created_at": iso(row.created_at),
    }


def _cell_maps(
    expected: ExpectedResultSet, actual_rows: list[dict[str, Any]], policy_id: str | None,
    variables: list[str] | None, level: str,
) -> tuple[dict[tuple, float], dict[tuple, float]]:
    allowed = set(variables or [])
    expected_cells: dict[tuple, float] = {}
    for row in expected.rows_data:
        if policy_id and str(row.get("policy_id")) != policy_id:
            continue
        period = int(row.get("projection_month") or 0)
        year = int(row.get("projection_year") or math.ceil(period / 12))
        for variable, value in row.items():
            if (variable in IDENTITY_COLUMNS or isinstance(value, bool)
                    or not isinstance(value, (int, float))):
                continue
            if allowed and variable not in allowed:
                continue
            key = (str(row.get("policy_id") or ""), period, variable) if level == "policy_month" else (year, variable)
            expected_cells[key] = expected_cells.get(key, 0.0) + float(value)
    actual_cells: dict[tuple, float] = {}
    for row in actual_rows:
        if policy_id and row["policy_id"] != policy_id:
            continue
        variable = row["variable_name"]
        if allowed and variable not in allowed:
            continue
        value = unwrap_value(row["value"])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        month = int(row["projection_month"])
        key = (
            (row["policy_id"], month, variable)
            if level == "policy_month"
            else (max(1, math.ceil(month / 12)), variable)
        )
        actual_cells[key] = actual_cells.get(key, 0.0) + float(value)
    return expected_cells, actual_cells


def reconcile(db: Session, run_id: str, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
    run = results_service.result_run(db, run_id)
    expected = db.get(ExpectedResultSet, payload["expected_result_set_id"])
    if expected is None:
        raise not_found("Expected result set not found.")
    if expected.project_id != run.project_id:
        raise ServiceError(400, "EXPECTED_RESULTS_PROJECT_MISMATCH", "Expected results and run belong to different projects.")
    level = payload.get("level") or "policy_month"
    if level not in {"policy_month", "aggregate_year", "both"}:
        raise ServiceError(422, "RECONCILIATION_LEVEL", "level must be policy_month, aggregate_year, or both.")
    abs_tol, rel_tol = float(payload.get("abs_tol", 0.01)), float(payload.get("rel_tol", 0.0001))
    if not math.isfinite(abs_tol) or not math.isfinite(rel_tol) or abs_tol < 0 or rel_tol < 0:
        raise ServiceError(
            422, "RECONCILIATION_TOLERANCE", "Tolerances must be finite and non-negative."
        )
    actual_rows = results_service._accepted_rows(db, run)
    levels = ["policy_month", "aggregate_year"] if level == "both" else [level]
    failures, summaries = [], defaultdict(lambda: {"cells_checked": 0, "cells_passed": 0, "cells_failed": 0, "max_abs_diff": 0.0, "max_rel_diff": 0.0})
    checked = passed = 0
    max_abs = max_rel = 0.0
    for comparison_level in levels:
        expected_cells, actual_cells = _cell_maps(
            expected, actual_rows, payload.get("policy_id"), payload.get("variables"), comparison_level
        )
        for key in sorted(expected_cells):
            expected_value = expected_cells[key]
            actual_value = actual_cells.get(key)
            variable = key[-1]
            abs_diff = abs(actual_value - expected_value) if actual_value is not None else math.inf
            rel_diff = abs_diff / abs(expected_value) if expected_value else (0.0 if abs_diff == 0 else math.inf)
            tolerance = max(abs_tol, rel_tol * abs(expected_value))
            cell_passed = actual_value is not None and abs_diff <= tolerance
            checked += 1
            passed += int(cell_passed)
            # Missing actual cells are failures, but JSON evidence remains standards-compliant
            # (PostgreSQL JSON rejects Infinity).
            finite_abs = abs_diff if math.isfinite(abs_diff) else 0.0
            finite_rel = rel_diff if math.isfinite(rel_diff) else 0.0
            max_abs, max_rel = max(max_abs, finite_abs), max(max_rel, finite_rel)
            summary = summaries[variable]
            summary["cells_checked"] += 1
            summary["cells_passed"] += int(cell_passed)
            summary["cells_failed"] += int(not cell_passed)
            summary["max_abs_diff"] = max(summary["max_abs_diff"], finite_abs)
            summary["max_rel_diff"] = max(summary["max_rel_diff"], finite_rel)
            if not cell_passed:
                failure = {"level": comparison_level, "variable": variable,
                           "expected": expected_value, "actual": actual_value,
                           "abs_diff": None if not math.isfinite(abs_diff) else abs_diff,
                           "rel_diff": None if not math.isfinite(rel_diff) else rel_diff,
                           "tolerance_used": tolerance}
                if comparison_level == "policy_month":
                    failure.update({"policy_id": key[0], "projection_month": key[1]})
                else:
                    failure["projection_year"] = key[0]
                failures.append(failure)
    actual_policies = {
        row["policy_id"] for row in actual_rows
        if not payload.get("policy_id") or row["policy_id"] == payload["policy_id"]
    }
    expected_policies = {
        str(row.get("policy_id")) for row in expected.rows_data
        if row.get("policy_id") is not None
        and (not payload.get("policy_id") or str(row.get("policy_id")) == payload["policy_id"])
    }
    configuration = results_service.run_configuration(db, run)
    inforce_ids = [item.get("id") for item in (configuration.get("datasets") or {}).get("inforce", [])]
    source_records = db.query(InforceRecord).filter(InforceRecord.file_id.in_(inforce_ids)).all() if inforce_ids else []
    if payload.get("policy_id"):
        source_records = [item for item in source_records if item.policy_id == payload["policy_id"]]
    source_policies = {item.policy_id for item in source_records}
    month_one_open = {
        row["policy_id"]: unwrap_value(row["value"])
        for row in actual_rows
        if int(row["projection_month"]) == 1 and row["variable_name"] == "account_value_open"
        and (not payload.get("policy_id") or row["policy_id"] == payload["policy_id"])
    }
    source_accounts = {}
    source_amount_fields = set()
    for item in source_records:
        data = item.data or {}
        field = "account_value" if data.get("account_value") not in (None, "") else "premium"
        if data.get(field) not in (None, ""):
            source_accounts[item.policy_id] = _value(data[field])
            source_amount_fields.add(field)
    account_diffs = [
        abs(float(month_one_open[policy]) - float(value))
        for policy, value in source_accounts.items() if policy in month_one_open
    ]
    count_passed = (source_policies or expected_policies) == actual_policies
    account_available = bool(source_accounts and month_one_open)
    account_passed = not account_available or (
        len(account_diffs) == len(source_accounts) and all(diff <= abs_tol for diff in account_diffs)
    )
    source_check = {
        "passed": count_passed and account_passed,
        "source_policy_count": len(source_policies),
        "expected_policy_count": len(expected_policies),
        "actual_policy_count": len(actual_policies),
        "opening_account_value": {
            "available": account_available, "policies_checked": len(account_diffs),
            "max_abs_diff": max(account_diffs, default=0.0), "passed": account_passed,
            "source_fields": sorted(source_amount_fields),
        },
    }
    summary = {
        "total_variables_checked": len(summaries),
        "total_cells_checked": checked, "cells_passed": passed, "cells_failed": checked - passed,
        "max_abs_diff": max_abs, "max_rel_diff": max_rel,
        "per_variable_summary": dict(sorted(summaries.items())),
        "source_to_output_check": source_check,
    }
    if checked == 0:
        raise ServiceError(
            422, "NO_COMPARABLE_EXPECTED_RESULTS",
            "No numeric expected-result cells matched the requested policy and variables.",
        )
    report = ReconciliationReport(
        project_id=run.project_id, run_id=run.id, expected_result_set_id=expected.id,
        policy_id_filter=payload.get("policy_id"), abs_tol=abs_tol, rel_tol=rel_tol,
        status="PASS" if not failures and source_check["passed"] else "FAIL",
        summary=summary, failures=failures, created_by=actor_id,
    )
    db.add(report)
    db.flush()
    record_audit(db, actor_user_id=actor_id, action="reconciliation.created",
                 entity_type="reconciliation", entity_id=report.id,
                 after_state={"run_id": run.id, "status": report.status, "summary": summary})
    db.commit()
    db.refresh(report)
    return report_view(report)


def disposition(db: Session, report: ReconciliationReport, status: str, notes: str | None, actor_id: str) -> dict[str, Any]:
    if status not in {"approved", "exception_noted", "rejected"}:
        raise ServiceError(422, "RECONCILIATION_DISPOSITION", "Invalid disposition status.")
    before = report_view(report)
    report.disposition_status = status
    report.disposition_notes = notes
    report.disposition_by = actor_id
    report.disposition_at = now_utc()
    db.flush()
    record_audit(db, actor_user_id=actor_id, action="reconciliation.disposition_recorded",
                 entity_type="reconciliation", entity_id=report.id,
                 before_state=before, after_state=report_view(report))
    db.commit()
    db.refresh(report)
    return report_view(report)
