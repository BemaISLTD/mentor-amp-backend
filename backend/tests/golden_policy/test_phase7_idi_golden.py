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


def _run_data(*, include_dlr=True):
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
            {"attained_age": 45, "gender": "F", "occupation_class": "CLASS_1", "rate": 0.0},
        ),
        idi.ACTIVE_LAPSE_TABLE: _table(
            idi.ACTIVE_LAPSE_TABLE, ("policy_year",), "rate", {"policy_year": 1, "rate": 0.0},
        ),
        idi.DISABLED_TERMINATION_TABLE: _table(
            idi.DISABLED_TERMINATION_TABLE, ("attained_age", "gender"), "rate",
            {"attained_age": 45, "gender": "F", "rate": 0.0},
        ),
        idi.DISABLED_MORTALITY_TABLE: _table(
            idi.DISABLED_MORTALITY_TABLE, ("attained_age", "gender"), "rate",
            {"attained_age": 45, "gender": "F", "rate": 0.0},
        ),
    }
    if include_dlr:
        tables[idi.NEW_CLAIM_DLR_TABLE] = _table(
            idi.NEW_CLAIM_DLR_TABLE, ("attained_age", "occupation_class"), "factor",
            {"attained_age": 45, "occupation_class": "CLASS_1", "factor": 0.0}, kind="factor",
        )
    policy = PolicyRecord(
        policy_id="IDI-D",
        dataset_id="idi-dataset",
        dataset_name="synthetic_idi_inforce.csv",
        data={
            "policy_id": "IDI-D", "product_type": "IDI", "issue_date": "2025-01-01",
            "issue_age": 45, "gender": "F", "premium": 1_200.0,
            "claim_status": "disabled", "occupation_class": "CLASS_1",
            "monthly_benefit": 5_000.0, "annual_premium": 1_200.0,
            "elimination_months": 3, "max_benefit_months": 12,
            "claim_months_elapsed": 0, "expense_per_policy_monthly": 0.0,
            "waive_premium_on_disability": 1, "discount_rate": 0.0,
        },
    )
    return RunData(
        variables=variables,
        formulas=formulas,
        tables=tables,
        policies=[policy],
        scenario=ScenarioSpec(id="base", name="Base"),
        valuation_date=date(2025, 1, 31),
        horizon_months=4,
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


@pytest.mark.skip(
    reason="Pending Actuarial Product Owner signed IDI oracle workbook per CAS Section 17"
)
def test_owner_approved_idi_golden():
    pass
