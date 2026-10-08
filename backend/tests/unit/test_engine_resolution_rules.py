"""Resolver and engine rules from the contract (§F.3, §F.6, §F.10).

Each test pins one rule that the previous prototype got wrong or did not have: zero is a valid
value, missing lookups fail loudly (no first-row fallback), scenario overrides, prior-period
values, context values, type checks, cycles, and trace capture.
"""

import pytest

from app.core.projection_engine.engine import EngineError, order_formulas, run_policy
from app.core.projection_engine.run_data import FormulaSpec, normalize_key
from tests.unit.engine_fixtures import FUNCTIONS, POLICY, make_run_data, mortality_table


def _values(result, name):
    return [value for month, var, value in sorted(result.outputs) if var == name]


def test_zero_mortality_is_a_valid_value_not_missing():
    # The old resolver treated qx = 0.0 as missing (GAP-VAR-005). Age 72 M is used at month 12.
    table = mortality_table(overrides={(age, "M"): 0.0 for age in range(40, 151)})
    data = make_run_data([POLICY], horizon=12, table=table)
    result = run_policy(data, data.policies[0], FUNCTIONS)
    assert result.error is None
    assert _values(result, "survival_probability") == [1.0] * 12
    assert _values(result, "expected_payment") == [1650.0] * 12


def test_missing_table_row_fails_loudly_instead_of_using_the_first_row():
    table = mortality_table()
    table.rows = [row for row in table.rows if not (row["age"] == 72 and row["gender"] == "M")]
    table.build_index()
    data = make_run_data([POLICY], horizon=24, table=table)
    result = run_policy(data, data.policies[0], FUNCTIONS)
    assert result.error is not None
    assert result.error.error_type == "lookup_failed"
    # 57 months in force at valuation; month 4 -> policy month 61 -> age 67 + 5 = 72
    assert result.error.month == 4  # first month at age 72
    assert result.outputs == []


@pytest.mark.parametrize(
    "operation, operand, expected_rate",
    [
        ("set", 0.03, 0.03),
        ("add", 0.01, 0.055),
        ("subtract", 0.01, 0.035),
        ("multiply", 2.0, 0.09),
        ("percent_change", -0.10, 0.0405),
    ],
)
def test_scenario_overrides_apply_to_the_discount_rate(operation, operand, expected_rate):
    overrides = ({"target_variable": "discount_rate_annual", "operation": operation, "value": operand},)
    data = make_run_data([POLICY], horizon=12, overrides=overrides, traced=frozenset({"SPIA-0001"}))
    result = run_policy(data, data.policies[0], FUNCTIONS)
    assert result.error is None
    rate_row = [
        row for row in result.trace_rows
        if row["variable_name"] == "discount_rate_annual" and row["projection_month"] == 1
    ][0]
    assert rate_row["output_value"]["value"] == pytest.approx(expected_rate)
    assert rate_row["input_values"]["scenario_override"]["operation"] == operation
    assert rate_row["input_values"]["scenario_override"]["base_value"] == pytest.approx(0.045)


def test_lower_discount_rate_gives_higher_reserve():
    base = make_run_data([POLICY], horizon=120)
    low = make_run_data(
        [POLICY], horizon=120,
        overrides=({"target_variable": "discount_rate_annual", "operation": "set", "value": 0.03},),
    )
    base_result = run_policy(base, base.policies[0], FUNCTIONS)
    low_result = run_policy(low, low.policies[0], FUNCTIONS)
    assert low_result.reserve_0 > base_result.reserve_0


def test_override_limited_to_a_period_only_changes_those_months():
    overrides = ({
        "target_variable": "discount_rate_annual", "operation": "set", "value": 0.03,
        "applies_from_period": 1, "applies_to_period": 6,
    },)
    data = make_run_data([POLICY], horizon=12, overrides=overrides, traced=frozenset({"SPIA-0001"}))
    result = run_policy(data, data.policies[0], FUNCTIONS)
    rates = {
        row["projection_month"]: row["output_value"]["value"]
        for row in result.trace_rows
        if row["variable_name"] == "discount_rate_annual"
    }
    assert rates[6] == pytest.approx(0.03)
    assert rates[7] == pytest.approx(0.045)
    assert any("changes over time" in warning for warning in result.warnings)


def test_prior_output_uses_initial_value_in_month_one():
    data = make_run_data([POLICY], horizon=2, traced=frozenset({"SPIA-0001"}))
    result = run_policy(data, data.policies[0], FUNCTIONS)
    prev = {
        row["projection_month"]: row for row in result.trace_rows
        if row["variable_name"] == "survival_prev"
    }
    assert prev[1]["output_value"]["value"] == 1.0
    assert prev[1]["input_values"]["initial_value"] is True
    month1_survival = _values(result, "survival_probability")[0]
    assert prev[2]["output_value"]["value"] == pytest.approx(month1_survival)
    assert prev[2]["input_values"]["of_month"] == 1


def test_missing_input_column_is_missing_value():
    policy = {key: value for key, value in POLICY.items() if key != "monthly_payment"}
    data = make_run_data([policy], horizon=3)
    result = run_policy(data, data.policies[0], FUNCTIONS)
    assert result.error.error_type == "missing_value"
    assert result.error.variable == "monthly_payment"


def test_text_in_a_number_column_is_invalid_data_type():
    policy = {**POLICY, "monthly_payment": "abc"}
    data = make_run_data([policy], horizon=3)
    result = run_policy(data, data.policies[0], FUNCTIONS)
    assert result.error.error_type == "invalid_data_type"


def test_missing_issue_date_means_zero_months_in_force():
    policy = {**POLICY, "issue_date": ""}
    data = make_run_data([policy], horizon=13, traced=frozenset({"SPIA-0001"}))
    result = run_policy(data, data.policies[0], FUNCTIONS)
    ages = {
        row["projection_month"]: row["output_value"]["value"]
        for row in result.trace_rows if row["variable_name"] == "attained_age"
    }
    assert ages[12] == 67 and ages[13] == 68


def test_cycles_are_rejected():
    formulas = [
        FormulaSpec(id="a", name="A", output_variable="a", function_ref="f", dependencies=("b",)),
        FormulaSpec(id="b", name="B", output_variable="b", function_ref="f", dependencies=("a",)),
    ]
    with pytest.raises(EngineError) as excinfo:
        order_formulas(formulas)
    assert excinfo.value.error_type == "circular_dependency"


def test_trace_is_captured_only_for_traced_policies_with_13_rows_per_month():
    other = {**POLICY, "policy_id": "SPIA-0002"}
    data = make_run_data([POLICY, other], horizon=6, traced=frozenset({"SPIA-0001"}))
    traced = run_policy(data, data.policies[0], FUNCTIONS)
    untraced = run_policy(data, data.policies[1], FUNCTIONS)
    assert untraced.trace_rows == []
    month_three = [row for row in traced.trace_rows if row["projection_month"] == 3]
    assert len(month_three) == 13  # 7 resolutions + 5 formulas + 1 valuation row
    formula_rows = [row for row in month_three if row["formula_id"]]
    assert {row["variable_name"] for row in formula_rows} == {
        "q_monthly", "survival_probability", "expected_payment", "discount_factor",
        "pv_expected_payment",
    }


@pytest.mark.parametrize(
    "raw, expected",
    [(72, 72), (72.0, 72), ("72", 72), (" 72.0 ", 72), ("m", "M"), (0.5, 0.5), (None, None)],
)
def test_normalize_key(raw, expected):
    assert normalize_key(raw) == expected
