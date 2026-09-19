"""SPIA Lite product configuration — variables, formulas, and setup."""

import uuid

from sqlalchemy.orm import Session

from app.core.formula_engine.formulas import register_function
from app.core.formula_engine.registry import register as reg_formula
from app.core.variable_registry.registry import register as reg_var
from app.models.schemas import FormulaDefinition, VariableDefinition
from app.products.shared_modules.mortality import mortality_rate_lookup
from app.products.shared_modules.payment_stream import calculate_payment
from app.products.shared_modules.present_value import present_value
from app.products.shared_modules.survival_probability import survival_probability

# =============================================================================
# SPIA VARIABLES
# =============================================================================

SPIA_VARIABLES: list[VariableDefinition] = [
    VariableDefinition(
        id="spia_mortality_rate", name="mortality_rate", kind="assumption",
        data_type="number",
        source={"type": "assumption", "table_id": "mort_2024"},
        required=True, product_applicability=["SPIA"],
        description="Mortality rate from the assumption table",
    ),
    VariableDefinition(
        id="spia_base_age", name="issue_age", kind="input",
        data_type="number",
        source={"type": "input", "column_name": "issue_age"},
        required=True, product_applicability=["SPIA"],
        description="Policyholder's age at issue",
    ),
    VariableDefinition(
        id="spia_gender", name="gender", kind="input",
        data_type="string",
        source={"type": "input", "column_name": "gender"},
        required=True, product_applicability=["SPIA"],
        description="Policyholder's gender (M/F)",
    ),
    VariableDefinition(
        id="spia_premium", name="premium", kind="input",
        data_type="number",
        source={"type": "input", "column_name": "premium"},
        required=True, product_applicability=["SPIA"],
        description="Initial premium amount",
    ),
    VariableDefinition(
        id="spia_discount_rate", name="discount_rate", kind="manual",
        data_type="number",
        source={"type": "manual", "value": 0.05},
        required=True, product_applicability=["SPIA"],
        description="Annual discount rate for present value (5%)",
    ),
    # Output variables (computed by formulas)
    VariableDefinition(
        id="spia_surv_prob", name="survival_probability", kind="formula",
        data_type="number", required=False, product_applicability=["SPIA"],
        description="Probability of surviving to a given month",
    ),
    VariableDefinition(
        id="spia_expected_pmt", name="expected_payment", kind="formula",
        data_type="number", required=False, product_applicability=["SPIA"],
        description="Payment amount * survival probability",
    ),
    VariableDefinition(
        id="spia_pv", name="present_value", kind="formula",
        data_type="number", required=False, product_applicability=["SPIA"],
        description="Present value of expected payments from month 1 to current",
    ),
]

# =============================================================================
# SPIA FORMULA FUNCTIONS
# =============================================================================

def _spia_survival_probability(issue_age, gender, mortality_rate, duration):
    """Calculate survival probability given a resolved mortality rate."""
    # mortality_rate comes from the resolver as a row dict or single value
    # For simplicity in Phase 1: treat mortality_rate as the rate directly
    if isinstance(mortality_rate, dict):
        rate = float(mortality_rate.get("mortality_rate", 0))
    else:
        rate = float(mortality_rate) if mortality_rate else 0.0

    # Build a simple table for the survival probability function
    table = [{"age": int(issue_age), "gender": str(gender), "duration": int(duration), "mortality_rate": rate}]
    return survival_probability(int(issue_age), str(gender), int(duration), table)


def _spia_expected_payment(premium, survival_probability):
    """Expected payment = monthly payment * survival probability."""
    monthly_pmt = float(premium) / 10.0 / 12.0  # annual = premium/10, monthly = annual/12
    return monthly_pmt * float(survival_probability)


def _spia_present_value(expected_payment, discount_rate, duration):
    """Present value of a single expected payment."""
    from app.products.shared_modules.present_value import npv
    return npv(float(expected_payment), float(discount_rate), int(duration))


SPIA_FORMULA_FUNCTIONS = {
    "spia_survival_probability_v1": _spia_survival_probability,
    "spia_expected_payment_v1": _spia_expected_payment,
    "spia_present_value_v1": _spia_present_value,
}

# =============================================================================
# SPIA FORMULAS
# =============================================================================

SPIA_FORMULAS: list[FormulaDefinition] = [
    FormulaDefinition(
        id="f_spia_surv", name="SPIA Survival Probability", output_variable="survival_probability",
        function_ref="spia_survival_probability_v1",
        dependencies=["issue_age", "gender", "mortality_rate", "duration"],
        category="SPIA", product_applicability=["SPIA"],
    ),
    FormulaDefinition(
        id="f_spia_exp_pmt", name="SPIA Expected Payment", output_variable="expected_payment",
        function_ref="spia_expected_payment_v1",
        dependencies=["premium", "survival_probability"],
        category="SPIA", product_applicability=["SPIA"],
    ),
    FormulaDefinition(
        id="f_spia_pv", name="SPIA Present Value", output_variable="present_value",
        function_ref="spia_present_value_v1",
        dependencies=["expected_payment", "discount_rate", "duration"],
        category="SPIA", product_applicability=["SPIA"],
    ),
]


# =============================================================================
# SETUP FUNCTION
# =============================================================================

def setup_spia_product(db: Session) -> None:
    """Register all SPIA variables, formulas, and functions in the database."""
    print("Setting up SPIA Lite product...")

    # Register variables
    for var in SPIA_VARIABLES:
        try:
            reg_var(db, var)
        except Exception:
            db.rollback()
            # Variable may already exist — skip

    # Register formula functions
    for key, func in SPIA_FORMULA_FUNCTIONS.items():
        register_function(key, func)

    # Register formulas
    for formula in SPIA_FORMULAS:
        try:
            reg_formula(db, formula)
        except Exception:
            db.rollback()

    print("  SPIA Lite setup complete.")
