"""Tests for deterministic prior-versus-current result reconciliation."""

import pytest

from app.core.reconciliation import reconcile_results


def _row(month, value, variable="reserve"):
    return {
        "policy_id": "P1",
        "scenario_id": "base",
        "month": month,
        "variable": variable,
        "value": value,
    }


def test_reconciliation_builds_detail_and_variable_bridge():
    result = reconcile_results(
        [_row(1, 100.0), _row(2, 120.0), _row(3, 10.0, "fee")],
        [_row(1, 110.0), _row(2, 120.0), _row(4, 140.0)],
    )

    assert result["summary"] == {
        "compared_points": 4,
        "changed_points": 1,
        "unchanged_points": 1,
        "added_points": 1,
        "removed_points": 1,
    }
    reserve = next(item for item in result["bridges"] if item["variable"] == "reserve")
    assert reserve["baseline_total"] == 220.0
    assert reserve["current_total"] == 370.0
    assert reserve["variance"] == 150.0
    changed = next(item for item in result["details"] if item["month"] == 1)
    assert changed["variance"] == 10.0
    assert changed["variance_pct"] == 10.0


def test_reconciliation_rejects_duplicate_dimensions():
    duplicate = [_row(1, 100.0), _row(1, 101.0)]

    with pytest.raises(ValueError, match="Duplicate baseline result key"):
        reconcile_results(duplicate, [])
