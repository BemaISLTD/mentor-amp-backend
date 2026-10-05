"""SPIA Illustrative model — function registration and the declarative model definition.

ILLUSTRATIVE — NOT ACTUARIALLY APPROVED. These are standard textbook annuity mechanics written
by the development team so the engine can run end to end (Milestone 1). They will be replaced by
the Actuarial Product Owner's reference case (docs/build/03_QUESTIONS_AND_DOCUMENT_REQUESTS_FOR_
PRODUCT_OWNER.md, Q-SPIA-1..6). Contract reference: docs/build/02_BACKEND_FRONTEND_CONTRACT.md §F.5.

Timing convention: monthly steps; the annuitant must survive to the end of month t to receive
that month's payment (annuity-immediate, payable monthly in arrears). t = 0 is the valuation date.

This module has two parts:
1. ``register_functions()`` — code: registers the pure formula functions (called at startup).
2. ``MODEL_DEFINITION`` — data: variables, formulas, groups and published outputs. The seed
   script writes it to the database; the engine reads it back from the database at run time.
"""

import math

from app.core.formula_engine.formulas import register_function

PRODUCT_CODE = "SPIA"
PRODUCT_NAME = "Single Premium Immediate Annuity"
FUNCTION_PREFIX = "spia_illustrative"
MORTALITY_TABLE_NAME = "SYNTH_MORT_GOMPERTZ_2026"
MORTALITY_TABLE_MIN_AGE = 40
# Terminal age: qx = 1 from 120, so survival is 0 beyond it. Rows continue to 150 (qx = 1) so a
# long horizon never looks up a missing age — missing rows fail loudly by design (contract §F.3).
MORTALITY_TABLE_TERMINAL_AGE = 120
MORTALITY_TABLE_MAX_AGE = 150
DEFAULT_DISCOUNT_RATE = 0.045
# Label of the registered implementations below (their code fingerprint is computed separately).
IMPLEMENTATION_VERSION = "spia-illustrative-m1"


# =============================================================================
# 1. PURE FORMULA FUNCTIONS
# =============================================================================

def q_monthly(mortality_rate_annual: float) -> float:
    """Monthly mortality from annual q, assuming a constant force of mortality within the year."""
    return 1.0 - (1.0 - mortality_rate_annual) ** (1.0 / 12.0)


def survival_probability(survival_prev: float, q_monthly: float) -> float:
    """Probability of being alive at the end of this month."""
    return survival_prev * (1.0 - q_monthly)


def expected_payment(monthly_payment: float, survival_probability: float) -> float:
    """Payment made at month end if alive, weighted by the probability of survival."""
    return monthly_payment * survival_probability


def discount_factor(discount_rate_annual: float, projection_month: float) -> float:
    """Discount factor from the end of this month back to the valuation date."""
    return (1.0 + discount_rate_annual) ** (-projection_month / 12.0)


def pv_expected_payment(expected_payment: float, discount_factor: float) -> float:
    """Expected payment discounted to the valuation date."""
    return expected_payment * discount_factor


_FUNCTIONS = [
    (
        "q_monthly",
        q_monthly,
        "q_monthly = 1 − (1 − mortality_rate_annual)^(1/12)",
        "Converts the annual mortality rate into a monthly rate, assuming the force of mortality "
        "is constant within the year.",
    ),
    (
        "survival_probability",
        survival_probability,
        "survival_probability = survival_prev × (1 − q_monthly)",
        "The probability of being alive at the end of this month: survival at the end of the "
        "previous month times the probability of surviving this month.",
    ),
    (
        "expected_payment",
        expected_payment,
        "expected_payment = monthly_payment × survival_probability",
        "The monthly payment is made at the end of the month only if the annuitant is alive, so "
        "the expected payment is the payment times the probability of survival.",
    ),
    (
        "discount_factor",
        discount_factor,
        "discount_factor = (1 + discount_rate_annual)^(−projection_month / 12)",
        "Discounts a payment made at the end of this month back to the valuation date at the "
        "annual discount rate.",
    ),
    (
        "pv_expected_payment",
        pv_expected_payment,
        "pv_expected_payment = expected_payment × discount_factor",
        "The expected payment for the month, discounted to the valuation date.",
    ),
]


def function_ref(name: str) -> str:
    return f"{FUNCTION_PREFIX}.{name}"


def register_functions() -> None:
    """Register every SPIA illustrative formula function (idempotent)."""
    for name, func, expression, explanation in _FUNCTIONS:
        register_function(
            function_ref(name),
            func,
            expression_text=expression,
            explanation=explanation,
            illustrative=True,
            implementation_version=IMPLEMENTATION_VERSION,
        )


# =============================================================================
# 2. DECLARATIVE MODEL DEFINITION (written to the database by the seed script)
# =============================================================================

VARIABLES: list[dict] = [
    {
        "name": "gender", "display_name": "Gender", "kind": "input", "data_type": "string",
        "unit": None, "required": True,
        "source": {"type": "input", "column": "gender", "transform": "upper"},
        "description": "Annuitant gender from the inforce file (M or F).",
    },
    {
        "name": "monthly_payment", "display_name": "Monthly payment", "kind": "input",
        "data_type": "number", "unit": "USD", "required": True,
        "source": {"type": "input", "column": "monthly_payment"},
        "description": "Monthly annuity payment from the inforce file.",
    },
    {
        "name": "attained_age", "display_name": "Attained age", "kind": "context",
        "data_type": "number", "unit": "years", "required": True,
        "source": {"type": "context", "field": "attained_age"},
        "description": "Age during the projection month: issue_age + completed policy years.",
    },
    {
        "name": "projection_month", "display_name": "Projection month", "kind": "context",
        "data_type": "number", "unit": "month", "required": True,
        "source": {"type": "context", "field": "projection_month"},
        "description": "Months after the valuation date (1 = first month).",
    },
    {
        "name": "mortality_rate_annual", "display_name": "Annual mortality rate (qx)",
        "kind": "assumption", "data_type": "number", "unit": "probability", "required": True,
        "source": {
            "type": "assumption",
            "table": MORTALITY_TABLE_NAME,
            "value_column": "qx",
            "key_map": {"age": "attained_age", "gender": "gender"},
        },
        "description": "Annual probability of death at the attained age (SYNTHETIC table).",
    },
    {
        "name": "discount_rate_annual", "display_name": "Annual discount rate", "kind": "manual",
        "data_type": "number", "unit": "rate", "required": True,
        "default_value": DEFAULT_DISCOUNT_RATE,
        "source": {"type": "manual", "value": DEFAULT_DISCOUNT_RATE},
        # The only variable the demo scenarios override (Low Interest Rate); explicitly allowed.
        "allow_scenario_override": True,
        "description": "Flat annual discount rate. Scenarios may override it.",
    },
    {
        "name": "survival_prev", "display_name": "Survival at previous month end",
        "kind": "prior_output", "data_type": "number", "unit": "probability", "required": True,
        "source": {
            "type": "prior_output", "variable": "survival_probability", "offset": 1,
            "initial_value": 1.0,
        },
        "description": "survival_probability of the previous month; 1.0 at the valuation date.",
    },
    {
        "name": "q_monthly", "display_name": "Monthly mortality rate", "kind": "formula",
        "data_type": "number", "unit": "probability", "required": False,
        "source": {"type": "formula"},
        "description": "Monthly probability of death.",
    },
    {
        "name": "survival_probability", "display_name": "Survival probability", "kind": "formula",
        "data_type": "number", "unit": "probability", "required": False,
        "source": {"type": "formula", "non_increasing": True},
        "description": "Probability of being alive at the end of the month.",
    },
    {
        "name": "expected_payment", "display_name": "Expected payment", "kind": "formula",
        "data_type": "number", "unit": "USD", "required": False,
        "source": {"type": "formula"},
        "description": "Monthly payment weighted by survival.",
    },
    {
        "name": "discount_factor", "display_name": "Discount factor", "kind": "formula",
        "data_type": "number", "unit": "factor", "required": False,
        "source": {"type": "formula"},
        "description": "Discount factor to the valuation date.",
    },
    {
        "name": "pv_expected_payment", "display_name": "PV of expected payment", "kind": "formula",
        "data_type": "number", "unit": "USD", "required": False,
        "source": {"type": "formula"},
        "description": "Expected payment discounted to the valuation date.",
    },
    {
        "name": "reserve", "display_name": "Reserve (PV of remaining expected payments)",
        "kind": "output", "data_type": "number", "unit": "USD", "required": False,
        "source": {
            "type": "valuation",
            "method": "illustrative_prospective_reserve",
            "cash_flow": "expected_payment",
            "rate": "discount_rate_annual",
            "consistency_sum_of": "pv_expected_payment",
        },
        "description": "Illustrative prospective reserve at the end of each month.",
    },
]

FORMULAS: list[dict] = [
    {"name": "Monthly mortality", "output_variable": "q_monthly",
     "dependencies": ["mortality_rate_annual"], "unit": "probability"},
    {"name": "Survival probability", "output_variable": "survival_probability",
     "dependencies": ["survival_prev", "q_monthly"], "unit": "probability"},
    {"name": "Expected payment", "output_variable": "expected_payment",
     "dependencies": ["monthly_payment", "survival_probability"], "unit": "USD"},
    {"name": "Discount factor", "output_variable": "discount_factor",
     "dependencies": ["discount_rate_annual", "projection_month"], "unit": "factor"},
    {"name": "PV of expected payment", "output_variable": "pv_expected_payment",
     "dependencies": ["expected_payment", "discount_factor"], "unit": "USD"},
]
for _formula in FORMULAS:
    _formula["function_ref"] = function_ref(_formula["output_variable"])

PUBLISHED_OUTPUTS: list[dict] = [
    {"variable_name": "reserve", "display_name": "Reserve (PV of remaining expected payments)",
     "unit": "USD", "dimension": "Policy × scenario × projection month",
     "aggregation": "end_of_period",
     "description": "Illustrative prospective reserve at the end of each month."},
    {"variable_name": "expected_payment", "display_name": "Expected payment", "unit": "USD",
     "dimension": "Policy × scenario × projection month", "aggregation": "sum",
     "description": "Monthly payment × probability of survival."},
    {"variable_name": "pv_expected_payment", "display_name": "PV of expected payment",
     "unit": "USD", "dimension": "Policy × scenario × projection month", "aggregation": "sum",
     "description": "Expected payment discounted to the valuation date."},
    {"variable_name": "survival_probability", "display_name": "Survival probability",
     "unit": "probability", "dimension": "Policy × scenario × projection month",
     "aggregation": "sum",
     "description": "Probability the annuitant is alive at month end. Summed across policies = "
                    "expected lives in force."},
]

MODEL = {
    "name": "SPIA Illustrative Model",
    "product_code": PRODUCT_CODE,
    "description": "Illustrative SPIA reserve model: monthly survival, expected payments and a "
                   "prospective reserve. Not actuarially approved.",
    "version": {
        "version_label": "v0.1",
        "block_name": "Synthetic SPIA Block",
        "profile_name": "Illustrative",
        "basis": "Illustrative",
        "methodology": "Illustrative prospective reserve",
        "notes": "First illustrative version for Milestone 1. Replace with the approved "
                 "reference case (Q-SPIA-1..6).",
    },
    "formula_group": {
        "name": "SPIA core (illustrative)",
        "description": "Survival, expected payment and discounting.",
        "lineage_state": "local",
        "version_label": "v1",
    },
}

MODEL_DEFINITION = {
    "model": MODEL,
    "variables": VARIABLES,
    "formulas": FORMULAS,
    "published_outputs": PUBLISHED_OUTPUTS,
}


def gompertz_qx(age: int, gender: str) -> float:
    """Synthetic Gompertz mortality (NOT a published table): q = 1 − exp(−B·c^x·(c−1)/ln c)."""
    if age >= MORTALITY_TABLE_TERMINAL_AGE:
        return 1.0
    c = 1.10
    b = 3.0e-5 if gender.upper() == "M" else 2.0e-5
    q = 1.0 - math.exp(-b * c**age * (c - 1.0) / math.log(c))
    return round(min(q, 1.0), 6)
