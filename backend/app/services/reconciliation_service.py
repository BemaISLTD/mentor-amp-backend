"""Expected-result ingestion and tolerance-based run reconciliation."""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.db.models.inforce import InforceRecord
from app.db.models.modeling import ModelPublishedOutput
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.run import Run
from app.services import access, results_service
from app.services.common import ServiceError, fingerprint, iso, not_found, now_utc, unwrap_value

MAX_EXPECTED_ROWS = 50_000
MAX_VARIABLES_PER_RECONCILIATION = 100
MAX_REPORT_FAILURES = 1_000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

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

STRING_IDENTITY_COLUMNS = {
    "policy_id", "scenario_id", "cohort_id", "segment_id", "product_code",
}

INTEGER_PERIOD_COLUMNS = {
    "projection_month", "projection_year", "policy_year", "month", "year",
}

IDENTITY_COLUMNS = STRING_IDENTITY_COLUMNS | INTEGER_PERIOD_COLUMNS


def _coerce_field_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in STRING_IDENTITY_COLUMNS:
        # P1 Section 11: String identity fields must NEVER be coerced to numbers (e.g. "000123" stays "000123")
        return str(value).strip()

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ServiceError(422, "EXPECTED_RESULTS_NONFINITE", f"Column '{key}' contains a non-finite numeric value.")
        return value

    text = str(value).strip()
    if text == "":
        return None

    if key in INTEGER_PERIOD_COLUMNS:
        try:
            return int(text)
        except (ValueError, TypeError) as exc:
            raise ServiceError(422, "EXPECTED_RESULTS_PERIOD", f"Column '{key}' must be an integer, got '{text}'.") from exc

    try:
        number = float(text)
        if not math.isfinite(number):
            raise ServiceError(422, "EXPECTED_RESULTS_NONFINITE", f"Column '{key}' contains a non-finite numeric value.")
        return int(number) if number.is_integer() and not any(c in text.lower() for c in (".", "e")) else number
    except ValueError:
        return text


def normalize_rows(
    rows: list[dict[str, Any]], granularity: str = "policy_month"
) -> tuple[list[dict[str, Any]], list[str]]:
    if len(rows) > MAX_EXPECTED_ROWS:
        raise ServiceError(
            422, "EXPECTED_RESULTS_TOO_MANY_ROWS",
            f"Expected results row count ({len(rows)}) exceeds maximum limit of {MAX_EXPECTED_ROWS} rows.",
        )

    normalized = []
    columns: set[str] = set()
    seen_keys: set[tuple] = set()

    for row_idx, source in enumerate(rows):
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
            row[canonical_name] = _coerce_field_value(canonical_name, value)

        # Validate projection_month
        if "projection_month" in row and row["projection_month"] is not None:
            raw_month = row["projection_month"]
            if isinstance(raw_month, bool) or not isinstance(raw_month, int):
                raise ServiceError(422, "EXPECTED_RESULTS_PERIOD", "projection_month must be an integer.")
            if row["projection_month"] < 0:
                raise ServiceError(422, "EXPECTED_RESULTS_PERIOD", "projection_month cannot be negative.")
            row.setdefault("projection_year", max(1, math.ceil(row["projection_month"] / 12)))

        # Validate projection_year
        if "projection_year" in row and row["projection_year"] is not None:
            raw_year = row["projection_year"]
            if isinstance(raw_year, bool) or not isinstance(raw_year, int):
                raise ServiceError(422, "EXPECTED_RESULTS_PERIOD", "projection_year must be an integer.")
            if row["projection_year"] < 1:
                raise ServiceError(422, "EXPECTED_RESULTS_PERIOD", "projection_year must be at least 1.")

        # P1 Section 10: Reject duplicate benchmark keys
        if granularity == "policy_month":
            key = (str(row.get("policy_id") or ""), str(row.get("scenario_id") or ""), row.get("projection_month"))
            if key in seen_keys:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_DUPLICATE_KEY",
                    f"Duplicate expected result key: policy_id='{key[0]}', projection_month={key[2]}.",
                )
            seen_keys.add(key)
        elif granularity == "aggregate_year":
            year_key = (str(row.get("scenario_id") or ""), row.get("projection_year"), str(row.get("variable") or ""))
            if year_key in seen_keys:
                raise ServiceError(
                    422, "EXPECTED_RESULTS_DUPLICATE_KEY",
                    f"Duplicate expected result key: projection_year={year_key[1]}.",
                )
            seen_keys.add(year_key)

        normalized.append(dict(sorted(row.items())))
        columns.update(row)

    normalized.sort(key=lambda r: (
        str(r.get("policy_id") or ""),
        int(r.get("projection_month") or 0),
        int(r.get("projection_year") or 0),
    ))
    return normalized, sorted(columns)


def parse_csv(content: bytes, granularity: str = "policy_month") -> tuple[list[dict[str, Any]], list[str]]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ServiceError(
            422, "PAYLOAD_TOO_LARGE",
            f"Expected results upload ({len(content)} bytes) exceeds maximum limit of {MAX_UPLOAD_BYTES} bytes.",
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ServiceError(422, "EXPECTED_RESULTS_ENCODING", "CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ServiceError(422, "EXPECTED_RESULTS_EMPTY", "CSV requires a header row.")
    return normalize_rows(list(reader), granularity=granularity)


def expected_view(row: ExpectedResultSet, include_rows: bool = False) -> dict[str, Any]:
    result = {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "product_code": row.product_code,
        "version_label": row.version_label,
        "source_reference": row.source_reference,
        "timing_convention": row.timing_convention,
        "granularity": row.granularity,
        "row_count": row.row_count,
        "columns_detected": row.columns_detected,
        "fingerprint": row.fingerprint,
        "status": row.status,
        "approved_by": getattr(row, "approved_by", None),
        "approved_at": iso(getattr(row, "approved_at", None)),
        "superseded_by_id": getattr(row, "superseded_by_id", None),
        "created_by": row.created_by,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }
    if include_rows:
        result["rows"] = row.rows_data
    return result


def create_expected_set(db: Session, project_id: str, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
    source_rows = payload.get("rows")
    if not isinstance(source_rows, list) or any(not isinstance(row, dict) for row in source_rows):
        raise ServiceError(422, "EXPECTED_RESULTS_ROWS", "'rows' must be a JSON array of row objects.")
    granularity = payload.get("granularity") or "policy_month"
    if granularity not in {"policy_month", "aggregate_year"}:
        raise ServiceError(422, "EXPECTED_RESULTS_GRANULARITY", "Unsupported expected-result granularity.")

    rows, columns = normalize_rows(source_rows, granularity=granularity)
    if not rows:
        raise ServiceError(422, "EXPECTED_RESULTS_EMPTY", "Expected results require at least one row.")

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
        project_id=project_id,
        name=payload["name"],
        product_code=payload["product_code"],
        version_label=payload.get("version_label") or "v1",
        source_reference=payload.get("source_reference") or "json-upload",
        timing_convention=payload.get("timing_convention"),
        granularity=granularity,
        row_count=len(rows),
        columns_detected=columns,
        rows_data=rows,
        fingerprint=fingerprint(rows),
        status="validated",
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    record_audit(
        db, actor_user_id=actor_id, action="expected_result_set.created",
        entity_type="expected_result_set", entity_id=row.id,
        after_state=expected_view(row),
    )
    db.commit()
    db.refresh(row)
    return expected_view(row)


def report_view(row: ReconciliationReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "run_id": row.run_id,
        "expected_result_set_id": row.expected_result_set_id,
        "level": getattr(row, "level", "policy_month"),
        "variables_requested": getattr(row, "variables_requested", []),
        "expected_result_fingerprint": getattr(row, "expected_result_fingerprint", None),
        "expected_result_version": getattr(row, "expected_result_version", None),
        "timing_convention_snapshot": getattr(row, "timing_convention_snapshot", None),
        "granularity_snapshot": getattr(row, "granularity_snapshot", None),
        "policy_id_filter": row.policy_id_filter,
        "abs_tol": row.abs_tol,
        "rel_tol": row.rel_tol,
        "status": row.status,
        "disposition_status": row.disposition_status,
        "disposition_notes": row.disposition_notes,
        "disposition_by": row.disposition_by,
        "disposition_at": iso(row.disposition_at),
        "disposition_history": getattr(row, "disposition_history", []) or [],
        "summary": row.summary,
        "failures": row.failures,
        "created_by": row.created_by,
        "created_at": iso(row.created_at),
    }


def _resolve_aggregations(
    db: Session, run: Run, configuration: dict[str, Any], variables: set[str]
) -> dict[str, str]:
    """Resolve Actuarial aggregation semantics (sum, last/end_of_period, first/beginning_of_period)."""
    published_meta = (configuration.get("outputs") or {}).get("published") or {}
    aggregations: dict[str, str] = {}

    for var in variables:
        agg = (published_meta.get(var) or {}).get("aggregation")
        if not agg:
            # Check ModelPublishedOutput in DB
            db_out = db.query(ModelPublishedOutput).filter(
                ModelPublishedOutput.model_version_id == run.model_version_id,
                ModelPublishedOutput.variable_name == var,
            ).first()
            if db_out and db_out.aggregation:
                agg = db_out.aggregation

        if not agg:
            raise ServiceError(
                422, "UNSUPPORTED_AGGREGATION",
                f"Aggregation metadata is absent for variable '{var}'. Aggregate-year reconciliation requires explicit aggregation semantic.",
            )

        clean_agg = str(agg).strip().lower()
        if clean_agg not in {"sum", "last", "end_of_period", "first", "beginning_of_period", "eom", "bom"}:
            raise ServiceError(
                422, "UNSUPPORTED_AGGREGATION",
                f"Variable '{var}' specifies unsupported aggregation semantic '{agg}'.",
            )
        aggregations[var] = clean_agg

    return aggregations


def _cell_maps(
    db: Session,
    run: Run,
    configuration: dict[str, Any],
    expected: ExpectedResultSet,
    actual_rows: list[dict[str, Any]],
    policy_id: str | None,
    variables: list[str] | None,
    level: str,
) -> tuple[dict[tuple, float], dict[tuple, float]]:
    allowed = set(variables or [])
    expected_cells: dict[tuple, float] = {}

    # Extract expected cells
    if level == "aggregate_year" and expected.granularity == "policy_month":
        needed_vars = {
            var for row in expected.rows_data for var in row
            if var not in IDENTITY_COLUMNS and not isinstance(row[var], bool) and isinstance(row[var], (int, float))
            and (not allowed or var in allowed)
        }
        exp_aggs = _resolve_aggregations(db, run, configuration, needed_vars)
        exp_grouped: dict[tuple[str, int, str], list[tuple[int, float]]] = defaultdict(list)
        for row in expected.rows_data:
            row_policy = str(row.get("policy_id") or "").strip()
            if policy_id and row_policy and row_policy != policy_id:
                continue
            month = int(row.get("projection_month") or 0)
            if month <= 0:
                continue
            year = int(row.get("projection_year") or math.ceil(month / 12))
            for var in needed_vars:
                if var in row and not isinstance(row[var], bool) and isinstance(row[var], (int, float)):
                    exp_grouped[(row_policy, year, var)].append((month, float(row[var])))

        exp_policy_year: dict[tuple[int, str], list[float]] = defaultdict(list)
        for (pol_id, year, var), month_values in exp_grouped.items():
            month_values.sort(key=lambda item: item[0])
            agg = exp_aggs[var]
            if agg == "sum":
                val = sum(v for _, v in month_values)
            elif agg in ("last", "end_of_period", "eom"):
                val = month_values[-1][1]
            elif agg in ("first", "beginning_of_period", "bom"):
                val = month_values[0][1]
            else:
                val = month_values[-1][1]
            exp_policy_year[(year, var)].append(val)

        for (year, var), values in exp_policy_year.items():
            expected_cells[(year, var)] = sum(values)
    else:
        for row in expected.rows_data:
            row_policy = str(row.get("policy_id") or "").strip()
            if level == "policy_month" and policy_id and row_policy != policy_id:
                continue
            if level == "aggregate_year" and policy_id and row_policy and row_policy != policy_id:
                continue
            period = int(row.get("projection_month") or 0)
            year = int(row.get("projection_year") or math.ceil(period / 12) if period > 0 else 1)
            for variable, value in row.items():
                if variable in IDENTITY_COLUMNS or isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                if allowed and variable not in allowed:
                    continue
                if level == "policy_month":
                    key = (str(row.get("policy_id") or ""), period, variable)
                    expected_cells[key] = float(value)
                else:
                    key = (year, variable)
                    expected_cells[key] = float(value)

    # Extract actual cells
    actual_cells: dict[tuple, float] = {}
    if level == "policy_month":
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
            key = (row["policy_id"], month, variable)
            actual_cells[key] = float(value)
    else:
        # P0 Section 3: Aggregate-year reconciliation must use variable aggregation semantics
        needed_vars = {key[1] for key in expected_cells}
        aggregations = _resolve_aggregations(db, run, configuration, needed_vars)

        # Group actual rows by (policy_id, year, variable)
        grouped_by_policy_year: dict[tuple[str, int, str], list[tuple[int, float]]] = defaultdict(list)
        for row in actual_rows:
            if policy_id and row["policy_id"] != policy_id:
                continue
            variable = row["variable_name"]
            if variable not in needed_vars:
                continue
            value = unwrap_value(row["value"])
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            month = int(row["projection_month"])
            year = max(1, math.ceil(month / 12))
            grouped_by_policy_year[(row["policy_id"], year, variable)].append((month, float(value)))

        # Aggregate each policy's yearly value
        policy_year_aggregates: dict[tuple[int, str], list[float]] = defaultdict(list)
        for (pol_id, year, var), month_values in grouped_by_policy_year.items():
            month_values.sort(key=lambda item: item[0])
            agg = aggregations[var]
            if agg == "sum":
                agg_val = sum(val for _, val in month_values)
            elif agg in ("last", "end_of_period", "eom"):
                agg_val = month_values[-1][1]
            elif agg in ("first", "beginning_of_period", "bom"):
                agg_val = month_values[0][1]
            else:
                raise ServiceError(422, "UNSUPPORTED_AGGREGATION", f"Unsupported aggregation '{agg}'.")
            policy_year_aggregates[(year, var)].append(agg_val)

        # Portfolio aggregate across policies
        for (year, var), values in policy_year_aggregates.items():
            actual_cells[(year, var)] = sum(values)

    return expected_cells, actual_cells


def normalize_product(p: str | None) -> str:
    if not p:
        return ""
    clean = p.strip().upper()
    if clean in ("SPIA", "SPIA_LITE"):
        return "SPIA"
    if clean in ("MYGA", "MYGA_LITE"):
        return "MYGA"
    return clean


def reconcile(db: Session, run_id: str, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
    run = results_service.result_run(db, run_id)

    # P1 Section 24: Cross-project IDs must return 404, not 400
    expected = db.get(ExpectedResultSet, payload["expected_result_set_id"])
    if expected is None or expected.project_id != run.project_id:
        raise not_found("Expected result set not found.")

    configuration = results_service.run_configuration(db, run)

    # P0 Section 4: Validate Expected Result Set Against the Run
    run_product = (configuration.get("model") or {}).get("product_code") or ""
    if expected.product_code and run_product and normalize_product(expected.product_code) != normalize_product(run_product):
        raise ServiceError(
            422, "EXPECTED_RESULTS_PRODUCT_MISMATCH",
            f"Expected results product '{expected.product_code}' does not match run model product '{run_product}'.",
        )

    expected_timing = (expected.timing_convention or "").strip().upper()
    run_timing = (
        (configuration.get("model_version") or {}).get("timing_convention")
        or configuration.get("timing_convention")
        or "EOM"
    ).strip().upper()
    if expected_timing and run_timing and expected_timing != run_timing:
        raise ServiceError(
            422, "EXPECTED_RESULTS_TIMING_MISMATCH",
            f"Expected timing convention '{expected.timing_convention}' does not match run timing convention '{run_timing}'.",
        )

    level = payload.get("level") or "policy_month"
    if level not in {"policy_month", "aggregate_year", "both"}:
        raise ServiceError(422, "RECONCILIATION_LEVEL", "level must be policy_month, aggregate_year, or both.")

    if expected.granularity == "aggregate_year" and level in ("policy_month", "both"):
        raise ServiceError(
            422, "EXPECTED_RESULTS_GRANULARITY_MISMATCH",
            "Cannot reconcile at policy_month level using aggregate_year expected results.",
        )
    if expected.granularity == "policy_month" and level == "aggregate_year":
        raise ServiceError(
            422, "EXPECTED_RESULTS_GRANULARITY_MISMATCH",
            "policy_month expected set cannot be used as aggregate_year without explicit supported transformation.",
        )

    # P1 Section 21: Max variables limit
    requested_vars = payload.get("variables")
    if requested_vars and len(requested_vars) > MAX_VARIABLES_PER_RECONCILIATION:
        raise ServiceError(
            422, "TOO_MANY_VARIABLES",
            f"Requested variable count ({len(requested_vars)}) exceeds maximum limit of {MAX_VARIABLES_PER_RECONCILIATION}.",
        )

    abs_tol, rel_tol = float(payload.get("abs_tol", 0.01)), float(payload.get("rel_tol", 0.0001))
    if not math.isfinite(abs_tol) or not math.isfinite(rel_tol) or abs_tol < 0 or rel_tol < 0:
        raise ServiceError(422, "RECONCILIATION_TOLERANCE", "Tolerances must be finite and non-negative.")

    actual_rows = results_service._accepted_rows(db, run)
    levels = ["policy_month", "aggregate_year"] if level == "both" else [level]

    failures, summaries = [], defaultdict(lambda: {
        "cells_checked": 0, "cells_passed": 0, "cells_failed": 0, "max_abs_diff": 0.0, "max_rel_diff": 0.0
    })
    checked = passed = 0
    max_abs = max_rel = 0.0
    total_failures_count = 0

    for comparison_level in levels:
        expected_cells, actual_cells = _cell_maps(
            db, run, configuration, expected, actual_rows,
            payload.get("policy_id"), requested_vars, comparison_level,
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
                total_failures_count += 1
                # P1 Section 21: Max failures retained in report detail (1,000)
                if len(failures) < MAX_REPORT_FAILURES:
                    failure = {
                        "level": comparison_level,
                        "variable": variable,
                        "expected": expected_value,
                        "actual": actual_value,
                        "abs_diff": None if not math.isfinite(abs_diff) else abs_diff,
                        "rel_diff": None if not math.isfinite(rel_diff) else rel_diff,
                        "tolerance_used": tolerance,
                    }
                    if comparison_level == "policy_month":
                        failure.update({"policy_id": key[0], "projection_month": key[1]})
                    else:
                        failure["projection_year"] = key[0]
                    failures.append(failure)

    if checked == 0:
        raise ServiceError(
            422, "NO_COMPARABLE_EXPECTED_RESULTS",
            "No numeric expected-result cells matched the requested policy and variables.",
        )

    # P1 Section 12: Source-to-output check must not false-pass
    actual_policies = {
        row["policy_id"] for row in actual_rows
        if not payload.get("policy_id") or row["policy_id"] == payload["policy_id"]
    }
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
            source_accounts[item.policy_id] = _coerce_field_value(field, data[field])
            source_amount_fields.add(field)

    account_diffs = [
        abs(float(month_one_open[policy]) - float(value))
        for policy, value in source_accounts.items() if policy in month_one_open
    ]

    # Three-state check: PASS, FAIL, NOT_VERIFIABLE
    has_account_value_model = any(row["variable_name"] == "account_value_open" for row in actual_rows)

    if not source_records:
        source_check_status = "NOT_VERIFIABLE"
        count_passed = False
        account_passed = False
    else:
        count_passed = bool(source_policies and source_policies == actual_policies)
        if not count_passed:
            source_check_status = "FAIL"
            account_passed = False
        elif has_account_value_model:
            if not account_available:
                source_check_status = "NOT_VERIFIABLE"
                account_passed = False
            else:
                account_passed = (
                    len(account_diffs) == len(source_accounts)
                    and all(diff <= abs_tol for diff in account_diffs)
                )
                source_check_status = "PASS" if account_passed else "FAIL"
        else:
            account_passed = True
            source_check_status = "PASS"
    source_check = {
        "status": source_check_status,
        "passed": source_check_status == "PASS",
        "not_verifiable": source_check_status == "NOT_VERIFIABLE",
        "source_policy_count": len(source_policies),
        "actual_policy_count": len(actual_policies),
        "opening_account_value": {
            "available": source_check_status != "NOT_VERIFIABLE",
            "policies_checked": len(account_diffs),
            "max_abs_diff": max(account_diffs, default=0.0),
            "passed": account_passed,
            "source_fields": sorted(source_amount_fields),
        },
    }

    summary = {
        "total_variables_checked": len(summaries),
        "total_cells_checked": checked,
        "cells_passed": passed,
        "cells_failed": checked - passed,
        "max_abs_diff": max_abs,
        "max_rel_diff": max_rel,
        "failures_truncated": total_failures_count > len(failures),
        "total_failures_count": total_failures_count,
        "per_variable_summary": dict(sorted(summaries.items())),
        "source_to_output_check": source_check,
    }

    # P1 Section 8 & 9: Freeze full scope on report
    calc_status = "PASS" if (total_failures_count == 0 and source_check_status != "FAIL") else "FAIL"
    report = ReconciliationReport(
        project_id=run.project_id,
        run_id=run.id,
        expected_result_set_id=expected.id,
        level=level,
        variables_requested=requested_vars or [],
        expected_result_fingerprint=expected.fingerprint,
        expected_result_version=expected.version_label,
        timing_convention_snapshot=expected.timing_convention or "EOM",
        granularity_snapshot=expected.granularity,
        policy_id_filter=payload.get("policy_id"),
        abs_tol=abs_tol,
        rel_tol=rel_tol,
        status=calc_status,
        summary=summary,
        failures=failures,
        disposition_history=[],
        created_by=actor_id,
    )
    db.add(report)
    db.flush()
    record_audit(
        db, actor_user_id=actor_id, action="reconciliation.created",
        entity_type="reconciliation", entity_id=report.id,
        after_state={"run_id": run.id, "status": report.status, "summary": summary},
    )
    db.commit()
    db.refresh(report)
    return report_view(report)


def disposition(
    db: Session, report: ReconciliationReport, status: str, notes: str | None, actor_id: str
) -> dict[str, Any]:
    # P1 Section 20: Reviewer disposition workflow rules
    valid_statuses = {"approved", "approved_with_exception", "exception_noted", "rejected"}
    if status not in valid_statuses:
        raise ServiceError(422, "RECONCILIATION_DISPOSITION", f"Invalid disposition status '{status}'.")

    # FAIL report cannot be marked "approved"
    if report.status == "FAIL" and status == "approved":
        raise ServiceError(
            422, "RECONCILIATION_CANNOT_APPROVE_FAILED",
            "A reconciliation report with status 'FAIL' cannot be approved without exception. Use 'approved_with_exception'.",
        )

    clean_notes = (notes or "").strip()
    if status in ("approved_with_exception", "exception_noted") and (not clean_notes or len(clean_notes) < 3):
        raise ServiceError(
            422, "RECONCILIATION_EXCEPTION_REASON_REQUIRED",
            f"Disposition status '{status}' requires a non-empty explanation note.",
        )

    before = report_view(report)
    history_entry = {
        "from_status": report.disposition_status,
        "to_status": status,
        "notes": clean_notes or notes,
        "by": actor_id,
        "at": iso(now_utc()),
    }
    history = list(getattr(report, "disposition_history", []) or [])
    history.append(history_entry)

    report.disposition_status = status
    report.disposition_notes = clean_notes or notes
    report.disposition_by = actor_id
    report.disposition_at = now_utc()
    report.disposition_history = history

    db.flush()
    record_audit(
        db, actor_user_id=actor_id, action="reconciliation.disposition_recorded",
        entity_type="reconciliation", entity_id=report.id,
        before_state=before, after_state=report_view(report),
    )
    db.commit()
    db.refresh(report)
    return report_view(report)
