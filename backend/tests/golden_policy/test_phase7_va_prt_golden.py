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


def _run_va_case(
    *,
    horizon_months: int,
    q_mort_annual: float,
    q_lapse_annual: float = 0.0,
    eq_av: float = 100_000.0,
    me_fee_rate: float = 0.012,
    admin_fee_rate: float = 0.006,
    rider_fee_rate: float = 0.006,
    rider_type: str = "NONE",
    benefit_base: float = 100_000.0,
    gmdb_base: float = 0.0,
    gwb_pct: float = 0.05,
    withdrawal_amount_monthly: float = 0.0,
):
    va.register_functions()
    variables, formulas = _definition_parts(va)
    fund = _table(
        va.FUND_RETURN_TABLE, ("path_id", "projection_month", "fund_code"), "return",
        [
            {"path_id": 0, "projection_month": m, "fund_code": fund_code, "return": 0.0}
            for m in range(1, horizon_months + 1)
            for fund_code in ("EQ", "BOND")
        ],
        kind="factor",
    )
    mortality = _table(
        va.MORTALITY_TABLE, ("attained_age", "gender"), "rate",
        [{"attained_age": 45, "gender": "F", "rate": q_mort_annual}],
    )
    lapse = _table(
        va.LAPSE_TABLE, ("attained_age",), "rate",
        [{"attained_age": 45, "rate": q_lapse_annual}],
    )
    policy = PolicyRecord(policy_id="VA-SURV", data={
        "issue_date": "2025-01-01", "issue_age": 45, "gender": "F",
        "eq_account_value": eq_av, "bond_account_value": 0.0, "fixed_account_value": 0.0,
        "fixed_credited_rate": 0.0, "me_fee_rate": me_fee_rate, "admin_fee_rate": admin_fee_rate,
        "rider_fee_rate": rider_fee_rate, "benefit_base": benefit_base, "gmdb_base": gmdb_base,
        "gmwb_remaining_balance": 0.0, "rollup_rate": 0.0, "gwb_pct": gwb_pct,
        "withdrawal_amount_monthly": withdrawal_amount_monthly, "discount_rate": 0.0,
        "eq_fund_code": "EQ", "bond_fund_code": "BOND",
        "fee_conversion_method": "simple_12th", "rider_type": rider_type,
        "withdrawal_mode": "pro_rata", "rider_fee_basis": "account_value",
    })
    data = RunData(
        variables=variables, formulas=formulas,
        tables={fund.name: fund, mortality.name: mortality, lapse.name: lapse},
        policies=[policy], scenario=ScenarioSpec(id="base", name="Base"),
        valuation_date=date(2025, 1, 31), horizon_months=horizon_months,
        output_variables=[row["variable_name"] for row in va.PUBLISHED_OUTPUTS],
        traced_policy_ids=frozenset({policy.policy_id}),
    )
    result = run_policy(data, policy, FORMULA_FUNCTIONS)
    assert result.error is None, result.error
    return {(month, name): value for month, name, value in result.outputs}, result


def test_va_survival_exposure_consistency_across_survival_regimes():
    """Verify conditional account fees vs survival-weighted expected insurer fees & cash flows."""
    # 1. Survival = 1.0 (zero decrements), positive fees (2.4%/yr -> 0.2%/mo), no withdrawals
    out_s1, _ = _run_va_case(horizon_months=3, q_mort_annual=0.0, q_lapse_annual=0.0)
    for m in range(1, 4):
        assert out_s1[(m, "survival_open")] == pytest.approx(1.0)
        assert out_s1[(m, "survival_close")] == pytest.approx(1.0)
        assert out_s1[(m, "expected_fees_collected")] == pytest.approx(out_s1[(m, "total_fees_collected")])
        assert out_s1[(m, "net_cash_flow")] == pytest.approx(-out_s1[(m, "expected_fees_collected")])
    # Month 1 fee = 100,000 * 0.002 = 200; Month 2 = 99,800 * 0.002 = 199.6
    assert out_s1[(1, "expected_fees_collected")] == pytest.approx(200.0)
    assert out_s1[(2, "expected_fees_collected")] == pytest.approx(199.6)

    # 2. Survival declining over time (q_mort_m = 0.2 -> annual = 1 - 0.8**12), positive fees, no withdrawals
    q_mort_annual = 1.0 - (0.8**12)
    out_dec, _ = _run_va_case(horizon_months=3, q_mort_annual=q_mort_annual, q_lapse_annual=0.0)
    for m, expected_surv_open in ((1, 1.0), (2, 0.8), (3, 0.64)):
        assert out_dec[(m, "survival_open")] == pytest.approx(expected_surv_open, abs=1e-12)
        assert out_dec[(m, "expected_fees_collected")] == pytest.approx(
            out_dec[(m, "total_fees_collected")] * expected_surv_open, abs=1e-9
        )
        assert out_dec[(m, "net_cash_flow")] == pytest.approx(-out_dec[(m, "expected_fees_collected")], abs=1e-9)
    # Conditional account fees are identical regardless of survival, while expected insurer fees scale with survival_open
    assert out_dec[(2, "total_fees_collected")] == pytest.approx(out_s1[(2, "total_fees_collected")], abs=1e-9)
    assert out_dec[(2, "expected_fees_collected")] == pytest.approx(0.8 * 199.6, abs=1e-9)
    assert out_dec[(3, "expected_fees_collected")] == pytest.approx(0.64 * out_s1[(3, "total_fees_collected")], abs=1e-9)

    # 3. 100% mortality in Month 1 (q_mort_annual = 1.0 -> q_mort_m = 1.0, survival_close = 0.0)
    # After complete extinction (Months 2 and 3: survival_open == 0.0), ZERO expected fees are recognized!
    out_ext, _ = _run_va_case(
        horizon_months=3, q_mort_annual=1.0, q_lapse_annual=0.0,
        rider_type="GMDB_ROP", gmdb_base=110_000.0,
    )
    assert out_ext[(1, "survival_open")] == pytest.approx(1.0)
    assert out_ext[(1, "survival_close")] == pytest.approx(0.0)
    assert out_ext[(1, "expected_fees_collected")] == pytest.approx(200.0)
    assert out_ext[(1, "death_claims_incurred")] == pytest.approx(110_000.0 - 99_800.0)
    for m in (2, 3):
        assert out_ext[(m, "survival_open")] == 0.0
        assert out_ext[(m, "survival_close")] == 0.0
        assert out_ext[(m, "total_fees_collected")] > 0.0  # conditional per-surviving-policy quantity
        assert out_ext[(m, "expected_fees_collected")] == 0.0  # zero expected insurer income after extinction!
        assert out_ext[(m, "rider_claims_incurred")] == 0.0
        assert out_ext[(m, "death_claims_incurred")] == 0.0
        assert out_ext[(m, "net_cash_flow")] == 0.0

    # 4. AV exhaustion + GLWB rider payments with declining survival (q_mort_m = 0.5)
    out_glwb, _ = _run_va_case(
        horizon_months=3,
        q_mort_annual=1.0 - (0.5**12),
        eq_av=0.0,
        me_fee_rate=0.012,
        admin_fee_rate=0.0,
        rider_fee_rate=0.01,
        rider_type="GLWB",
        benefit_base=120_000.0,
        gwb_pct=0.05,
        withdrawal_amount_monthly=500.0,
    )
    # Monthly GLWB allowance = 120,000 * 0.05 / 12 = 500; AV=0 so rider pays 500 conditional, survival-weighted by 1.0, 0.5, 0.25
    assert [out_glwb[(m, "expected_fees_collected")] for m in (1, 2, 3)] == [0.0, 0.0, 0.0]
    assert [out_glwb[(m, "rider_claims_incurred")] for m in (1, 2, 3)] == pytest.approx([500.0, 250.0, 125.0], abs=1e-9)
    assert [out_glwb[(m, "net_cash_flow")] for m in (1, 2, 3)] == pytest.approx([500.0, 250.0, 125.0], abs=1e-9)
    assert out_glwb[(0, "reserve")] == pytest.approx(875.0, abs=1e-9)


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
