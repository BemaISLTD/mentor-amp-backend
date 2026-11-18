from datetime import date

import pytest

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import order_formulas, run_policy
from app.core.projection_engine.run_data import (
    FormulaSpec, PolicyRecord, RunData, ScenarioSpec, TableSpec, VariableSpec,
)
from app.products.prt_lite import config as prt
from app.products.va_lite import config as va


def _definition_parts(config):
    variables = {
        row["name"]: VariableSpec(
            name=row["name"], kind=row["kind"], data_type=row.get("data_type", "number"),
            unit=row.get("unit"), source=dict(row.get("source") or {}),
            default_value=row.get("default_value"), required=row.get("required", True),
            allow_scenario_override=row.get("allow_scenario_override", False),
        )
        for row in config.VARIABLES
    }
    formulas = order_formulas([
        FormulaSpec(
            id=f"{config.PRODUCT_CODE}-{index}", name=row["name"],
            output_variable=row["output_variable"], function_ref=row["function_ref"],
            dependencies=tuple(row["dependencies"]), illustrative=True,
        )
        for index, row in enumerate(config.FORMULAS, start=1)
    ])
    return variables, formulas


def _table(name, keys, value, rows, kind="assumption"):
    table = TableSpec(
        id=f"table-{name}", name=name, key_columns=tuple(keys), value_column=value,
        rows=rows, kind=kind,
    )
    assert table.build_index() == []
    return table


def test_va_glwb_av_exhaustion_claim_executes_through_shared_engine():
    va.register_functions()
    variables, formulas = _definition_parts(va)
    fund = _table(
        va.FUND_RETURN_TABLE, ("path_id", "projection_month", "fund_code"), "return",
        [
            {"path_id": 0, "projection_month": 1, "fund_code": "EQ", "return": 0.0},
            {"path_id": 0, "projection_month": 1, "fund_code": "BOND", "return": 0.0},
        ], kind="factor",
    )
    mortality = _table(
        va.MORTALITY_TABLE, ("attained_age", "gender"), "rate",
        [{"attained_age": 45, "gender": "F", "rate": 0.0}],
    )
    lapse = _table(
        va.LAPSE_TABLE, ("attained_age",), "rate", [{"attained_age": 45, "rate": 0.0}],
    )
    policy = PolicyRecord(policy_id="VA-GLWB", data={
        "issue_date": "2025-01-01", "issue_age": 45, "gender": "F",
        "eq_account_value": 0.0, "bond_account_value": 0.0, "fixed_account_value": 0.0,
        "fixed_credited_rate": 0.0, "me_fee_rate": 0.0, "admin_fee_rate": 0.0,
        "rider_fee_rate": 0.0, "benefit_base": 100_000.0, "gmdb_base": 0.0,
        "gmwb_remaining_balance": 0.0, "rollup_rate": 0.0, "gwb_pct": 0.05,
        "withdrawal_amount_monthly": 5_000.0 / 12.0, "discount_rate": 0.0,
        "eq_fund_code": "EQ", "bond_fund_code": "BOND",
        "fee_conversion_method": "simple_12th", "rider_type": "GLWB",
        "withdrawal_mode": "pro_rata", "rider_fee_basis": "account_value",
    })
    data = RunData(
        variables=variables, formulas=formulas,
        tables={fund.name: fund, mortality.name: mortality, lapse.name: lapse},
        policies=[policy], scenario=ScenarioSpec(id="base", name="Base"),
        valuation_date=date(2025, 1, 31), horizon_months=1,
        output_variables=[row["variable_name"] for row in va.PUBLISHED_OUTPUTS],
        traced_policy_ids=frozenset({policy.policy_id}),
    )
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error
    output = {(month, name): value for month, name, value in result.outputs}
    assert output[(1, "account_value_close")] == 0.0
    assert output[(1, "benefit_base_close")] == 100_000.0
    assert output[(1, "rider_claims_incurred")] == pytest.approx(5_000.0 / 12.0)
    assert output[(0, "reserve")] == pytest.approx(5_000.0 / 12.0)


def test_prt_life_only_zero_termination_pays_ninety_percent_anchor():
    prt.register_functions()
    variables, formulas = _definition_parts(prt)
    primary = _table(
        prt.PRIMARY_MORTALITY_TABLE, ("attained_age", "gender"), "rate",
        [{"attained_age": 65, "gender": "M", "rate": 1.0 - 0.9**12}],
    )
    spouse = _table(
        prt.SPOUSE_MORTALITY_TABLE,
        ("form_of_annuity", "spouse_age", "spouse_gender"), "rate",
        [{"form_of_annuity": "life_only", "spouse_age": -1, "spouse_gender": "NOT_APPLICABLE", "rate": 0.0}],
    )
    termination = _table(
        prt.OPTIONAL_TERMINATION_TABLE, ("policy_year",), "rate",
        [{"policy_year": 1, "rate": 0.0}],
    )
    policy = PolicyRecord(policy_id="PRT-LIFE", data={
        "issue_date": "2025-01-01", "issue_age": 65, "gender": "M",
        "participant_status": "in_pay", "form_of_annuity": "life_only",
        "monthly_benefit": 1_000.0, "deferral_months": 0, "certain_months": 0,
        "joint_survivor_pct": 0.0, "cola_rate": 0.0, "cola_cap": 0.0,
        "expense_pct_of_benefit": 0.0, "discount_rate": 0.0,
    })
    data = RunData(
        variables=variables, formulas=formulas,
        tables={primary.name: primary, spouse.name: spouse, termination.name: termination},
        policies=[policy], scenario=ScenarioSpec(id="base", name="Base"),
        valuation_date=date(2025, 1, 31), horizon_months=1,
        output_variables=[row["variable_name"] for row in prt.PUBLISHED_OUTPUTS],
        traced_policy_ids=frozenset({policy.policy_id}),
    )
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error
    output = {(month, name): value for month, name, value in result.outputs}
    assert output[(1, "non_termination_survival_close")] == 1.0
    assert output[(1, "payable_benefit_factor")] == pytest.approx(0.9)
    assert output[(1, "expected_benefit_payment")] == pytest.approx(900.0)
    assert output[(0, "reserve")] == pytest.approx(900.0)


def test_prt_spouse_mortality_lookup_advances_attained_age_at_anniversary():
    prt.register_functions()
    variables, formulas = _definition_parts(prt)
    primary = _table(
        prt.PRIMARY_MORTALITY_TABLE, ("attained_age", "gender"), "rate",
        [
            {"attained_age": age, "gender": "M", "rate": 0.0}
            for age in (65, 66)
        ],
    )
    spouse = _table(
        prt.SPOUSE_MORTALITY_TABLE,
        ("form_of_annuity", "spouse_age", "spouse_gender"), "rate",
        [
            {"form_of_annuity": "joint_survivor", "spouse_age": 64, "spouse_gender": "F", "rate": 0.0},
            {"form_of_annuity": "joint_survivor", "spouse_age": 65, "spouse_gender": "F", "rate": 0.12},
        ],
    )
    termination = _table(
        prt.OPTIONAL_TERMINATION_TABLE, ("policy_year",), "rate",
        [{"policy_year": year, "rate": 0.0} for year in (1, 2)],
    )
    policy = PolicyRecord(policy_id="PRT-JS", data={
        "issue_date": "2025-01-01", "issue_age": 65, "gender": "M",
        "spouse_age": 64, "spouse_gender": "F",
        "participant_status": "in_pay", "form_of_annuity": "joint_survivor",
        "monthly_benefit": 1_000.0, "deferral_months": 0, "certain_months": 0,
        "joint_survivor_pct": 0.5, "cola_rate": 0.0, "cola_cap": 0.0,
        "expense_pct_of_benefit": 0.0, "discount_rate": 0.0,
    })
    data = RunData(
        variables=variables, formulas=formulas,
        tables={primary.name: primary, spouse.name: spouse, termination.name: termination},
        policies=[policy], scenario=ScenarioSpec(id="base", name="Base"),
        valuation_date=date(2025, 1, 31), horizon_months=13,
        output_variables=[row["variable_name"] for row in prt.PUBLISHED_OUTPUTS],
        traced_policy_ids=frozenset({policy.policy_id}),
    )
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error
    lookups = {
        row["projection_month"]: row["lookup_keys"]
        for row in result.trace_rows
        if row["variable_name"] == "q_mort_spouse_annual"
    }
    assert lookups[12]["spouse_age"] == 64
    assert lookups[13]["spouse_age"] == 65


@pytest.mark.skip(reason="Pending Actuarial Product Owner signed VA oracle workbook per CAS Section 17")
def test_owner_approved_va_golden():
    pass


@pytest.mark.skip(reason="Pending Actuarial Product Owner signed PRT oracle workbook per CAS Section 17")
def test_owner_approved_prt_golden():
    pass
