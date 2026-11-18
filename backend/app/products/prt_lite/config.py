"""Illustrative PRT vertical executed exclusively by the shared projection engine."""

from __future__ import annotations

import math

from app.core.formula_engine.formulas import register_function
from app.products.shared_modules.prt_benefits import (
    non_termination_step,
    prt_escalated_payment,
    prt_monthly_benefit_factor,
)

PRODUCT_CODE = "PRT"
PRODUCT_NAME = "Pension Risk Transfer (Illustrative)"
FUNCTION_PREFIX = "prt_illustrative"
IMPLEMENTATION_VERSION = "prt-illustrative-m7-v1"
PRIMARY_MORTALITY_TABLE = "PRT_PRIMARY_MORTALITY"
SPOUSE_MORTALITY_TABLE = "PRT_SPOUSE_MORTALITY"
OPTIONAL_TERMINATION_TABLE = "PRT_OPTIONAL_TERMINATION"


def probability_from_annual(annual_rate: float) -> float:
    rate = float(annual_rate)
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        raise ValueError("Annual probability must be finite and in [0, 1].")
    return 1.0 - (1.0 - rate) ** (1.0 / 12.0)


def primary_mortality_monthly(q_mort_primary_annual: float) -> float:
    return probability_from_annual(q_mort_primary_annual)


def spouse_mortality_monthly(q_mort_spouse_annual: float) -> float:
    return probability_from_annual(q_mort_spouse_annual)


def optional_termination_monthly(
    participant_status: str,
    projection_month: float,
    deferral_months: float,
    q_term_optional_annual: float,
) -> float:
    if participant_status == "deferred" and int(projection_month) <= int(deferral_months):
        return probability_from_annual(q_term_optional_annual)
    return 0.0


def annuitant_survival_open(projection_month: float, annuitant_survival_previous: float) -> float:
    return 1.0 if int(projection_month) == 1 else float(annuitant_survival_previous)


def spouse_survival_open(projection_month: float, spouse_survival_previous: float) -> float:
    return 1.0 if int(projection_month) == 1 else float(spouse_survival_previous)


def non_termination_survival_open(
    projection_month: float, non_termination_survival_previous: float
) -> float:
    return 1.0 if int(projection_month) == 1 else float(non_termination_survival_previous)


def annuitant_survival_close(
    annuitant_survival_open: float, q_mort_primary_m: float
) -> float:
    return float(annuitant_survival_open) * (1.0 - float(q_mort_primary_m))


def spouse_survival_close(
    spouse_survival_open: float, q_mort_spouse_m: float
) -> float:
    return float(spouse_survival_open) * (1.0 - float(q_mort_spouse_m))


def non_termination_survival_close(
    non_termination_survival_open: float, q_term_optional_m: float
) -> float:
    return non_termination_step(non_termination_survival_open, q_term_optional_m)


def scheduled_benefit_payment(
    monthly_benefit: float, policy_year: float, cola_rate: float, cola_cap: float
) -> float:
    return prt_escalated_payment(monthly_benefit, int(policy_year), cola_rate, cola_cap)


def payable_benefit_factor(
    participant_status: str,
    projection_month: float,
    deferral_months: float,
    certain_months: float,
    form_of_annuity: str,
    joint_survivor_pct: float,
    annuitant_survival_close: float,
    non_termination_survival_close: float,
    spouse_survival_close: float,
) -> float:
    return prt_monthly_benefit_factor(
        participant_status=participant_status,
        projection_month=int(projection_month),
        deferral_months=int(deferral_months),
        certain_months=int(certain_months),
        form_of_annuity=form_of_annuity,
        joint_survivor_pct=joint_survivor_pct,
        annuitant_survival_prob=annuitant_survival_close,
        non_termination_survival_prob=non_termination_survival_close,
        spouse_survival_prob=spouse_survival_close if form_of_annuity == "joint_survivor" else None,
    )


def expected_benefit_payment(
    scheduled_benefit_payment: float, payable_benefit_factor: float
) -> float:
    return float(scheduled_benefit_payment) * float(payable_benefit_factor)


def expected_admin_expense(
    expected_benefit_payment: float, expense_pct_of_benefit: float
) -> float:
    expense = float(expense_pct_of_benefit)
    if not math.isfinite(expense) or expense < 0.0:
        raise ValueError("expense_pct_of_benefit must be finite and non-negative.")
    return float(expected_benefit_payment) * expense


def total_prt_outflow(expected_benefit_payment: float, expected_admin_expense: float) -> float:
    return float(expected_benefit_payment) + float(expected_admin_expense)


def discount_factor(discount_rate: float, projection_month: float) -> float:
    rate = float(discount_rate)
    if not math.isfinite(rate) or rate <= -1.0:
        raise ValueError("discount_rate must be finite and greater than -1.")
    return (1.0 + rate) ** (-float(projection_month) / 12.0)


def pv_benefit_payment(total_prt_outflow: float, discount_factor: float) -> float:
    return float(total_prt_outflow) * float(discount_factor)


_FUNCTIONS = {
    name: value for name, value in globals().copy().items()
    if name in {
        "primary_mortality_monthly", "spouse_mortality_monthly",
        "optional_termination_monthly",
        "annuitant_survival_open", "spouse_survival_open",
        "non_termination_survival_open", "annuitant_survival_close", "spouse_survival_close",
        "non_termination_survival_close", "scheduled_benefit_payment",
        "payable_benefit_factor", "expected_benefit_payment", "expected_admin_expense",
        "total_prt_outflow", "discount_factor", "pv_benefit_payment",
    }
}


def register_functions() -> None:
    for name, function in _FUNCTIONS.items():
        register_function(
            f"{FUNCTION_PREFIX}.{name}", function, expression_text=name,
            explanation="Illustrative PRT shared-engine calculation; ACT-P7-DEC-05 pending.",
            illustrative=True, implementation_version=IMPLEMENTATION_VERSION,
        )


def _variable(name, kind, *, data_type="number", source=None, default=None,
              required=True, override=False):
    row = {
        "name": name, "display_name": name.replace("_", " ").title(),
        "description": f"Illustrative PRT {name}.", "kind": kind,
        "data_type": data_type, "unit": None, "required": required,
        "source": source or {"type": kind, "column": name},
        "allow_scenario_override": override,
    }
    if default is not None:
        row["default_value"] = default
    return row


VARIABLES = [
    *[_variable(name, "input", data_type="string") for name in (
        "participant_status", "form_of_annuity", "gender",
    )],
    _variable("spouse_gender", "input", data_type="string", required=False, default="NOT_APPLICABLE"),
    *[_variable(name, "input") for name in (
        "monthly_benefit", "deferral_months", "certain_months", "joint_survivor_pct",
        "cola_rate", "cola_cap", "expense_pct_of_benefit",
    )],
    _variable("discount_rate", "input", override=True),
    _variable("spouse_age", "input", required=False, default=-1.0),
    _variable(
        "spouse_attained_age", "context", required=False, default=-1.0,
        source={"type": "context", "field": "attained_age", "age_column": "spouse_age"},
    ),
    _variable("projection_month", "context", source={"type": "context", "field": "projection_month"}),
    _variable("policy_year", "context", source={"type": "context", "field": "policy_year"}),
    _variable("attained_age", "context", source={"type": "context", "field": "attained_age"}),
    _variable("q_mort_primary_annual", "assumption", source={
        "type": "assumption", "table": PRIMARY_MORTALITY_TABLE, "value_column": "rate",
        "key_map": {"attained_age": "attained_age", "gender": "gender"},
    }),
    # This is an exact conditional key, not a row-0 fallback: formulas emit explicit
    # non-joint sentinels, while joint rows must match their spouse attributes.
    _variable("q_mort_spouse_annual", "assumption", source={
        "type": "assumption", "table": SPOUSE_MORTALITY_TABLE, "value_column": "rate",
        "key_map": {"form_of_annuity": "form_of_annuity", "spouse_age": "spouse_attained_age", "spouse_gender": "spouse_gender"},
    }),
    _variable("q_term_optional_annual", "assumption", source={
        "type": "assumption", "table": OPTIONAL_TERMINATION_TABLE, "value_column": "rate",
        "key_map": {"policy_year": "policy_year"},
    }),
]
for name, output in (
    ("annuitant_survival_previous", "annuitant_survival_close"),
    ("spouse_survival_previous", "spouse_survival_close"),
    ("non_termination_survival_previous", "non_termination_survival_close"),
):
    VARIABLES.append(_variable(name, "prior_output", source={
        "type": "prior_output", "variable": output, "offset": 1, "initial_value": 1.0,
    }))

_FORMULAS = [
    ("q_mort_primary_m", "primary_mortality_monthly", ["q_mort_primary_annual"]),
    ("q_mort_spouse_m", "spouse_mortality_monthly", ["q_mort_spouse_annual"]),
    ("q_term_optional_m", "optional_termination_monthly", ["participant_status", "projection_month", "deferral_months", "q_term_optional_annual"]),
    ("annuitant_survival_open", "annuitant_survival_open", ["projection_month", "annuitant_survival_previous"]),
    ("spouse_survival_open", "spouse_survival_open", ["projection_month", "spouse_survival_previous"]),
    ("non_termination_survival_open", "non_termination_survival_open", ["projection_month", "non_termination_survival_previous"]),
    ("annuitant_survival_close", "annuitant_survival_close", ["annuitant_survival_open", "q_mort_primary_m"]),
    ("spouse_survival_close", "spouse_survival_close", ["spouse_survival_open", "q_mort_spouse_m"]),
    ("non_termination_survival_close", "non_termination_survival_close", ["non_termination_survival_open", "q_term_optional_m"]),
    ("scheduled_benefit_payment", "scheduled_benefit_payment", ["monthly_benefit", "policy_year", "cola_rate", "cola_cap"]),
    ("payable_benefit_factor", "payable_benefit_factor", ["participant_status", "projection_month", "deferral_months", "certain_months", "form_of_annuity", "joint_survivor_pct", "annuitant_survival_close", "non_termination_survival_close", "spouse_survival_close"]),
    ("expected_benefit_payment", "expected_benefit_payment", ["scheduled_benefit_payment", "payable_benefit_factor"]),
    ("expected_admin_expense", "expected_admin_expense", ["expected_benefit_payment", "expense_pct_of_benefit"]),
    ("total_prt_outflow", "total_prt_outflow", ["expected_benefit_payment", "expected_admin_expense"]),
    ("discount_factor", "discount_factor", ["discount_rate", "projection_month"]),
    ("pv_benefit_payment", "pv_benefit_payment", ["total_prt_outflow", "discount_factor"]),
]
for output, _function, _deps in _FORMULAS:
    VARIABLES.append(_variable(output, "formula", source={"type": "formula"}))
VARIABLES.append(_variable("reserve", "valuation", source={
    "type": "valuation", "method": "illustrative_prospective_reserve",
    "cash_flow": "total_prt_outflow", "rate": "discount_rate",
    "consistency_sum_of": "pv_benefit_payment",
}))
FORMULAS = [{
    "name": output, "output_variable": output,
    "function_ref": f"{FUNCTION_PREFIX}.{function}", "dependencies": deps, "unit": None,
} for output, function, deps in _FORMULAS]

_OUTPUTS = [
    "annuitant_survival_close", "non_termination_survival_close", "spouse_survival_close",
    "scheduled_benefit_payment", "payable_benefit_factor", "expected_benefit_payment",
    "expected_admin_expense", "total_prt_outflow", "discount_factor",
    "pv_benefit_payment", "reserve",
]
PUBLISHED_OUTPUTS = [{
    "variable_name": name, "display_name": name.replace("_", " ").title(), "unit": None,
    "dimension": "policy_month",
    "aggregation": "sum" if name in {"expected_benefit_payment", "expected_admin_expense", "total_prt_outflow", "pv_benefit_payment"} else "end_of_period",
    "description": "Synthetic illustrative PRT output; owner confirmation pending.",
} for name in _OUTPUTS]

MODEL = {
    "name": "PRT Illustrative M7", "product_code": PRODUCT_CODE,
    "description": "Synthetic engineering model; not actuarially approved.",
    "version": {
        "version_label": "m7-v1", "block_name": "Synthetic PRT block",
        "profile_name": "Illustrative", "basis": "Illustrative",
        "methodology": "Expected pension benefit projection",
        "notes": "ACT-P7-DEC-05 and ACT-DEC-03 remain open.",
        "configuration": {"timing_convention": "EOM", "is_synthetic": True, "is_production_approved": False},
    },
    "formula_group": {"name": "PRT M7", "description": "PRT shared-engine formulas", "lineage_state": "local", "version_label": "v1"},
}
MODEL_DEFINITION = {"model": MODEL, "variables": VARIABLES, "formulas": FORMULAS, "published_outputs": PUBLISHED_OUTPUTS}
