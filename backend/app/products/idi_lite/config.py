"""Illustrative IDI reference vertical executed by the shared projection engine.

Contract choices and synthetic tables remain pending Product Owner confirmation
under ACT-P7-DEC-01 and ACT-P7-DEC-02.
"""

from __future__ import annotations

import math

from app.core.formula_engine.formulas import register_function
from app.products.shared_modules.disability import (
    idi_benefit_payable,
    idi_net_liability_cash_flow,
    monthly_incidence_prob,
    monthly_termination_prob,
)

PRODUCT_CODE = "IDI"
PRODUCT_NAME = "Individual Disability Income (Illustrative)"
FUNCTION_PREFIX = "idi_illustrative"
IMPLEMENTATION_VERSION = "idi-illustrative-m7-v1"

ACTIVE_MORTALITY_TABLE = "IDI_ACTIVE_MORTALITY"
INCIDENCE_TABLE = "IDI_INCIDENCE"
ACTIVE_LAPSE_TABLE = "IDI_ACTIVE_LAPSE"
DISABLED_TERMINATION_TABLE = "IDI_DISABLED_TERMINATION"
DISABLED_MORTALITY_TABLE = "IDI_DISABLED_MORTALITY"
NEW_CLAIM_DLR_TABLE = "IDI_NEW_CLAIM_DLR"


def q_mort_active_monthly(q_mort_active_annual: float) -> float:
    return monthly_termination_prob(q_mort_active_annual, "annual")


def q_lapse_monthly(q_lapse_annual: float) -> float:
    return monthly_termination_prob(q_lapse_annual, "annual")


def q_term_monthly(q_term_annual: float) -> float:
    return monthly_termination_prob(q_term_annual, "annual")


def q_mort_disabled_monthly(q_mort_disabled_annual: float) -> float:
    return monthly_termination_prob(q_mort_disabled_annual, "annual")


def incidence_probability(q_inc_annual: float, incidence_multiplier: float) -> float:
    multiplier = float(incidence_multiplier)
    if not math.isfinite(multiplier) or multiplier < 0.0:
        raise ValueError("incidence_multiplier must be finite and non-negative.")
    annual = float(q_inc_annual) * multiplier
    if not 0.0 <= annual <= 1.0:
        raise ValueError("Adjusted annual incidence must be in [0, 1].")
    return monthly_incidence_prob(annual)


def active_probability_open(
    projection_month: float,
    claim_status: str,
    active_prob_previous: float,
) -> float:
    if int(projection_month) == 1:
        return 1.0 if claim_status == "active" else 0.0
    return float(active_prob_previous)


def disabled_probability_open(
    projection_month: float,
    claim_status: str,
    disabled_prob_previous: float,
) -> float:
    if int(projection_month) == 1:
        return 1.0 if claim_status == "disabled" else 0.0
    return float(disabled_prob_previous)


def claim_elapsed_open(
    projection_month: float,
    claim_status: str,
    claim_months_elapsed: float,
    claim_elapsed_previous: float,
) -> float:
    if int(projection_month) == 1:
        return float(claim_months_elapsed if claim_status == "disabled" else 0.0)
    return float(claim_elapsed_previous)


def new_disabled(active_prob_open: float, q_inc_m: float) -> float:
    return float(active_prob_open) * float(q_inc_m)


def active_probability_close(
    active_prob_open: float,
    q_mort_active_m: float,
    q_lapse_m: float,
    q_inc_m: float,
) -> float:
    decrement = float(q_mort_active_m) + float(q_lapse_m) + float(q_inc_m)
    if decrement > 1.0:
        raise ValueError("Competing active decrements cannot exceed 1.0.")
    return float(active_prob_open) * (1.0 - decrement)


def disabled_surviving(
    disabled_prob_open: float,
    q_term_m: float,
    q_mort_disabled_m: float,
) -> float:
    return float(disabled_prob_open) * (1.0 - float(q_term_m)) * (1.0 - float(q_mort_disabled_m))


def disabled_probability_close(disabled_surviving: float, new_disabled: float) -> float:
    return float(disabled_surviving) + float(new_disabled)


def claim_elapsed_close(claim_elapsed_open: float, disabled_prob_open: float) -> float:
    return float(claim_elapsed_open) + 1.0 if float(disabled_prob_open) > 0.0 else 0.0


def expected_premium_inflow(
    active_prob_close: float,
    disabled_prob_close: float,
    annual_premium: float,
    waive_premium_on_disability: float,
) -> float:
    flag = int(waive_premium_on_disability)
    if flag not in {0, 1}:
        raise ValueError("waive_premium_on_disability must be 0 or 1.")
    exposure = float(active_prob_close) if flag else float(active_prob_close) + float(disabled_prob_close)
    return exposure * float(annual_premium) / 12.0


def expected_claim_benefit_outgo(
    claim_elapsed_close: float,
    elimination_months: float,
    max_benefit_months: float,
    monthly_benefit: float,
    disabled_surviving: float,
) -> float:
    return idi_benefit_payable(
        int(claim_elapsed_close),
        int(elimination_months),
        int(max_benefit_months),
        monthly_benefit,
        disabled_surviving,
    )


def expected_expense_outgo(
    active_prob_close: float,
    disabled_prob_close: float,
    expense_per_policy_monthly: float,
) -> float:
    return (float(active_prob_close) + float(disabled_prob_close)) * float(expense_per_policy_monthly)


def expected_new_claim_incurred(
    new_disabled: float,
    monthly_benefit: float,
    new_claim_dlr_factor: float,
) -> float:
    return float(new_disabled) * float(monthly_benefit) * float(new_claim_dlr_factor)


def net_liability_cash_flow(
    expected_claim_benefit_outgo: float,
    expected_expense_outgo: float,
    expected_premium_inflow: float,
    expected_new_claim_incurred: float,
) -> float:
    return idi_net_liability_cash_flow(
        expected_claim_benefit_outgo,
        expected_expense_outgo,
        expected_premium_inflow,
        expected_new_claim_incurred,
    )


def discount_factor(discount_rate: float, projection_month: float) -> float:
    rate = float(discount_rate)
    if not math.isfinite(rate) or rate <= -1.0:
        raise ValueError("discount_rate must be finite and greater than -1.")
    return (1.0 + rate) ** (-float(projection_month) / 12.0)


def pv_net_cash_flow(net_liability_cash_flow: float, discount_factor: float) -> float:
    return float(net_liability_cash_flow) * float(discount_factor)


_FUNCTIONS = {
    "q_mort_active_monthly": q_mort_active_monthly,
    "q_lapse_monthly": q_lapse_monthly,
    "q_term_monthly": q_term_monthly,
    "q_mort_disabled_monthly": q_mort_disabled_monthly,
    "incidence_probability": incidence_probability,
    "active_probability_open": active_probability_open,
    "disabled_probability_open": disabled_probability_open,
    "claim_elapsed_open": claim_elapsed_open,
    "new_disabled": new_disabled,
    "active_probability_close": active_probability_close,
    "disabled_surviving": disabled_surviving,
    "disabled_probability_close": disabled_probability_close,
    "claim_elapsed_close": claim_elapsed_close,
    "expected_premium_inflow": expected_premium_inflow,
    "expected_claim_benefit_outgo": expected_claim_benefit_outgo,
    "expected_expense_outgo": expected_expense_outgo,
    "expected_new_claim_incurred": expected_new_claim_incurred,
    "net_liability_cash_flow": net_liability_cash_flow,
    "discount_factor": discount_factor,
    "pv_net_cash_flow": pv_net_cash_flow,
}

def register_functions() -> None:
    for name, function in _FUNCTIONS.items():
        register_function(
            f"{FUNCTION_PREFIX}.{name}",
            function,
            expression_text=name,
            explanation=(function.__doc__ or "Illustrative IDI shared-engine calculation.").strip(),
            illustrative=True,
            implementation_version=IMPLEMENTATION_VERSION,
        )


def _variable(
    name: str,
    kind: str,
    *,
    data_type: str = "number",
    unit: str | None = None,
    source: dict | None = None,
    default_value=None,
    required: bool = True,
    allow_scenario_override: bool = False,
) -> dict:
    row = {
        "name": name,
        "display_name": name.replace("_", " ").title(),
        "description": f"Illustrative IDI {name.replace('_', ' ')}.",
        "kind": kind,
        "data_type": data_type,
        "unit": unit,
        "required": required,
        "source": source or {"type": kind, "column": name},
        "allow_scenario_override": allow_scenario_override,
    }
    if default_value is not None:
        row["default_value"] = default_value
    return row


VARIABLES = [
    _variable("claim_status", "input", data_type="string"),
    _variable("occupation_class", "input", data_type="string"),
    _variable("gender", "input", data_type="string", source={"type": "input", "column": "gender", "transform": "upper"}),
    *[_variable(name, "input") for name in (
        "monthly_benefit", "annual_premium", "elimination_months", "max_benefit_months",
        "claim_months_elapsed", "expense_per_policy_monthly", "waive_premium_on_disability",
        "discount_rate",
    )],
    _variable("projection_month", "context", unit="month", source={"type": "context", "field": "projection_month"}),
    _variable("policy_year", "context", unit="year", source={"type": "context", "field": "policy_year"}),
    _variable("attained_age", "context", unit="years", source={"type": "context", "field": "attained_age"}),
    _variable("incidence_multiplier", "manual", default_value=1.0, source={"type": "manual", "value": 1.0}, allow_scenario_override=True),
    _variable("q_mort_active_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": ACTIVE_MORTALITY_TABLE, "value_column": "rate",
        "key_map": {"attained_age": "attained_age", "gender": "gender"},
    }),
    _variable("q_inc_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": INCIDENCE_TABLE, "value_column": "rate",
        "key_map": {"attained_age": "attained_age", "gender": "gender", "occupation_class": "occupation_class"},
    }),
    _variable("q_lapse_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": ACTIVE_LAPSE_TABLE, "value_column": "rate",
        "key_map": {"policy_year": "policy_year"},
    }),
    _variable("q_term_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": DISABLED_TERMINATION_TABLE, "value_column": "rate",
        "key_map": {"attained_age": "attained_age", "gender": "gender"},
    }),
    _variable("q_mort_disabled_annual", "assumption", unit="probability", source={
        "type": "assumption", "table": DISABLED_MORTALITY_TABLE, "value_column": "rate",
        "key_map": {"attained_age": "attained_age", "gender": "gender"},
    }),
    _variable("new_claim_dlr_factor", "factor", unit="factor", source={
        "type": "factor", "table": NEW_CLAIM_DLR_TABLE, "value_column": "factor",
        "key_map": {"attained_age": "attained_age", "occupation_class": "occupation_class"},
    }),
    _variable("active_prob_previous", "prior_output", unit="probability", source={
        "type": "prior_output", "variable": "active_prob_close", "offset": 1, "initial_value": 0.0,
    }),
    _variable("disabled_prob_previous", "prior_output", unit="probability", source={
        "type": "prior_output", "variable": "disabled_prob_close", "offset": 1, "initial_value": 0.0,
    }),
    _variable("claim_elapsed_previous", "prior_output", unit="month", source={
        "type": "prior_output", "variable": "claim_elapsed_close", "offset": 1, "initial_value": 0.0,
    }),
]

_FORMULAS = [
    ("q_mort_active_m", "q_mort_active_monthly", ["q_mort_active_annual"]),
    ("q_inc_m", "incidence_probability", ["q_inc_annual", "incidence_multiplier"]),
    ("q_lapse_m", "q_lapse_monthly", ["q_lapse_annual"]),
    ("q_term_m", "q_term_monthly", ["q_term_annual"]),
    ("q_mort_disabled_m", "q_mort_disabled_monthly", ["q_mort_disabled_annual"]),
    ("active_prob_open", "active_probability_open", ["projection_month", "claim_status", "active_prob_previous"]),
    ("disabled_prob_open", "disabled_probability_open", ["projection_month", "claim_status", "disabled_prob_previous"]),
    ("claim_elapsed_open", "claim_elapsed_open", ["projection_month", "claim_status", "claim_months_elapsed", "claim_elapsed_previous"]),
    ("new_disabled", "new_disabled", ["active_prob_open", "q_inc_m"]),
    ("active_prob_close", "active_probability_close", ["active_prob_open", "q_mort_active_m", "q_lapse_m", "q_inc_m"]),
    ("disabled_surviving", "disabled_surviving", ["disabled_prob_open", "q_term_m", "q_mort_disabled_m"]),
    ("disabled_prob_close", "disabled_probability_close", ["disabled_surviving", "new_disabled"]),
    ("claim_elapsed_close", "claim_elapsed_close", ["claim_elapsed_open", "disabled_prob_open"]),
    ("expected_premium_inflow", "expected_premium_inflow", ["active_prob_close", "disabled_prob_close", "annual_premium", "waive_premium_on_disability"]),
    ("expected_claim_benefit_outgo", "expected_claim_benefit_outgo", ["claim_elapsed_close", "elimination_months", "max_benefit_months", "monthly_benefit", "disabled_surviving"]),
    ("expected_expense_outgo", "expected_expense_outgo", ["active_prob_close", "disabled_prob_close", "expense_per_policy_monthly"]),
    ("expected_new_claim_incurred", "expected_new_claim_incurred", ["new_disabled", "monthly_benefit", "new_claim_dlr_factor"]),
    ("net_liability_cash_flow", "net_liability_cash_flow", ["expected_claim_benefit_outgo", "expected_expense_outgo", "expected_premium_inflow", "expected_new_claim_incurred"]),
    ("discount_factor", "discount_factor", ["discount_rate", "projection_month"]),
    ("pv_net_cash_flow", "pv_net_cash_flow", ["net_liability_cash_flow", "discount_factor"]),
]

for output, _function, _dependencies in _FORMULAS:
    unit = (
        "probability" if output.startswith("q_") or "prob" in output or output in {"new_disabled", "disabled_surviving"}
        else "month" if output in {"claim_elapsed_close", "claim_elapsed_open"}
        else "factor" if output == "discount_factor"
        else "USD"
    )
    VARIABLES.append(_variable(output, "formula", unit=unit, source={"type": "formula"}))

VARIABLES.append(_variable("reserve", "valuation", unit="USD", source={
    "type": "valuation", "method": "illustrative_prospective_reserve",
    "cash_flow": "net_liability_cash_flow", "rate": "discount_rate",
    "consistency_sum_of": "pv_net_cash_flow",
}))

FORMULAS = [
    {
        "name": output,
        "output_variable": output,
        "function_ref": f"{FUNCTION_PREFIX}.{function}",
        "dependencies": dependencies,
        "unit": next(variable["unit"] for variable in VARIABLES if variable["name"] == output),
    }
    for output, function, dependencies in _FORMULAS
]

_OUTPUTS = [
    "active_prob_close", "disabled_prob_close", "expected_premium_inflow",
    "expected_claim_benefit_outgo", "expected_expense_outgo", "expected_new_claim_incurred",
    "net_liability_cash_flow", "discount_factor", "pv_net_cash_flow", "reserve",
]
PUBLISHED_OUTPUTS = [
    {
        "variable_name": name,
        "display_name": name.replace("_", " ").title(),
        "unit": next(variable["unit"] for variable in VARIABLES if variable["name"] == name),
        "dimension": "policy_month",
        "aggregation": "sum" if name.startswith("expected_") or name in {"net_liability_cash_flow", "pv_net_cash_flow"} else "end_of_period",
        "description": "Synthetic illustrative IDI output; actuarial approval pending.",
    }
    for name in _OUTPUTS
]

MODEL = {
    "name": "IDI Illustrative M7",
    "product_code": PRODUCT_CODE,
    "description": "Synthetic engineering model; not actuarially approved.",
    "version": {
        "version_label": "m7-v1", "block_name": "Synthetic IDI block",
        "profile_name": "Illustrative", "basis": "Illustrative",
        "methodology": "Expected multi-state disability projection",
        "notes": "ACT-P7-DEC-01 and ACT-P7-DEC-02 remain pending owner confirmation.",
        "configuration": {"timing_convention": "EOM", "is_synthetic": True, "is_production_approved": False},
    },
    "formula_group": {
        "name": "IDI M7", "description": "IDI shared-engine formulas",
        "lineage_state": "local", "version_label": "v1",
    },
}

MODEL_DEFINITION = {
    "model": MODEL,
    "variables": VARIABLES,
    "formulas": FORMULAS,
    "published_outputs": PUBLISHED_OUTPUTS,
}
