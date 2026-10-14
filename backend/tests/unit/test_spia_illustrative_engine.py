"""Engine self-consistency test for the SPIA illustrative model (contract §F.8).

This compares the engine with an INDEPENDENT calculation written differently (direct products
and double sums instead of the engine's recursion). It proves the engine computes what the
illustrative formulas say. It is NOT actuarial validation: that needs the business's reference
case (docs/build/03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_PRODUCT_OWNER.md, Q-SPIA-1..6).
"""

import math

import pytest

from app.core.projection_engine.engine import run_policy
from app.products.spia_lite import config as spia
from tests.unit.engine_fixtures import FUNCTIONS, POLICY, make_run_data


def independent_spia(policy: dict, horizon: int, rate: float) -> dict[str, list[float]]:
    """Closed-form SPIA illustrative values, computed without the engine."""
    months_in_force = (2026 - 2022) * 12 + (12 - 3)  # issue 2022-03, valuation 2026-12
    issue_age = int(policy["issue_age"])
    payment = float(policy["monthly_payment"])
    q_monthly = []
    for t in range(1, horizon + 1):
        age = issue_age + (months_in_force + t - 1) // 12
        qx = spia.gompertz_qx(age, "M")
        q_monthly.append(1 - (1 - qx) ** (1 / 12))
    survival = [math.prod(1 - q for q in q_monthly[:t]) for t in range(1, horizon + 1)]
    expected = [payment * s for s in survival]
    pv = [expected[t - 1] * (1 + rate) ** (-t / 12) for t in range(1, horizon + 1)]
    reserve = [
        math.fsum(expected[k - 1] * (1 + rate) ** (-(k - t) / 12) for k in range(t + 1, horizon + 1))
        for t in range(0, horizon + 1)
    ]
    return {"survival": survival, "expected": expected, "pv": pv, "reserve": reserve}


def _series(result, name):
    values = [(month, value) for month, var, value in result.outputs if var == name]
    return [value for _, value in sorted(values)]


@pytest.mark.parametrize("horizon", [24, 600])
def test_engine_matches_independent_calculation(horizon):
    data = make_run_data([POLICY], horizon=horizon)
    result = run_policy(data, data.policies[0], FUNCTIONS)

    assert result.error is None, result.error and result.error.as_dict()
    assert result.warnings == []
    expected = independent_spia(POLICY, horizon, spia.DEFAULT_DISCOUNT_RATE)

    for name, key in (
        ("survival_probability", "survival"),
        ("expected_payment", "expected"),
        ("pv_expected_payment", "pv"),
        ("reserve", "reserve"),
    ):
        engine_values = _series(result, name)
        assert len(engine_values) == len(expected[key]), name
        for got, want in zip(engine_values, expected[key]):
            assert got == pytest.approx(want, rel=1e-9, abs=1e-9), name

    # reserve at valuation = sum of PV of expected payments (contract §F.8)
    assert result.reserve_0 == pytest.approx(math.fsum(expected["pv"]), rel=1e-9)


def test_run_is_deterministic():
    data = make_run_data([POLICY], horizon=120)
    first = run_policy(data, data.policies[0], FUNCTIONS)
    second = run_policy(data, data.policies[0], FUNCTIONS)
    assert first.outputs == second.outputs


def test_attained_age_uses_issue_date_and_issue_age():
    # issue 2022-03 -> 57 months in force at 2026-12-31; month 12 -> policy month 69 -> age 67 + 5
    data = make_run_data([POLICY], horizon=12, traced=frozenset({"SPIA-0001"}))
    result = run_policy(data, data.policies[0], FUNCTIONS)
    age_rows = [
        row for row in result.trace_rows
        if row["variable_name"] == "attained_age" and row["projection_month"] == 12
    ]
    assert age_rows[0]["output_value"] == {"value": 72}
    lookup = [
        row for row in result.trace_rows
        if row["variable_name"] == "mortality_rate_annual" and row["projection_month"] == 12
    ][0]
    assert lookup["lookup_keys"] == {"age": 72, "gender": "M"}
    assert lookup["source_table"] == spia.MORTALITY_TABLE_NAME
