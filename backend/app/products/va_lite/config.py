"""Illustrative VA vertical executed exclusively by the shared projection engine.

Selected Illustrative Cash-Flow & Exposure Timing Convention (Pending
ACT-P7-DEC-03, ACT-P7-DEC-04, ACT-DEC-01, ACT-DEC-02):
- Conditional per-surviving-policy account quantities (``eq_av_*``,
  ``bond_av_*``, ``fixed_av_*``, ``account_value_*``, ``total_fee_due``,
  ``total_fees_collected``, ``av_paid_withdrawal``, ``benefit_base_*``,
  ``gmwb_remaining_*``, ``gmdb_base_*``, ``rider_claim_payment``,
  ``nar_death_claim``) represent values conditional on the policy being in force
  at the start of month ``t``.
- Expected insurer cash flows are consistently survival-weighted using
  ``survival_open(t)`` (start-of-month in-force exposure, before EOM mortality
  and lapse decrements produce ``survival_close(t)``):
  * ``expected_fees_collected = total_fees_collected * survival_open``
  * ``rider_claims_incurred = rider_claim_payment * survival_open``
  * ``death_claims_incurred = nar_death_claim * survival_open * q_mort_m``
  * ``net_cash_flow = rider_claims_incurred + death_claims_incurred - expected_fees_collected``
- Consequently, after complete policy extinction (``survival_open == 0``),
  expected fee income and all expected insurer cash flows are identically ``0.0``.
"""

from __future__ import annotations

import math

from app.core.formula_engine.formulas import register_function
from app.products.shared_modules.variable_annuity import (
    monthly_fee_rate,
    va_guarantee_step,
    va_nar_death_claim,
)

PRODUCT_CODE = "VA"
PRODUCT_NAME = "Variable Annuity (Illustrative)"
FUNCTION_PREFIX = "va_illustrative"
IMPLEMENTATION_VERSION = "va-illustrative-m7-v2"
FUND_RETURN_TABLE = "VA_FUND_RETURN_PATHS"
MORTALITY_TABLE = "VA_MORTALITY"
LAPSE_TABLE = "VA_LAPSE"


def opening_balance(projection_month: float, initial_balance: float, previous_balance: float) -> float:
    return float(initial_balance if int(projection_month) == 1 else previous_balance)


def eq_av_open(projection_month: float, eq_account_value: float, eq_av_previous: float) -> float:
    return opening_balance(projection_month, eq_account_value, eq_av_previous)


def bond_av_open(projection_month: float, bond_account_value: float, bond_av_previous: float) -> float:
    return opening_balance(projection_month, bond_account_value, bond_av_previous)


def fixed_av_open(projection_month: float, fixed_account_value: float, fixed_av_previous: float) -> float:
    return opening_balance(projection_month, fixed_account_value, fixed_av_previous)


def guarantee_open(projection_month: float, initial_value: float, previous_value: float) -> float:
    return opening_balance(projection_month, initial_value, previous_value)


def benefit_base_open(projection_month: float, benefit_base: float, benefit_base_previous: float) -> float:
    return guarantee_open(projection_month, benefit_base, benefit_base_previous)


def gmwb_remaining_open(
    projection_month: float, gmwb_remaining_balance: float, gmwb_remaining_previous: float
) -> float:
    return guarantee_open(projection_month, gmwb_remaining_balance, gmwb_remaining_previous)


def gmdb_base_open(projection_month: float, gmdb_base: float, gmdb_base_previous: float) -> float:
    return guarantee_open(projection_month, gmdb_base, gmdb_base_previous)


def survival_open(projection_month: float, survival_previous: float) -> float:
    return 1.0 if int(projection_month) == 1 else float(survival_previous)


def fixed_monthly_return(fixed_credited_rate: float) -> float:
    rate = float(fixed_credited_rate)
    if not math.isfinite(rate) or rate <= -1.0:
        raise ValueError("fixed_credited_rate must be finite and greater than -1.")
    return (1.0 + rate) ** (1.0 / 12.0) - 1.0


def grown_balance(opening: float, monthly_return: float) -> float:
    rate = float(monthly_return)
    if not math.isfinite(rate) or rate <= -1.0:
        raise ValueError("Monthly fund return must be finite and greater than -1.")
    return float(opening) * (1.0 + rate)


def eq_av_after_growth(eq_av_open: float, eq_fund_return: float) -> float:
    return grown_balance(eq_av_open, eq_fund_return)


def bond_av_after_growth(bond_av_open: float, bond_fund_return: float) -> float:
    return grown_balance(bond_av_open, bond_fund_return)


def fixed_av_after_growth(fixed_av_open: float, fixed_monthly_return: float) -> float:
    return grown_balance(fixed_av_open, fixed_monthly_return)


def account_value_after_growth(
    eq_av_after_growth: float, bond_av_after_growth: float, fixed_av_after_growth: float
) -> float:
    return math.fsum((eq_av_after_growth, bond_av_after_growth, fixed_av_after_growth))


def total_fee_due(
    account_value_after_growth: float,
    benefit_base_open: float,
    me_fee_rate: float,
    admin_fee_rate: float,
    rider_fee_rate: float,
    fee_conversion_method: str,
    rider_fee_basis: str,
) -> float:
    if rider_fee_basis not in {"account_value", "benefit_base"}:
        raise ValueError("Unsupported rider_fee_basis.")
    asset_rate = monthly_fee_rate(me_fee_rate, fee_conversion_method) + monthly_fee_rate(
        admin_fee_rate, fee_conversion_method
    )
    rider_basis = account_value_after_growth if rider_fee_basis == "account_value" else benefit_base_open
    return account_value_after_growth * asset_rate + rider_basis * monthly_fee_rate(
        rider_fee_rate, fee_conversion_method
    )


def total_fees_collected(account_value_after_growth: float, total_fee_due: float) -> float:
    return min(float(account_value_after_growth), float(total_fee_due))


def account_value_after_fees(
    account_value_after_growth: float, total_fees_collected: float
) -> float:
    return float(account_value_after_growth) - float(total_fees_collected)


def av_paid_withdrawal(account_value_after_fees: float, withdrawal_amount_monthly: float) -> float:
    withdrawal = float(withdrawal_amount_monthly)
    if not math.isfinite(withdrawal) or withdrawal < 0.0:
        raise ValueError("withdrawal_amount_monthly must be finite and non-negative.")
    return min(float(account_value_after_fees), withdrawal)


def account_value_close(account_value_after_fees: float, av_paid_withdrawal: float) -> float:
    return float(account_value_after_fees) - float(av_paid_withdrawal)


def allocated_close(grown: float, account_value_after_growth: float, account_value_close: float) -> float:
    return float(grown) * float(account_value_close) / float(account_value_after_growth) if account_value_after_growth else 0.0


def eq_av_close(eq_av_after_growth: float, account_value_after_growth: float, account_value_close: float) -> float:
    return allocated_close(eq_av_after_growth, account_value_after_growth, account_value_close)


def bond_av_close(bond_av_after_growth: float, account_value_after_growth: float, account_value_close: float) -> float:
    return allocated_close(bond_av_after_growth, account_value_after_growth, account_value_close)


def fixed_av_close(fixed_av_after_growth: float, account_value_after_growth: float, account_value_close: float) -> float:
    return allocated_close(fixed_av_after_growth, account_value_after_growth, account_value_close)


def _guarantee(
    rider_type: str, benefit_base_open: float, gmwb_remaining_open: float,
    gmdb_base_open: float, account_value_after_fees: float, account_value_close: float,
    av_paid_withdrawal: float, withdrawal_amount_monthly: float, gwb_pct: float,
    rollup_rate: float, projection_month: float, survival_open: float,
    withdrawal_mode: str,
) -> dict[str, float]:
    return va_guarantee_step(
        rider_type=rider_type,
        benefit_base_open=benefit_base_open,
        gmwb_remaining_open=gmwb_remaining_open,
        gmdb_base_open=gmdb_base_open,
        account_value_before_withdrawal=account_value_after_fees,
        account_value_close=account_value_close,
        av_paid_withdrawal=av_paid_withdrawal,
        gross_withdrawal=withdrawal_amount_monthly,
        gwb_pct=gwb_pct,
        rollup_rate=rollup_rate,
        projection_month=int(projection_month),
        survival_open=survival_open,
        withdrawal_mode=withdrawal_mode,
    )


def benefit_base_close(**kwargs) -> float:
    return _guarantee(**kwargs)["benefit_base_close"]


def gmwb_remaining_close(**kwargs) -> float:
    return _guarantee(**kwargs)["gmwb_remaining_close"]


def gmdb_base_close(**kwargs) -> float:
    return _guarantee(**kwargs)["gmdb_base_close"]


def rider_claim_payment(**kwargs) -> float:
    return _guarantee(**kwargs)["rider_claim_payment"]


def monthly_probability(annual_rate: float) -> float:
    rate = float(annual_rate)
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        raise ValueError("Annual decrement must be finite and in [0, 1].")
    return 1.0 - (1.0 - rate) ** (1.0 / 12.0)


def q_mort_m(q_mort_annual: float) -> float:
    return monthly_probability(q_mort_annual)


def q_lapse_m(q_lapse_annual: float) -> float:
    return monthly_probability(q_lapse_annual)


def survival_close(survival_open: float, q_mort_m: float, q_lapse_m: float) -> float:
    return float(survival_open) * (1.0 - float(q_mort_m)) * (1.0 - float(q_lapse_m))


def rider_claims_incurred(rider_claim_payment: float, survival_open: float) -> float:
    return float(rider_claim_payment) * float(survival_open)


def nar_death_claim(
    account_value_close: float, gmdb_base_close: float, rider_type: str
) -> float:
    return va_nar_death_claim(account_value_close, gmdb_base_close, rider_type)


def death_claims_incurred(nar_death_claim: float, survival_open: float, q_mort_m: float) -> float:
    return float(nar_death_claim) * float(survival_open) * float(q_mort_m)


def expected_fees_collected(total_fees_collected: float, survival_open: float) -> float:
    return float(total_fees_collected) * float(survival_open)


def net_cash_flow(
    rider_claims_incurred: float,
    death_claims_incurred: float,
    expected_fees_collected: float,
) -> float:
    return float(rider_claims_incurred) + float(death_claims_incurred) - float(expected_fees_collected)


def discount_factor(discount_rate: float, projection_month: float) -> float:
    rate = float(discount_rate)
    if not math.isfinite(rate) or rate <= -1.0:
        raise ValueError("discount_rate must be finite and greater than -1.")
    return (1.0 + rate) ** (-float(projection_month) / 12.0)


def pv_net_cash_flow(net_cash_flow: float, discount_factor: float) -> float:
    return float(net_cash_flow) * float(discount_factor)


_FUNCTIONS = {
    name: value for name, value in globals().copy().items()
    if name in {
        "eq_av_open", "bond_av_open", "fixed_av_open", "benefit_base_open",
        "gmwb_remaining_open", "gmdb_base_open", "survival_open", "fixed_monthly_return",
        "eq_av_after_growth", "bond_av_after_growth", "fixed_av_after_growth",
        "account_value_after_growth", "total_fee_due", "total_fees_collected",
        "expected_fees_collected",
        "account_value_after_fees", "av_paid_withdrawal", "account_value_close",
        "eq_av_close", "bond_av_close", "fixed_av_close", "benefit_base_close",
        "gmwb_remaining_close", "gmdb_base_close", "rider_claim_payment", "q_mort_m",
        "q_lapse_m", "survival_close", "rider_claims_incurred", "nar_death_claim",
        "death_claims_incurred", "net_cash_flow", "discount_factor", "pv_net_cash_flow",
    }
}


def register_functions() -> None:
    for name, function in _FUNCTIONS.items():
        register_function(
            f"{FUNCTION_PREFIX}.{name}", function, expression_text=name,
            explanation="Illustrative VA shared-engine calculation; ACT-P7-DEC-03/04 pending.",
            illustrative=True, implementation_version=IMPLEMENTATION_VERSION,
        )


def _variable(name, kind, *, data_type="number", unit=None, source=None, default=None,
              required=True, override=False):
    row = {
        "name": name, "display_name": name.replace("_", " ").title(),
        "description": f"Illustrative VA {name}.", "kind": kind,
        "data_type": data_type, "unit": unit, "required": required,
        "source": source or {"type": kind, "column": name},
        "allow_scenario_override": override,
    }
    if default is not None:
        row["default_value"] = default
    return row


VARIABLES = [
    *[_variable(name, "input") for name in (
        "eq_account_value", "bond_account_value", "fixed_account_value", "fixed_credited_rate",
        "me_fee_rate", "admin_fee_rate", "rider_fee_rate", "benefit_base", "gmdb_base",
        "rollup_rate", "gwb_pct", "withdrawal_amount_monthly", "discount_rate",
    )],
    _variable("gmwb_remaining_balance", "input", default=0.0, required=False),
    *[_variable(name, "input", data_type="string") for name in (
        "eq_fund_code", "bond_fund_code", "fee_conversion_method", "rider_type",
        "withdrawal_mode", "rider_fee_basis",
    )],
    _variable("projection_month", "context", source={"type": "context", "field": "projection_month"}),
    _variable("attained_age", "context", source={"type": "context", "field": "attained_age"}),
    _variable("gender", "input", data_type="string", source={"type": "input", "column": "gender", "transform": "upper"}),
    _variable("path_id", "manual", default=0.0, source={"type": "manual", "value": 0.0}, override=True),
    _variable("eq_fund_return", "factor", source={
        "type": "factor", "table": FUND_RETURN_TABLE, "value_column": "return",
        "key_map": {"path_id": "path_id", "projection_month": "projection_month", "fund_code": "eq_fund_code"},
    }),
    _variable("bond_fund_return", "factor", source={
        "type": "factor", "table": FUND_RETURN_TABLE, "value_column": "return",
        "key_map": {"path_id": "path_id", "projection_month": "projection_month", "fund_code": "bond_fund_code"},
    }),
    _variable("q_mort_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": MORTALITY_TABLE, "value_column": "rate",
        "key_map": {"attained_age": "attained_age", "gender": "gender"},
    }),
    _variable("q_lapse_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": LAPSE_TABLE, "value_column": "rate",
        "key_map": {"attained_age": "attained_age"},
    }),
]

for name, output, initial in (
    ("eq_av_previous", "eq_av_close", 0.0), ("bond_av_previous", "bond_av_close", 0.0),
    ("fixed_av_previous", "fixed_av_close", 0.0),
    ("benefit_base_previous", "benefit_base_close", 0.0),
    ("gmwb_remaining_previous", "gmwb_remaining_close", 0.0),
    ("gmdb_base_previous", "gmdb_base_close", 0.0),
    ("survival_previous", "survival_close", 1.0),
):
    VARIABLES.append(_variable(name, "prior_output", source={
        "type": "prior_output", "variable": output, "offset": 1, "initial_value": initial,
    }))

_GUARANTEE_DEPS = [
    "rider_type", "benefit_base_open", "gmwb_remaining_open", "gmdb_base_open",
    "account_value_after_fees", "account_value_close", "av_paid_withdrawal",
    "withdrawal_amount_monthly", "gwb_pct", "rollup_rate", "projection_month",
    "survival_open", "withdrawal_mode",
]
_FORMULAS = [
    ("eq_av_open", ["projection_month", "eq_account_value", "eq_av_previous"]),
    ("bond_av_open", ["projection_month", "bond_account_value", "bond_av_previous"]),
    ("fixed_av_open", ["projection_month", "fixed_account_value", "fixed_av_previous"]),
    ("benefit_base_open", ["projection_month", "benefit_base", "benefit_base_previous"]),
    ("gmwb_remaining_open", ["projection_month", "gmwb_remaining_balance", "gmwb_remaining_previous"]),
    ("gmdb_base_open", ["projection_month", "gmdb_base", "gmdb_base_previous"]),
    ("survival_open", ["projection_month", "survival_previous"]),
    ("fixed_monthly_return", ["fixed_credited_rate"]),
    ("eq_av_after_growth", ["eq_av_open", "eq_fund_return"]),
    ("bond_av_after_growth", ["bond_av_open", "bond_fund_return"]),
    ("fixed_av_after_growth", ["fixed_av_open", "fixed_monthly_return"]),
    ("account_value_after_growth", ["eq_av_after_growth", "bond_av_after_growth", "fixed_av_after_growth"]),
    ("total_fee_due", ["account_value_after_growth", "benefit_base_open", "me_fee_rate", "admin_fee_rate", "rider_fee_rate", "fee_conversion_method", "rider_fee_basis"]),
    ("total_fees_collected", ["account_value_after_growth", "total_fee_due"]),
    ("account_value_after_fees", ["account_value_after_growth", "total_fees_collected"]),
    ("av_paid_withdrawal", ["account_value_after_fees", "withdrawal_amount_monthly"]),
    ("account_value_close", ["account_value_after_fees", "av_paid_withdrawal"]),
    ("eq_av_close", ["eq_av_after_growth", "account_value_after_growth", "account_value_close"]),
    ("bond_av_close", ["bond_av_after_growth", "account_value_after_growth", "account_value_close"]),
    ("fixed_av_close", ["fixed_av_after_growth", "account_value_after_growth", "account_value_close"]),
    *[(name, list(_GUARANTEE_DEPS)) for name in (
        "benefit_base_close", "gmwb_remaining_close", "gmdb_base_close", "rider_claim_payment"
    )],
    ("q_mort_m", ["q_mort_annual"]), ("q_lapse_m", ["q_lapse_annual"]),
    ("survival_close", ["survival_open", "q_mort_m", "q_lapse_m"]),
    ("expected_fees_collected", ["total_fees_collected", "survival_open"]),
    ("rider_claims_incurred", ["rider_claim_payment", "survival_open"]),
    ("nar_death_claim", ["account_value_close", "gmdb_base_close", "rider_type"]),
    ("death_claims_incurred", ["nar_death_claim", "survival_open", "q_mort_m"]),
    ("net_cash_flow", ["rider_claims_incurred", "death_claims_incurred", "expected_fees_collected"]),
    ("discount_factor", ["discount_rate", "projection_month"]),
    ("pv_net_cash_flow", ["net_cash_flow", "discount_factor"]),
]

for name, _dependencies in _FORMULAS:
    unit = "probability" if name in {"survival_open", "survival_close", "q_mort_m", "q_lapse_m"} else "factor" if name == "discount_factor" else "USD"
    VARIABLES.append(_variable(name, "formula", unit=unit, source={"type": "formula"}))
VARIABLES.append(_variable("reserve", "valuation", source={
    "type": "valuation", "method": "illustrative_prospective_reserve",
    "cash_flow": "net_cash_flow", "rate": "discount_rate", "consistency_sum_of": "pv_net_cash_flow",
}))
FORMULAS = [{
    "name": name, "output_variable": name, "function_ref": f"{FUNCTION_PREFIX}.{name}",
    "dependencies": dependencies,
    "unit": next(v["unit"] for v in VARIABLES if v["name"] == name) or "USD",
} for name, dependencies in _FORMULAS]

_OUTPUTS = [
    "survival_open", "survival_close",
    "eq_av_close", "bond_av_close", "fixed_av_close", "account_value_close",
    "benefit_base_close", "gmwb_remaining_close", "gmdb_base_close",
    "total_fees_collected", "expected_fees_collected",
    "rider_claims_incurred", "death_claims_incurred",
    "net_cash_flow", "pv_net_cash_flow", "reserve",
]
PUBLISHED_OUTPUTS = [{
    "variable_name": name, "display_name": name.replace("_", " ").title(),
    "unit": "probability" if name in {"survival_open", "survival_close"} else "USD",
    "dimension": "policy_month",
    "aggregation": "sum" if name in {"total_fees_collected", "expected_fees_collected", "rider_claims_incurred", "death_claims_incurred", "net_cash_flow", "pv_net_cash_flow"} else "end_of_period",
    "description": (
        "Conditional per-surviving-policy account fee; not survival-weighted."
        if name == "total_fees_collected"
        else "Survival-weighted expected insurer fee income (total_fees_collected * survival_open)."
        if name == "expected_fees_collected"
        else "Synthetic illustrative VA output; owner confirmation pending."
    ),
} for name in _OUTPUTS]

MODEL = {
    "name": "VA Illustrative M7", "product_code": PRODUCT_CODE,
    "description": "Synthetic engineering model; not actuarially approved.",
    "version": {
        "version_label": "m7-v1", "block_name": "Synthetic VA block",
        "profile_name": "Illustrative", "basis": "Illustrative",
        "methodology": "Separate/fixed account projection with explicit guarantee type",
        "notes": "ACT-P7-DEC-03/04 and ACT-DEC-01/02 remain open.",
        "configuration": {"timing_convention": "EOM", "is_synthetic": True, "is_production_approved": False},
    },
    "formula_group": {"name": "VA M7", "description": "VA shared-engine formulas", "lineage_state": "local", "version_label": "v1"},
}
MODEL_DEFINITION = {"model": MODEL, "variables": VARIABLES, "formulas": FORMULAS, "published_outputs": PUBLISHED_OUTPUTS}
