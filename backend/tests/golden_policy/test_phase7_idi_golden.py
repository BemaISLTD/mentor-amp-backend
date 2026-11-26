from datetime import date

import pytest

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import order_formulas, run_policy
from app.core.projection_engine.run_data import (
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    TableSpec,
    VariableSpec,
)
from app.products.idi_lite import config as idi


def _table(name, keys, value_column, row, *, kind="assumption"):
    table = TableSpec(
        id=f"table-{name}",
        name=name,
        key_columns=tuple(keys),
        value_column=value_column,
        rows=[row],
        kind=kind,
    )
    assert table.build_index() == []
    return table


def _run_data(
    *,
    include_dlr: bool = True,
    horizon_months: int = 4,
    policy_overrides: dict | None = None,
    q_inc_annual: float = 0.0,
    q_term_annual: float = 0.0,
    q_mort_disabled_annual: float = 0.0,
    dlr_factor: float = 0.0,
):
    idi.register_functions()
    variables = {
        row["name"]: VariableSpec(
            name=row["name"],
            kind=row["kind"],
            data_type=row.get("data_type", "number"),
            unit=row.get("unit"),
            source=dict(row.get("source") or {}),
            default_value=row.get("default_value"),
            required=row.get("required", True),
            allow_scenario_override=row.get("allow_scenario_override", False),
        )
        for row in idi.VARIABLES
    }
    formulas = order_formulas([
        FormulaSpec(
            id=f"idi-{index}",
            name=row["name"],
            output_variable=row["output_variable"],
            function_ref=row["function_ref"],
            dependencies=tuple(row["dependencies"]),
            illustrative=True,
        )
        for index, row in enumerate(idi.FORMULAS, start=1)
    ])
    tables = {
        idi.ACTIVE_MORTALITY_TABLE: _table(
            idi.ACTIVE_MORTALITY_TABLE, ("attained_age", "gender"), "rate",
            {"attained_age": 45, "gender": "F", "rate": 0.0},
        ),
        idi.INCIDENCE_TABLE: _table(
            idi.INCIDENCE_TABLE, ("attained_age", "gender", "occupation_class"), "rate",
            {"attained_age": 45, "gender": "F", "occupation_class": "CLASS_1", "rate": q_inc_annual},
        ),
        idi.ACTIVE_LAPSE_TABLE: _table(
            idi.ACTIVE_LAPSE_TABLE, ("policy_year",), "rate", {"policy_year": 1, "rate": 0.0},
        ),
        idi.DISABLED_TERMINATION_TABLE: _table(
            idi.DISABLED_TERMINATION_TABLE, ("attained_age", "gender"), "rate",
            {"attained_age": 45, "gender": "F", "rate": q_term_annual},
        ),
        idi.DISABLED_MORTALITY_TABLE: _table(
            idi.DISABLED_MORTALITY_TABLE, ("attained_age", "gender"), "rate",
            {"attained_age": 45, "gender": "F", "rate": q_mort_disabled_annual},
        ),
    }
    if include_dlr:
        tables[idi.NEW_CLAIM_DLR_TABLE] = _table(
            idi.NEW_CLAIM_DLR_TABLE, ("attained_age", "occupation_class"), "factor",
            {"attained_age": 45, "occupation_class": "CLASS_1", "factor": dlr_factor}, kind="factor",
        )
    policy_data = {
        "policy_id": "IDI-D", "product_type": "IDI", "issue_date": "2025-01-01",
        "issue_age": 45, "gender": "F", "premium": 1_200.0,
        "claim_status": "disabled", "occupation_class": "CLASS_1",
        "monthly_benefit": 5_000.0, "annual_premium": 1_200.0,
        "elimination_months": 3, "max_benefit_months": 12,
        "claim_months_elapsed": 0, "expense_per_policy_monthly": 0.0,
        "waive_premium_on_disability": 1, "discount_rate": 0.0,
    }
    if policy_overrides:
        policy_data.update(policy_overrides)
    policy = PolicyRecord(
        policy_id=policy_data["policy_id"],
        dataset_id="idi-dataset",
        dataset_name="synthetic_idi_inforce.csv",
        data=policy_data,
    )
    return RunData(
        variables=variables,
        formulas=formulas,
        tables=tables,
        policies=[policy],
        scenario=ScenarioSpec(id="base", name="Base"),
        valuation_date=date(2025, 1, 31),
        horizon_months=horizon_months,
        output_variables=[row["variable_name"] for row in idi.PUBLISHED_OUTPUTS],
        traced_policy_ids=frozenset({policy.policy_id}),
    ), policy


def test_idi_disabled_elimination_anchor_executes_through_shared_engine():
    data, policy = _run_data()
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error
    output = {(month, name): value for month, name, value in result.outputs}
    assert [output[(month, "expected_claim_benefit_outgo")] for month in range(1, 5)] == [
        0.0, 0.0, 0.0, 5_000.0
    ]
    assert [output[(month, "expected_premium_inflow")] for month in range(1, 5)] == [
        0.0, 0.0, 0.0, 0.0
    ]
    assert output[(0, "reserve")] == pytest.approx(5_000.0, abs=1e-6)
    assert result.sums["pv_net_cash_flow"] == pytest.approx(5_000.0, abs=1e-6)
    trace_names = {row["variable_name"] for row in result.trace_rows}
    assert {"claim_status", "q_term_annual", "disabled_prob_close", "reserve"} <= trace_names


def test_idi_missing_exact_lookup_fails_closed():
    data, policy = _run_data(include_dlr=False)
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is not None
    assert result.error.error_type == "lookup_failed"
    assert idi.NEW_CLAIM_DLR_TABLE in result.error.message


def test_idi_shared_engine_incident_claim_cohort_duration_counterexample():
    """Shared-engine verification of the exact 4-month incident-claim cohort case.

    q_inc_m = 0.1 <=> q_inc_annual = 1 - 0.9**12.
    elimination_months = 0, max_benefit_months = 1, monthly_benefit = 1000, dlr = 0.
    Hand-calculated monthly benefits: [0.0, 100.0, 90.0, 81.0] (total reserve = 271.0).
    """
    q_inc_annual = 1.0 - (0.9**12)
    data, policy = _run_data(
        horizon_months=4,
        q_inc_annual=q_inc_annual,
        dlr_factor=0.0,
        policy_overrides={
            "policy_id": "IDI-COHORT",
            "claim_status": "active",
            "monthly_benefit": 1_000.0,
            "annual_premium": 0.0,
            "elimination_months": 0,
            "max_benefit_months": 1,
            "claim_months_elapsed": 0,
            "expense_per_policy_monthly": 0.0,
            "waive_premium_on_disability": 1,
            "discount_rate": 0.0,
        },
    )
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error
    output = {(month, name): value for month, name, value in result.outputs}
    benefits = [output[(m, "expected_claim_benefit_outgo")] for m in range(1, 5)]
    assert benefits == pytest.approx([0.0, 100.0, 90.0, 81.0], abs=1e-9)
    assert [output[(m, "expected_new_claim_incurred")] for m in range(1, 5)] == [0.0, 0.0, 0.0, 0.0]
    assert output[(0, "reserve")] == pytest.approx(271.0, abs=1e-9)
    assert result.sums["pv_net_cash_flow"] == pytest.approx(271.0, abs=1e-9)


def test_idi_shared_engine_dlr_incurred_mode_does_not_double_count_new_claims():
    """When new_claim_dlr_factor > 0, new claims are valued via DLR at incidence only."""
    q_inc_annual = 1.0 - (0.9**12)
    data, policy = _run_data(
        horizon_months=4,
        q_inc_annual=q_inc_annual,
        dlr_factor=3.0,
        policy_overrides={
            "policy_id": "IDI-DLR",
            "claim_status": "active",
            "monthly_benefit": 1_000.0,
            "annual_premium": 0.0,
            "elimination_months": 0,
            "max_benefit_months": 1,
            "claim_months_elapsed": 0,
            "expense_per_policy_monthly": 0.0,
            "waive_premium_on_disability": 1,
            "discount_rate": 0.0,
        },
    )
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error
    output = {(month, name): value for month, name, value in result.outputs}
    # Monthly benefits stay 0.0 because new claims are settled via DLR at incidence (no double counting).
    assert [output[(m, "expected_claim_benefit_outgo")] for m in range(1, 5)] == [0.0, 0.0, 0.0, 0.0]
    assert [output[(m, "disabled_prob_close")] for m in range(1, 5)] == [0.0, 0.0, 0.0, 0.0]
    expected_incurred = [0.1 * (0.9 ** (m - 1)) * 1_000.0 * 3.0 for m in range(1, 5)]
    assert [output[(m, "expected_new_claim_incurred")] for m in range(1, 5)] == pytest.approx(
        expected_incurred, abs=1e-9
    )
    assert output[(0, "reserve")] == pytest.approx(sum(expected_incurred), abs=1e-9)


@pytest.mark.skip(
    reason="Pending Actuarial Product Owner signed IDI oracle workbook per CAS Section 17"
)
def test_owner_approved_idi_golden():
    pass
