"""Structure and pure-function tests for the illustrative MYGA definition."""

from dataclasses import replace
from datetime import date

import pytest

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.formula_engine.formulas import get_registration
from app.core.projection_engine.engine import order_formulas, run_policy
from app.core.projection_engine.run_data import (
    FormulaSpec, PolicyRecord, RunData, ScenarioSpec, TableSpec, VariableSpec,
)
from app.products.myga_lite import config as myga


def test_twenty_ordered_functions_have_governed_metadata():
    myga.register_functions()

    assert len(myga._FUNCTIONS) == 20
    assert [item[0] for item in myga._FUNCTIONS] == [
        item["output_variable"] for item in myga.FORMULAS[:20]
    ]
    for name, function, expression, explanation in myga._FUNCTIONS:
        registration = get_registration(myga.function_ref(name))
        assert registration is not None
        assert registration.func is function
        assert registration.expression_text == expression
        assert registration.explanation == explanation
        assert registration.implementation_fingerprint
        assert registration.illustrative is True

    for name, function in myga._SUPPORT_FUNCTIONS:
        registration = get_registration(myga.function_ref(name))
        assert registration.func is function
        assert registration.expression_text == myga._SUPPORT_EXPRESSIONS[name]
        assert registration.explanation
        assert registration.implementation_fingerprint
        assert registration.illustrative is True


def test_declarative_formula_graph_is_acyclic_and_topological():
    formulas = [
        FormulaSpec(
            id=f"myga-{index}", name=row["name"],
            output_variable=row["output_variable"], function_ref=row["function_ref"],
            dependencies=tuple(row["dependencies"]), illustrative=True,
        )
        for index, row in enumerate(myga.FORMULAS, start=1)
    ]

    ordered = order_formulas(formulas)

    positions = {formula.output_variable: index for index, formula in enumerate(ordered)}
    assert len(ordered) == len(myga.FORMULAS)
    for formula in ordered:
        for dependency in formula.dependencies:
            if dependency in positions:
                assert positions[dependency] < positions[formula.output_variable]


def test_guarantee_period_and_renewal_boundaries():
    assert myga.guarantee_period_index(1, 5) == 1
    assert myga.guarantee_period_index(5, 5) == 1
    assert myga.guarantee_period_index(6, 5) == 2
    assert myga.year_in_guarantee_period(6, 5, 2) == 1
    assert myga.month_in_policy_year(61) == 0
    assert myga.renewal_window(2, 1, 0) == 1
    assert myga.renewal_window(2, 1, 1) == 0


def test_mva_is_symmetric_and_capped_by_charge():
    assert myga.market_value_adjustment(0, 0.05, 0.10, 4.5, 90_000, 8_000) == -8_000
    assert myga.market_value_adjustment(0, 0.10, 0.05, 4.5, 90_000, 8_000) == 8_000
    assert myga.market_value_adjustment(1, 0.05, 0.10, 4.5, 90_000, 8_000) == 0


def test_withdrawal_cannot_exceed_opening_account_value():
    assert myga.account_value_before_credit(100, 100) == 0
    assert myga.withdrawal_payment(0.5, 100, 100) == 50
    with pytest.raises(ValueError, match="opening account value"):
        myga.account_value_before_credit(100, 200)
    with pytest.raises(ValueError, match="opening account value"):
        myga.withdrawal_payment(1, 200, 100)
    with pytest.raises(ValueError, match="opening account value"):
        myga.account_value_before_credit(100, -1)


def test_gmsv_rate_rejects_below_contractual_nonforfeiture_bound_but_allows_higher_rate():
    with pytest.raises(ValueError, match="at least 0.15%"):
        myga.mgsv_close(87_500, 0, 0, 0.001)
    assert myga.mgsv_close(87_500, 0, 0, 0.04) > 87_500


def test_model_is_explicitly_illustrative_and_reference_only():
    version = myga.MODEL_DEFINITION["model"]["version"]
    assert version["illustrative"] is True
    assert version["configuration"]["reference_only"] is True
    assert "No compliance claim" in version["configuration"]["regulatory_claim"]


def test_published_balances_and_flows_have_correct_aggregation_rules():
    aggregation = {
        row["variable_name"]: row["aggregation"] for row in myga.PUBLISHED_OUTPUTS
    }
    assert aggregation["reserve"] == "end_of_period"
    assert aggregation["account_value_close"] == "end_of_period"
    assert aggregation["liability_outgo"] == "sum"
    assert aggregation["net_cash_flow"] == "sum"


def test_declarative_model_executes_through_shared_projection_engine():
    myga.register_functions()
    variables = {
        row["name"]: VariableSpec(
            name=row["name"], kind=row["kind"], data_type=row.get("data_type", "number"),
            unit=row.get("unit"), source=dict(row.get("source") or {}),
            default_value=row.get("default_value"), required=row.get("required", True),
            allow_scenario_override=row.get("allow_scenario_override", False),
        )
        for row in myga.VARIABLES
    }
    formulas = order_formulas([
        FormulaSpec(
            id=f"myga-{index}", name=row["name"],
            output_variable=row["output_variable"], function_ref=row["function_ref"],
            dependencies=tuple(row["dependencies"]), illustrative=True,
        )
        for index, row in enumerate(myga.FORMULAS, start=1)
    ])
    mortality = TableSpec(
        id="mortality", name=myga.MORTALITY_TABLE_NAME,
        key_columns=("age", "gender"), value_column="qx",
        rows=[{"age": age, "gender": "M", "qx": 0.01} for age in range(60, 66)],
    )
    mortality.build_index()
    charges = TableSpec(
        id="charges", name=myga.SURRENDER_CHARGE_TABLE_NAME,
        key_columns=("schedule", "year_in_gp"), value_column="rate", kind="factor",
        rows=[
            {"schedule": schedule, "year_in_gp": year, "rate": rate}
            for schedule, rates in (
                ("initial", (0.09, 0.08, 0.07, 0.06, 0.05)),
                ("renewal", (0.05, 0.04, 0.03, 0.02, 0.01)),
            )
            for year, rate in enumerate(rates, start=1)
        ],
    )
    assert charges.build_index() == []
    age_caps = TableSpec(
        id="age-caps", name=myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME,
        key_columns=("attained_age",), value_column="cap", kind="factor",
        rows=[{"attained_age": age, "cap": 1.0} for age in range(60, 66)],
    )
    assert age_caps.build_index() == []
    data = RunData(
        variables=variables, formulas=formulas,
        tables={
            myga.MORTALITY_TABLE_NAME: mortality,
            myga.SURRENDER_CHARGE_TABLE_NAME: charges,
            myga.SURRENDER_CHARGE_AGE_CAP_TABLE_NAME: age_caps,
        },
        policies=[PolicyRecord(policy_id="MYGA-ANCHOR", data={
            "issue_date": "2026-12-31", "issue_age": 60, "gender": "M",
            "premium": 100_000, "account_value": 100_000, "mgsv_value": 87_500,
            "free_wd_base": 100_000, "guarantee_period_years": 5,
            "declared_rate": 0.0445, "gmir": 0.0025, "gmsv_rate": 0.028,
            "mva_ref_yield_at_issue": 0.05,
        })],
        scenario=ScenarioSpec(id="base", name="Base"),
        valuation_date=date(2026, 12, 31), horizon_months=12,
        output_variables=[
            "account_value_close", "inforce_close", "liability_outgo", "reserve",
            "premium_cash_flow", "acquisition_commission",
        ],
        traced_policy_ids=frozenset({"MYGA-ANCHOR"}),
    )

    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is None, result.error
    assert result.months_computed == 12
    assert result.reserve_0 is not None and result.reserve_0 > 0
    assert len([row for row in result.outputs if row[1] == "account_value_close"]) == 12
    trace_variables = {row["variable_name"] for row in result.trace_rows}
    assert {
        "account_value_close", "free_withdrawal_available", "surrender_excess",
        "surrender_charge", "market_value_adjustment", "surrender_value",
        "surrender_benefit",
    }.issubset(trace_variables)

    new_business = {(month, name): value for month, name, value in result.outputs}
    assert new_business[(1, "premium_cash_flow")] == 100_000
    assert new_business[(1, "acquisition_commission")] == 2_000

    existing = PolicyRecord(
        policy_id="MYGA-EXISTING",
        data={**data.policies[0].data, "issue_date": "2025-12-31", "issue_age": 59},
    )
    existing_result = run_policy(
        replace(data, policies=[existing], traced_policy_ids=frozenset()),
        existing,
        FORMULA_FUNCTIONS,
    )
    assert existing_result.error is None, existing_result.error
    existing_cash_flows = {
        (month, name): value for month, name, value in existing_result.outputs
    }
    assert all(
        value == 0.0 for (month, name), value in existing_cash_flows.items()
        if name in {"premium_cash_flow", "acquisition_commission"}
    )

    excessive = replace(data, scenario=ScenarioSpec(
        id="excessive", name="Excessive withdrawal", overrides=({
            "target_variable": "withdrawal", "operation": "set", "value": 200_000,
            "applies_from_period": 1, "applies_to_period": 1,
        },),
    ))
    rejected = run_policy(excessive, excessive.policies[0], FORMULA_FUNCTIONS)
    assert rejected.error is not None
    assert rejected.error.variable == "account_value_before_credit"


def test_death_benefit_when_surrender_benefit_exceeds_account_value():
    """Actuarial Decision Test: When positive MVA elevates Cash Surrender Value above Account Value,

    the death benefit pays max(AV, CSV) = CSV so beneficiaries are not disadvantaged relative to surrender.
    """
    av_close = 100_000.0
    # Falling interest rates produce positive MVA, making CSV = 105,000 > AV = 100,000
    surrender_benefit = 105_000.0
    benefit = myga.death_benefit(av_close, surrender_benefit)
    assert benefit == 105_000.0
    assert benefit > av_close

    # Normal environment where surrender charge/MVA depresses CSV below AV
    normal_csv = 95_000.0
    normal_benefit = myga.death_benefit(av_close, normal_csv)
    assert normal_benefit == 100_000.0
    assert normal_benefit == av_close
