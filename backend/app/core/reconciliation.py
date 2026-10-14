"""Deterministic comparison of two immutable projection result datasets."""

from collections import defaultdict
from numbers import Real
from typing import Any


DIMENSIONS = ("policy_id", "scenario_id", "month", "variable")


def _key(row: dict[str, Any]) -> tuple[str, str, int, str]:
    return tuple(row[field] for field in DIMENSIONS)  # type: ignore[return-value]


def _index(rows: list[dict[str, Any]], label: str) -> dict[tuple, dict[str, Any]]:
    indexed: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = _key(row)
        if key in indexed:
            raise ValueError(f"Duplicate {label} result key: {key}")
        indexed[key] = row
    return indexed


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return float(value)


def reconcile_results(
    baseline_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline = _index(baseline_rows, "baseline")
    current = _index(current_rows, "current")
    details: list[dict[str, Any]] = []
    bridge_values: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "baseline_total": 0.0,
            "current_total": 0.0,
            "matched_points": 0,
            "added_points": 0,
            "removed_points": 0,
            "non_numeric_points": 0,
        }
    )

    for key in sorted(set(baseline) | set(current)):
        baseline_row = baseline.get(key)
        current_row = current.get(key)
        baseline_value = baseline_row["value"] if baseline_row else None
        current_value = current_row["value"] if current_row else None
        baseline_number = _number(baseline_value)
        current_number = _number(current_value)

        if baseline_row is None:
            change_type = "added"
        elif current_row is None:
            change_type = "removed"
        elif baseline_value == current_value:
            change_type = "unchanged"
        else:
            change_type = "changed"

        variance = (
            current_number - baseline_number
            if baseline_number is not None and current_number is not None
            else None
        )
        variance_pct = (
            variance / baseline_number * 100
            if variance is not None and baseline_number != 0
            else None
        )
        detail = {
            "policy_id": key[0],
            "scenario_id": key[1],
            "month": key[2],
            "variable": key[3],
            "baseline_value": baseline_value,
            "current_value": current_value,
            "variance": variance,
            "variance_pct": variance_pct,
            "change_type": change_type,
        }
        details.append(detail)

        bridge = bridge_values[key[3]]
        if baseline_number is not None:
            bridge["baseline_total"] += baseline_number
        if current_number is not None:
            bridge["current_total"] += current_number
        if baseline_number is None and current_number is None:
            bridge["non_numeric_points"] += 1
        elif baseline_row is None:
            bridge["added_points"] += 1
        elif current_row is None:
            bridge["removed_points"] += 1
        else:
            bridge["matched_points"] += 1

    bridges: list[dict[str, Any]] = []
    for variable, values in sorted(bridge_values.items()):
        variance = values["current_total"] - values["baseline_total"]
        baseline_total = values["baseline_total"]
        bridges.append(
            {
                "variable": variable,
                **values,
                "variance": variance,
                "variance_pct": (
                    variance / baseline_total * 100 if baseline_total != 0 else None
                ),
            }
        )

    return {
        "details": details,
        "bridges": bridges,
        "summary": {
            "compared_points": len(details),
            "changed_points": sum(
                detail["change_type"] == "changed" for detail in details
            ),
            "unchanged_points": sum(
                detail["change_type"] == "unchanged" for detail in details
            ),
            "added_points": sum(
                detail["change_type"] == "added" for detail in details
            ),
            "removed_points": sum(
                detail["change_type"] == "removed" for detail in details
            ),
        },
    }
