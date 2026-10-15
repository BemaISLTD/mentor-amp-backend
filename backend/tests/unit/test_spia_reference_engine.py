"""Reconciliation tests for the native SPIA reference-model anchor."""

import json
import math
from datetime import date
from pathlib import Path

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
from app.products.spia_reference import config as spia

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "spia_reference_lifelib_v0_17_2.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _variables() -> dict[str, VariableSpec]:
    return {
        item["name"]: VariableSpec(
            name=item["name"],
            kind=item["kind"],
            data_type=item["data_type"],
            unit=item.get("unit"),
            source=dict(item["source"]),
            default_value=item.get("default_value"),
            required=item.get("required", True),
            display_name=item.get("display_name"),
            allow_scenario_override=item.get("allow_scenario_override", False),
        )
        for item in spia.VARIABLES
    }


def _formulas() -> list[FormulaSpec]:
    return order_formulas(
        [
            FormulaSpec(
                id=f"formula-{item['output_variable']}",
                name=item["name"],
                output_variable=item["output_variable"],
                function_ref=item["function_ref"],
                dependencies=tuple(item["dependencies"]),
                illustrative=True,
            )
            for item in spia.FORMULAS
        ]
    )


def _table(name: str, value_column: str, fixture: dict) -> TableSpec:
    rows = [
        {
            "age": row["age"],
            "gender": row["gender"],
            value_column: row[value_column],
        }
        for row in fixture["assumptions"]
    ]
    table = TableSpec(
        id=f"table-{name.lower()}",
        name=name,
        key_columns=("age", "gender"),
        value_column=value_column,
        rows=rows,
    )
    assert table.build_index() == []
    return table


def _surrender_charge_table(fixture: dict) -> TableSpec:
    table = TableSpec(
        id="table-spia-reference-surrender-charge",
        name=spia.SURRENDER_CHARGE_TABLE_NAME,
        key_columns=("policy_year",),
        value_column="surrender_charge_rate",
        rows=list(fixture["surrender_charge_schedule"]),
        kind="factor",
    )
    assert table.build_index() == []
    return table


def _run_data(
    payment_frequency: int = 12,
    payment_timing: str = "arrears",
    cola_rate: float = 0.0,
    is_joint: int = 0,
    joint_issue_age: int = 65,
    joint_gender: str = "M",
    survivor_percentage: float = 1.0,
    reduction_trigger: str = "either",
    benefit_form: str = "life_only",
    premium: float = 0.0,
    certain_months: int = 0,
    commutation_enabled: int = 0,
    issue_state_excludes_withdrawal: int | None = 0,
    commutation_utilization_rate: float = 0.0,
    cumulative_scheduled_paid_at_valuation: float = 0.0,
    commutation_fraction_at_valuation: float = 0.0,
    issue_date: str = "2026-12-31",
    issue_age: int | None = None,
    annuity_year: int | None = None,
) -> RunData:
    fixture = _fixture()
    spia.register_functions()
    mortality = _table(
        spia.MORTALITY_TABLE_NAME, "base_mortality_rate", fixture
    )
    improvement = _table(
        spia.IMPROVEMENT_TABLE_NAME, "mortality_improvement_rate", fixture
    )
    surrender_charge = _surrender_charge_table(fixture)
    policy_input = fixture["input"]
    policy = {
        "policy_id": "IA-MENTORAMP-ANCHOR",
        "product_type": "SPIA",
        "issue_date": issue_date,
        "issue_age": policy_input["primary_age"] if issue_age is None else issue_age,
        "gender": policy_input["primary_sex"],
        "annual_income": policy_input["annual_income"],
        "benefit_form": benefit_form,
        "premium": premium,
        "cumulative_scheduled_paid_at_valuation": cumulative_scheduled_paid_at_valuation,
        "commutation_fraction_at_valuation": commutation_fraction_at_valuation,
        "certain_months": certain_months,
        "commutation_enabled": commutation_enabled,
        "annuity_year": policy_input["annuity_year"] if annuity_year is None else annuity_year,
        "payment_frequency": payment_frequency,
        "payment_timing": payment_timing,
        "cola_rate": cola_rate,
        "rating_factor": policy_input["rating_factor"],
        "is_joint": is_joint,
        "joint_issue_age": joint_issue_age,
        "joint_gender": joint_gender,
        "joint_rating_factor": 1.0,
        "survivor_percentage": survivor_percentage,
        "reduction_trigger": reduction_trigger,
    }
    if issue_state_excludes_withdrawal is not None:
        policy["issue_state_excludes_withdrawal"] = issue_state_excludes_withdrawal
    return RunData(
        variables=_variables(),
        formulas=_formulas(),
        tables={
            mortality.name: mortality,
            improvement.name: improvement,
            surrender_charge.name: surrender_charge,
        },
        policies=[
            PolicyRecord(
                policy_id=policy["policy_id"],
                data=policy,
                dataset_id="fixture-inforce",
                dataset_name=FIXTURE_PATH.name,
            )
        ],
        scenario=ScenarioSpec(
            id="base",
            name="Base",
            overrides=(
                {
                    "target_variable": "commutation_utilization_rate",
                    "operation": "set",
                    "value": commutation_utilization_rate,
                },
            ),
        ),
        valuation_date=date(2026, 12, 31),
        horizon_months=len(fixture["rows"]),
        output_variables=[
            "annual_income_current",
            "survival_probability",
            "joint_survival_probability",
            "last_survivor_probability",
            "payment_factor_life",
            "certain_floor",
            "payment_factor",
            "annuity_payment",
            "cumulative_scheduled_before_current",
            "commutation_fraction",
            "commutation_eligible",
            "commuted_value",
            "commutation_amount",
            "commutation_payment",
            "surrender_charge",
            "cumulative_scheduled_payment",
            "last_death_probability",
            "refund_claim",
            "obligation_factor",
            "maintenance_expense",
            "liability_cash_flow",
            "net_cash_flow",
            "cash_flow_identity_residual",
            "pv_annuity_payment",
            "pv_commutation_payment",
            "pv_maintenance_expense",
            "pv_liability_cash_flow",
            "reserve",
        ],
        traced_policy_ids=frozenset({policy["policy_id"]}),
    )


def _series(result, variable_name: str) -> list[float]:
    values = [
        (month, value)
        for month, name, value in result.outputs
        if name == variable_name
    ]
    return [value for _, value in sorted(values)]


def test_native_engine_reconciles_to_pinned_lifelib_anchor():
    fixture = _fixture()
    data = _run_data()
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is None, result.error and result.error.as_dict()
    assert result.warnings == []
    tolerance = fixture["tolerance"]
    for variable_name in fixture["comparison_scope"]:
        actual = _series(result, variable_name)
        expected = [row[variable_name] for row in fixture["rows"]]
        assert len(actual) == len(expected)
        for got, want in zip(actual, expected):
            assert got == pytest.approx(
                want,
                rel=tolerance["relative"],
                abs=tolerance["absolute"],
            )


def test_mentoramp_valuation_layer_is_separate_and_self_consistent():
    fixture = _fixture()
    assert fixture["excluded_from_comparison"] == [
        "discount_factor",
        "pv_annuity_payment",
        "pv_refund_claim",
        "pv_maintenance_expense",
        "pv_liability_cash_flow",
        "reserve",
    ]

    data = _run_data()
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()

    payment = _series(result, "annuity_payment")
    present_value = _series(result, "pv_annuity_payment")
    expected_pv = [
        value * (1.0 + spia.DEFAULT_DISCOUNT_RATE) ** (-month / 12.0)
        for month, value in enumerate(payment, start=1)
    ]
    assert present_value == pytest.approx(expected_pv, rel=1e-12, abs=1e-12)
    assert result.reserve_0 == pytest.approx(
        math.fsum(_series(result, "pv_liability_cash_flow")), rel=1e-12
    )


def test_payment_schedule_supports_quarterly_arrears_and_advance():
    arrears_data = _run_data(payment_frequency=4, payment_timing="arrears")
    advance_data = _run_data(payment_frequency=4, payment_timing="advance")
    arrears = run_policy(
        arrears_data, arrears_data.policies[0], FORMULA_FUNCTIONS
    )
    advance = run_policy(
        advance_data, advance_data.policies[0], FORMULA_FUNCTIONS
    )

    assert arrears.error is None
    assert advance.error is None
    assert [
        month for month, value in enumerate(_series(arrears, "annuity_payment"), 1) if value
    ] == [3, 6, 9, 12, 15, 18, 21, 24]
    assert [
        month for month, value in enumerate(_series(advance, "annuity_payment"), 1) if value
    ] == [1, 4, 7, 10, 13, 16, 19, 22]


def test_advance_payments_use_start_of_month_valuation_timing():
    data = _run_data(payment_timing="advance")
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is None, result.error and result.error.as_dict()
    payments = _series(result, "annuity_payment")
    present_values = _series(result, "pv_annuity_payment")
    expected = [
        payment * (1.0 + spia.DEFAULT_DISCOUNT_RATE) ** (-(month - 1) / 12.0)
        for month, payment in enumerate(payments, start=1)
    ]
    assert present_values == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert present_values[0] == pytest.approx(payments[0])
    assert result.reserve_0 == pytest.approx(
        math.fsum(_series(result, "pv_liability_cash_flow")), rel=1e-12
    )


def test_advance_timing_and_cola_match_shipped_lifelib_case():
    case = _fixture()["shipped_advance_cola_case"]
    inputs = case["input"]
    for row in case["rows"]:
        current_income = spia.annual_income_current(
            inputs["annual_income"], inputs["cola_rate"], row["month"]
        )
        factor = spia.payment_factor_life(
            "life_only",
            0,
            1.0,
            "either",
            inputs["timing"],
            row["survival_start"],
            row["survival_end"],
            1.0,
            1.0,
        )
        installment = spia.scheduled_installment_untrimmed(
            current_income,
            inputs["frequency"],
            inputs["timing"],
            row["month"],
        )
        payment = spia.annuity_payment(installment, factor, 0.0, 0.0)
        assert current_income == pytest.approx(row["annual_income_current"])
        assert payment == pytest.approx(row["annuity_payment"])


def test_native_engine_reconciles_to_pinned_joint_table_anchor():
    fixture = _fixture()
    case = fixture["shipped_joint_table_case"]
    inputs = case["input"]
    data = _run_data(
        payment_frequency=inputs["frequency"],
        payment_timing=inputs["timing"],
        cola_rate=inputs["cola_rate"],
        is_joint=1,
        joint_issue_age=inputs["joint_age"],
        joint_gender=inputs["joint_sex"],
        survivor_percentage=inputs["survivor_percentage"],
        reduction_trigger=inputs["reduction_trigger"],
    )
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is None, result.error and result.error.as_dict()
    tolerance = fixture["tolerance"]
    for variable_name in (
        "survival_probability",
        "joint_survival_probability",
        "last_survivor_probability",
        "payment_factor_life",
        "annuity_payment",
    ):
        actual = _series(result, variable_name)
        expected = [row[variable_name] for row in case["rows"]]
        assert actual == pytest.approx(
            expected,
            rel=tolerance["relative"],
            abs=tolerance["absolute"],
        )


def test_survivor_reduction_triggers_match_pinned_scenario_cases():
    for case in _fixture()["shipped_joint_trigger_cases"]:
        factor = spia.payment_factor_life(
            "life_only",
            1,
            0.6666666667,
            case["reduction_trigger"],
            "arrears",
            1.0,
            case["primary_survival"],
            1.0,
            case["joint_survival"],
        )
        assert factor == pytest.approx(case["payment_factor_life"]), case


@pytest.mark.parametrize("trigger", ["either", "primary"])
def test_joint_payment_stops_after_last_death(trigger):
    factor = spia.payment_factor_life(
        "life_only", 1, 0.6666666667, trigger, "arrears", 1.0, 0.0, 1.0, 0.0
    )
    assert factor == 0.0


@pytest.mark.parametrize(
    "case_name", ["life_certain_survivor", "life_certain_both_dead"]
)
def test_life_certain_floor_matches_shipped_lifelib_boundaries(case_name):
    case = _fixture()["shipped_benefit_form_cases"][case_name]
    for row in case["rows"]:
        floor = spia.certain_floor(
            "life_certain",
            case["certain_months"],
            0.0,
            0.0,
            row["month"],
        )
        assert floor == row["certain_floor"]
        assert spia.payment_factor(floor, row["life_factor"]) == pytest.approx(
            row["payment_factor"]
        )


def test_life_certain_engine_uses_guarantee_as_floor_not_additional_payment():
    data = _run_data(benefit_form="life_certain", certain_months=12)
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is None, result.error and result.error.as_dict()
    payments = _series(result, "annuity_payment")
    life_factors = _series(result, "payment_factor_life")
    master_factors = _series(result, "payment_factor")
    assert payments[:12] == pytest.approx([500.0] * 12)
    assert master_factors[:12] == pytest.approx([1.0] * 12)
    assert master_factors[12] == pytest.approx(life_factors[12])
    assert payments[12] < 500.0


@pytest.mark.parametrize(
    "case_name,timing",
    [("cash_refund_arrears", "arrears"), ("cash_refund_advance", "advance")],
)
def test_cash_refund_death_month_order_matches_shipped_lifelib(case_name, timing):
    case = _fixture()["shipped_benefit_form_cases"][case_name]
    claim = spia.refund_claim(
        "cash_refund",
        case["premium"],
        timing,
        case["cumulative_scheduled_prev"],
        case["cumulative_scheduled_payment"],
        case["last_death_probability"],
    )
    assert claim == pytest.approx(case["refund_claim"])


def test_installment_refund_trims_final_guaranteed_installment_then_resumes():
    premium = 100250.0
    final_guaranteed = spia.scheduled_installment(
        "installment_refund", premium, 500.0, 100000.0
    )
    assert final_guaranteed == 250.0
    assert spia.certain_floor(
        "installment_refund", 0, premium, 100000.0, 201
    ) == 1.0

    after_recovery = spia.scheduled_installment(
        "installment_refund", premium, 500.0, 100250.0
    )
    assert after_recovery == 500.0
    assert spia.certain_floor(
        "installment_refund", 0, premium, 100250.0, 202
    ) == 0.0


def test_installment_refund_floor_matches_shipped_lifelib_boundary():
    case = _fixture()["shipped_benefit_form_cases"]["installment_refund"]
    for row in case["rows"]:
        assert spia.certain_floor(
            "installment_refund",
            0,
            case["premium"],
            row["cumulative_scheduled_prev"],
            row["month"],
        ) == row["certain_floor"]


def test_certain_only_matches_shipped_lifelib_boundary_regardless_of_survival():
    case = _fixture()["shipped_benefit_form_cases"]["certain_only"]
    for row in case["rows"]:
        life_factor = spia.payment_factor_life(
            "certain_only", 0, 1.0, "either", "arrears", 0.0, 0.0, 0.0, 0.0
        )
        floor = spia.certain_floor(
            "certain_only", case["certain_months"], 0.0, 0.0, row["month"]
        )
        payment = spia.annuity_payment(
            case["scheduled_installment"],
            spia.payment_factor(floor, life_factor),
            floor,
            0.0,
        )
        assert payment == pytest.approx(row["annuity_payment"])


@pytest.mark.parametrize("form", ["cash_refund", "installment_refund"])
def test_refund_forms_require_positive_premium(form):
    if form == "cash_refund":
        with pytest.raises(ValueError, match="requires positive premium"):
            spia.refund_claim(form, 0.0, "arrears", 0.0, 0.0, 1.0)
    else:
        with pytest.raises(ValueError, match="requires positive premium"):
            spia.scheduled_installment(form, 0.0, 500.0, 0.0)


def test_joint_cash_refund_requires_full_survivor_percentage():
    with pytest.raises(ValueError, match="survivor_percentage of 1"):
        spia.payment_factor_life(
            "cash_refund", 1, 0.5, "either", "arrears", 1.0, 1.0, 1.0, 1.0
        )


def test_rejects_unknown_benefit_form():
    data = _run_data(benefit_form="unknown")
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is not None
    assert result.error.error_type == "invalid_formula"
    assert "unsupported benefit_form" in result.error.message


def test_expense_and_cash_flow_statement_match_shipped_lifelib_cases():
    for row in _fixture()["shipped_expense_cases"]:
        inflation = spia.expense_inflation_factor(
            spia.DEFAULT_EXPENSE_INFLATION_RATE, row["projection_month"]
        )
        obligation = spia.obligation_factor(
            row["benefit_form"],
            row["certain_floor"],
            row["last_survivor_probability"],
        )
        expense = spia.maintenance_expense(
            spia.DEFAULT_EXPENSE_MAINTENANCE_ANNUAL,
            inflation,
            obligation,
        )
        liability = spia.liability_cash_flow(
            row["annuity_payment"], row["refund_claim"], 0.0, expense
        )
        assert inflation == pytest.approx(row["expense_inflation_factor"])
        assert obligation == pytest.approx(row["obligation_factor"])
        assert expense == pytest.approx(row["maintenance_expense"])
        assert liability == pytest.approx(row["liability_cash_flow"])
        assert spia.net_cash_flow(liability) == pytest.approx(row["net_cash_flow"])
        assert spia.cash_flow_identity_residual(
            liability,
            row["annuity_payment"],
            row["refund_claim"],
            0.0,
            expense,
        ) == pytest.approx(0.0, abs=1e-12)


def test_engine_cash_flow_and_present_value_identities_close_each_month():
    data = _run_data()
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is None, result.error and result.error.as_dict()
    annuity = _series(result, "annuity_payment")
    refunds = _series(result, "refund_claim")
    commutations = _series(result, "commutation_payment")
    expenses = _series(result, "maintenance_expense")
    liability = _series(result, "liability_cash_flow")
    net = _series(result, "net_cash_flow")
    residual = _series(result, "cash_flow_identity_residual")
    pv_annuity = _series(result, "pv_annuity_payment")
    pv_commutations = _series(result, "pv_commutation_payment")
    pv_expenses = _series(result, "pv_maintenance_expense")
    pv_liability = _series(result, "pv_liability_cash_flow")

    for values in zip(
        annuity,
        refunds,
        commutations,
        expenses,
        liability,
        net,
        residual,
        pv_annuity,
        pv_commutations,
        pv_expenses,
        pv_liability,
    ):
        annuity_value, refund, commutation, expense, gross, insurer_net, resid, pv_ann, pv_comm, pv_exp, pv_gross = values
        assert gross == pytest.approx(annuity_value + refund + commutation + expense)
        assert insurer_net == pytest.approx(-gross)
        assert resid == pytest.approx(0.0, abs=1e-12)
        assert pv_gross == pytest.approx(pv_ann + pv_comm + pv_exp)


def test_cash_refund_engine_includes_refund_and_expense_in_total_and_reserve():
    data = _run_data(benefit_form="cash_refund", premium=100000.0)
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is None, result.error and result.error.as_dict()
    refunds = _series(result, "refund_claim")
    expenses = _series(result, "maintenance_expense")
    liability = _series(result, "liability_cash_flow")
    annuity = _series(result, "annuity_payment")
    pv_liability = _series(result, "pv_liability_cash_flow")
    assert any(value > 0.0 for value in refunds)
    assert liability == pytest.approx(
        [payment + refund + expense for payment, refund, expense in zip(annuity, refunds, expenses)]
    )
    assert result.reserve_0 == pytest.approx(math.fsum(pv_liability), rel=1e-12)


def test_certain_only_obligation_stops_even_when_annuitant_survives():
    assert spia.obligation_factor("certain_only", 1.0, 0.9) == 1.0
    assert spia.obligation_factor("certain_only", 0.0, 0.9) == 0.0
    assert spia.obligation_factor("life_certain", 0.0, 0.9) == 0.9


def test_commutation_reference_mechanics_match_pinned_lifelib_sensitivity():
    rows = {
        row["month"]: row
        for row in _fixture()["shipped_commutation_sensitivity"]["rows"]
    }
    value = spia.commuted_value(
        "life_certain",
        120,
        100000.0,
        6515.0,
        6000.0,
        0.03,
        12,
        "arrears",
        13,
        0.0,
        0.04,
        "compound",
    )
    assert value == pytest.approx(rows[13]["commuted_value"], rel=1e-12)
    assert spia.commutation_eligible(1, 0, "life_certain", 1.0, 2, 13) == 1.0
    minimum = spia.minimum_remaining_guaranteed_installment(
        "life_certain", 120, 100000.0, 6515.0, 6000.0, 0.03, 12, "arrears", 13
    )
    amount = spia.commutation_amount(1.0, value, 0.1, 0.0, minimum, 5000.0, 100.0)
    assert amount == pytest.approx(rows[13]["commutation_amount"], rel=1e-12)
    assert spia.surrender_charge(amount, 2, 0.08) == pytest.approx(
        rows[13]["surrender_charge"], rel=1e-12
    )
    assert spia.commutation_payment(amount, 2, 0.08) == pytest.approx(
        rows[13]["commutation_payment"], rel=1e-12
    )
    fraction = spia.commutation_fraction(0.0, amount, value, 0.0, 14)
    assert fraction == pytest.approx(rows[14]["commutation_fraction"], rel=1e-12)
    assert spia.annuity_payment(515.0, 1.0, 1.0, fraction) == pytest.approx(
        rows[14]["annuity_payment"], rel=1e-12
    )

    year_three_value = spia.commuted_value(
        "life_certain", 120, 100000.0, 12710.45, 6000.0, 0.03, 12,
        "arrears", 25, fraction, 0.04, "compound",
    )
    assert year_three_value == pytest.approx(rows[25]["commuted_value"], rel=1e-12)
    assert spia.commutation_amount(
        1.0, year_three_value, 0.1, fraction, 530.45, 5000.0, 100.0
    ) == rows[25]["commutation_amount"]

    month_120_income = spia.annual_income_current(6000.0, 0.03, 120)
    month_120_installment = spia.scheduled_installment_untrimmed(
        month_120_income, 12, "arrears", 120
    )
    assert spia.annuity_payment(
        month_120_installment, 1.0, 1.0, fraction
    ) == pytest.approx(rows[120]["annuity_payment"], rel=1e-12)

    month_121_income = spia.annual_income_current(6000.0, 0.03, 121)
    month_121_installment = spia.scheduled_installment_untrimmed(
        month_121_income, 12, "arrears", 121
    )
    life_factor = rows[121]["annuity_payment"] / month_121_installment
    assert spia.annuity_payment(
        month_121_installment, life_factor, 0.0, fraction
    ) == pytest.approx(rows[121]["annuity_payment"], rel=1e-12)
    assert spia.annuity_payment(
        month_121_installment, life_factor, 0.0, fraction
    ) == spia.annuity_payment(month_121_installment, life_factor, 0.0, 0.0)


def test_optional_commutation_engine_matches_pinned_nonzero_boundary():
    data = _run_data(
        benefit_form="life_certain",
        premium=100000.0,
        certain_months=120,
        cola_rate=0.03,
        commutation_enabled=1,
        commutation_utilization_rate=0.1,
    )
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()
    rows = {
        row["month"]: row
        for row in _fixture()["shipped_commutation_sensitivity"]["rows"]
    }
    for month in (12, 13, 14, 24):
        index = month - 1
        for variable in (
            "commuted_value",
            "commutation_amount",
            "commutation_fraction",
            "commutation_payment",
            "annuity_payment",
            "liability_cash_flow",
        ):
            if variable in rows[month]:
                assert _series(result, variable)[index] == pytest.approx(
                    rows[month][variable], rel=1e-12, abs=1e-12
                )


def test_commutation_is_strictly_optional_and_jurisdiction_gated():
    assert spia.commutation_eligible(0, 0, "life_certain", 1.0, 2, 13) == 0.0
    assert spia.commutation_eligible(1, 1, "life_certain", 1.0, 2, 13) == 0.0
    assert spia.commutation_eligible(1, 0, "life_only", 0.0, 2, 13) == 0.0
    assert spia.commutation_eligible(1, 0, "life_certain", 1.0, 1, 1) == 0.0
    assert spia.commutation_eligible(1, 0, "life_certain", 1.0, 2, 14) == 0.0
    assert spia.commutation_eligible(1, 0, "life_certain", 1.0, 3, 25) == 1.0

    default_data = _run_data(benefit_form="life_certain", certain_months=120)
    default_result = run_policy(
        default_data, default_data.policies[0], FORMULA_FUNCTIONS
    )
    assert default_result.error is None
    assert _series(default_result, "commutation_payment") == pytest.approx([0.0] * 24)


def test_existing_inforce_policy_uses_contract_duration_for_commutation():
    data = _run_data(
        benefit_form="life_certain",
        certain_months=120,
        cola_rate=0.03,
        commutation_enabled=1,
        commutation_utilization_rate=0.2,
        issue_date="2024-12-31",
        issue_age=63,
        annuity_year=2024,
        joint_issue_age=63,
        commutation_fraction_at_valuation=0.25,
    )
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()
    assert _series(result, "commutation_eligible")[0] == 1.0
    assert _series(result, "annual_income_current")[0] == pytest.approx(6365.4)
    assert _series(result, "certain_floor")[0] == 1.0
    assert _series(result, "commutation_fraction")[0] == pytest.approx(0.25)
    assert _series(result, "annuity_payment")[0] == pytest.approx(397.8375)
    assert _series(result, "commutation_payment")[0] > 0.0
    assert _series(result, "commutation_fraction")[1] == pytest.approx(0.4)


def test_opening_cumulative_payments_control_installment_refund_boundary():
    data = _run_data(
        benefit_form="installment_refund",
        premium=100000.0,
        cumulative_scheduled_paid_at_valuation=99500.0,
    )
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()
    assert _series(result, "cumulative_scheduled_before_current")[:2] == pytest.approx(
        [99500.0, 100000.0]
    )
    assert _series(result, "cumulative_scheduled_payment")[:2] == pytest.approx(
        [100000.0, 100500.0]
    )
    assert _series(result, "certain_floor")[:2] == [1.0, 0.0]


def test_opening_cumulative_payments_reduce_cash_refund_claim():
    data = _run_data(
        benefit_form="cash_refund",
        premium=100000.0,
        cumulative_scheduled_paid_at_valuation=99500.0,
    )
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()
    first_death_probability = _series(result, "last_death_probability")[0]
    assert _series(result, "refund_claim")[0] == pytest.approx(
        first_death_probability * 500.0
    )


def test_missing_jurisdiction_status_fails_closed_when_commutation_is_enabled():
    data = _run_data(
        benefit_form="life_certain",
        certain_months=120,
        commutation_enabled=1,
        issue_state_excludes_withdrawal=None,
        commutation_utilization_rate=0.2,
        issue_date="2025-12-31",
        issue_age=64,
        annuity_year=2025,
        joint_issue_age=64,
    )
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)
    assert result.error is None, result.error and result.error.as_dict()
    assert _series(result, "commutation_eligible")[0] == 0.0
    assert _series(result, "commutation_payment")[0] == 0.0


def test_commutation_minimum_amount_and_remaining_payment_limits_apply():
    assert spia.commutation_amount(1.0, 43053.62154485113, 0.1, 0.1, 530.45, 5000.0, 100.0) == 0.0
    assert spia.commutation_amount(1.0, 10000.0, 0.5, 0.0, 100.0, 5000.0, 100.0) == 0.0
    with pytest.raises(ValueError, match="between 0 and 1"):
        spia.commutation_amount(1.0, 10000.0, 1.1, 0.0, 500.0, 5000.0, 100.0)
    with pytest.raises(ValueError, match="minimum_amount cannot be negative"):
        spia.commutation_amount(0.0, 0.0, 0.0, 0.0, 500.0, -1.0, 100.0)
    with pytest.raises(ValueError, match="minimum_payment cannot be negative"):
        spia.commutation_amount(0.0, 0.0, 0.0, 0.0, 500.0, 5000.0, -1.0)
    with pytest.raises(ValueError, match="at_valuation must be between 0 and 1"):
        spia.commutation_fraction(0.0, 0.0, 0.0, 1.1, 1)
    with pytest.raises(ValueError, match="paid_at_valuation cannot be negative"):
        spia.cumulative_scheduled_before_current(0.0, -1.0, 1)


def test_rejects_invalid_expense_assumptions():
    with pytest.raises(ValueError, match="greater than -1"):
        spia.expense_inflation_factor(-1.0, 1)
    with pytest.raises(ValueError, match="cannot be negative"):
        spia.maintenance_expense(-1.0, 1.0, 1.0)


@pytest.mark.parametrize("frequency", [0, 3, 5, 13, 4.5])
def test_rejects_unsupported_payment_frequency(frequency):
    data = _run_data(payment_frequency=frequency)
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is not None
    assert result.error.error_type == "invalid_formula"
    assert "payment_frequency must be one of" in result.error.message


def test_rejects_unknown_payment_timing():
    data = _run_data(payment_timing="middle")
    result = run_policy(data, data.policies[0], FORMULA_FUNCTIONS)

    assert result.error is not None
    assert result.error.error_type == "invalid_formula"
    assert "payment_timing must be" in result.error.message


def test_reference_model_definition_freezes_provenance_and_scope():
    version = spia.MODEL["version"]
    reference = version["configuration"]["reference_model"]

    assert version["version_label"] == "v1.0-reference"
    assert reference["source"]["commit"] == (
        "b706d8bbab530cc9ef94e06600e9c9d1468d8cb1"
    )
    assert reference["runtime_required"] is False
    assert reference["production_dependency"] is False
    assert version["configuration"]["reconciliation_scope"] == [
        "survival_probability",
        "joint_survival_probability",
        "last_survivor_probability",
        "payment_factor_life",
        "certain_floor",
        "payment_factor",
        "annuity_payment",
        "commutation_fraction",
        "commuted_value",
        "commutation_payment",
        "surrender_charge",
        "refund_claim",
        "obligation_factor",
        "maintenance_expense",
        "liability_cash_flow",
        "net_cash_flow",
        "cash_flow_identity_residual",
    ]
    commutation = version["configuration"]["commutation"]
    assert commutation["enabled_by_default"] is False
    assert commutation["assumption_status"] == "illustrative_unverified"
    assert commutation["valuation_use"] == "best_estimate_only"
    assert commutation["carvm_eligible"] is False
