"""Dense float64 prospective-reserve & discounting model definition (M6-03 GPU workload).

ILLUSTRATIVE - NOT ACTUARIALLY APPROVED OR REGULATORY COMPLIANT.

Preserves the exact Mentor AMP valuation and discounting equations:
- cash_flow_t = monthly_cash_flow * (1 + cash_flow_growth_rate)^((t - 1) / 12)
- discount_factor_t = (1 + discount_rate_annual)^(-t / 12)
- pv_cash_flow_t = cash_flow_t * discount_factor_t
- reserve_H = 0; reserve_t = (reserve_{t+1} + cash_flow_{t+1}) * (1 + discount_rate_annual)^(-1/12)
"""

from __future__ import annotations

import math

from app.core.formula_engine.formulas import register_function
from app.products.inforce_registry import (
    COMMON_INFORCE_FIELDS,
    ProductInforceDefinition,
    register_inforce_product,
)

PRODUCT_CODE = "PROSPECTIVE_RESERVE_DISCOUNTING"
PRODUCT_NAME = "Prospective Reserve Discounting"
FUNCTION_PREFIX = "prospective_reserve"
IMPLEMENTATION_VERSION = "prospective-reserve-v1"
DEFAULT_DISCOUNT_RATE = 0.045
DEFAULT_GROWTH_RATE = 0.0


def scheduled_cash_flow(
    monthly_cash_flow: float,
    cash_flow_growth_rate: float,
    projection_month: float,
) -> float:
    """Compute month-end scheduled cash flow using annual compounding growth."""
    cf = float(monthly_cash_flow)
    g = float(cash_flow_growth_rate)
    if not (math.isfinite(cf) and math.isfinite(g)):
        raise ValueError("cash_flow inputs must be finite")
    if g <= -1.0:
        raise ValueError("cash_flow_growth_rate must be greater than -100%")
    m = float(projection_month)
    if m <= 1.0 or g == 0.0:
        return cf
    return cf * ((1.0 + g) ** ((m - 1.0) / 12.0))


def discount_factor(discount_rate_annual: float, projection_month: float) -> float:
    """Discount month-end cash flows at the effective annual scenario rate."""
    rate = float(discount_rate_annual)
    if not math.isfinite(rate):
        raise ValueError("discount_rate_annual must be finite")
    if rate <= -1.0:
        raise ValueError("discount_rate_annual must be greater than -100%")
    return (1.0 + rate) ** (-float(projection_month) / 12.0)


def pv_cash_flow(cash_flow: float, discount_factor: float) -> float:
    """Discount monthly cash flow back to valuation date."""
    cf = float(cash_flow)
    df = float(discount_factor)
    if not (math.isfinite(cf) and math.isfinite(df)):
        raise ValueError("pv_cash_flow inputs must be finite")
    return cf * df


FORMULA_DEFS = (
    ("cash_flow", scheduled_cash_flow),
    ("discount_factor", discount_factor),
    ("pv_cash_flow", pv_cash_flow),
)

FORMULAS = (
    {
        "name": "Scheduled Cash Flow",
        "output_variable": "cash_flow",
        "function_ref": f"{FUNCTION_PREFIX}:cash_flow",
        "dependencies": ["monthly_cash_flow", "cash_flow_growth_rate", "projection_month"],
        "expression_text": "monthly_cash_flow * (1 + cash_flow_growth_rate)^((projection_month - 1)/12)",
    },
    {
        "name": "Discount Factor",
        "output_variable": "discount_factor",
        "function_ref": f"{FUNCTION_PREFIX}:discount_factor",
        "dependencies": ["discount_rate_annual", "projection_month"],
        "expression_text": "(1 + discount_rate_annual)^(-projection_month/12)",
    },
    {
        "name": "PV Cash Flow",
        "output_variable": "pv_cash_flow",
        "function_ref": f"{FUNCTION_PREFIX}:pv_cash_flow",
        "dependencies": ["cash_flow", "discount_factor"],
        "expression_text": "cash_flow * discount_factor",
    },
)

VARIABLES = (
    {
        "name": "projection_month",
        "display_name": "Projection month",
        "description": "1-based projection month index.",
        "kind": "input",
        "data_type": "integer",
        "source": {"type": "context", "field": "projection_month"},
    },
    {
        "name": "monthly_cash_flow",
        "display_name": "Monthly cash flow",
        "description": "Base monthly contractual cash flow per policy.",
        "kind": "input",
        "data_type": "number",
        "unit": "USD",
        "source": {"type": "input", "column": "monthly_cash_flow"},
        "required": True,
    },
    {
        "name": "cash_flow_growth_rate",
        "display_name": "Cash-flow growth rate",
        "description": "Optional annual compounding cash-flow growth rate.",
        "kind": "input",
        "data_type": "number",
        "unit": "rate",
        "source": {"type": "input", "column": "cash_flow_growth_rate"},
        "default_value": DEFAULT_GROWTH_RATE,
        "required": False,
    },
    {
        "name": "discount_rate_annual",
        "display_name": "Annual valuation discount rate",
        "description": "Annual effective discount rate used for PV and reserve.",
        "kind": "input",
        "data_type": "number",
        "unit": "rate",
        "source": {"type": "manual", "value": DEFAULT_DISCOUNT_RATE},
        "default_value": DEFAULT_DISCOUNT_RATE,
        "allow_scenario_override": True,
    },
    {
        "name": "cash_flow",
        "display_name": "Monthly projected cash flow",
        "description": "Projected month-end cash flow.",
        "kind": "output",
        "data_type": "number",
        "unit": "USD",
        "source": {"type": "formula"},
    },
    {
        "name": "discount_factor",
        "display_name": "Monthly discount factor",
        "description": "Present-value discount factor for projection month t.",
        "kind": "output",
        "data_type": "number",
        "unit": "factor",
        "source": {"type": "formula"},
    },
    {
        "name": "pv_cash_flow",
        "display_name": "Present value of monthly cash flow",
        "description": "Discounted monthly cash flow at valuation date.",
        "kind": "output",
        "data_type": "number",
        "unit": "USD",
        "source": {"type": "formula"},
    },
    {
        "name": "reserve",
        "display_name": "Prospective reserve",
        "description": "Backward prospective present-value reserve at month t.",
        "kind": "output",
        "data_type": "number",
        "unit": "USD",
        "source": {
            "type": "valuation",
            "method": "illustrative_prospective_reserve",
            "cash_flow": "cash_flow",
            "rate": "discount_rate_annual",
            "consistency_sum_of": "pv_cash_flow",
        },
    },
)

INFORCE_DEFINITION = ProductInforceDefinition(
    product_code=PRODUCT_CODE,
    fields={
        "monthly_cash_flow": "number",
    },
    amount_fields=("monthly_cash_flow",),
    negative_allowed=True,
)

PUBLISHED_OUTPUTS = [
    {
        "variable_name": "cash_flow",
        "display_name": "Monthly projected cash flow",
        "unit": "USD",
        "dimension": "policy_month",
        "aggregation": "sum",
        "description": "Projected month-end cash flow.",
    },
    {
        "variable_name": "discount_factor",
        "display_name": "Monthly discount factor",
        "unit": "factor",
        "dimension": "policy_month",
        "aggregation": "mean",
        "description": "Monthly valuation discount factor.",
    },
    {
        "variable_name": "pv_cash_flow",
        "display_name": "Present value of monthly cash flow",
        "unit": "USD",
        "dimension": "policy_month",
        "aggregation": "sum",
        "description": "Present value of monthly cash flow.",
    },
    {
        "variable_name": "reserve",
        "display_name": "Prospective reserve",
        "unit": "USD",
        "dimension": "policy_month",
        "aggregation": "sum",
        "description": "Prospective reserve at each projection month.",
    },
]

MODEL = {
    "name": "Prospective Reserve Discounting — M6-03 Dense Kernel",
    "product_code": PRODUCT_CODE,
    "description": "Dense float64 prospective-reserve and cash-flow discounting model (CPU & CUDA GPU).",
    "version": {
        "version_label": "v1.0",
        "block_name": "Dense Float64 Block",
        "profile_name": "Illustrative",
        "basis": "ILLUSTRATIVE_PROSPECTIVE",
        "methodology": "deterministic_monthly_discounting",
        "notes": "Dense float64 prospective reserve reference & CUDA GPU kernel model.",
        "illustrative": True,
        "configuration": {
            "reference_only": True,
            "default_outputs": ["cash_flow", "discount_factor", "pv_cash_flow", "reserve"],
        },
    },
    "formula_group": {
        "name": "Prospective reserve discounting",
        "description": "Dense monthly discounting and backward prospective reserve.",
        "lineage_state": "local",
        "version_label": "v1",
    },
}


def register_functions() -> None:
    """Idempotently register all formulas and inforce schema definitions."""
    register_inforce_product(INFORCE_DEFINITION)
    expr_by_suffix = {
        item["output_variable"]: item["expression_text"]
        for item in FORMULAS
    }
    for suffix, fn in FORMULA_DEFS:
        register_function(
            f"{FUNCTION_PREFIX}:{suffix}",
            fn,
            expression_text=expr_by_suffix.get(suffix),
            explanation=fn.__doc__,
            illustrative=True,
            implementation_version=IMPLEMENTATION_VERSION,
        )
