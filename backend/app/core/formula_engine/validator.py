"""Validates formula declarations against the variable registry and function registry."""

from sqlalchemy.orm import Session

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.variable_registry.registry import get_by_name
from app.models.schemas import FormulaDefinition


def validate_formula_declaration(db: Session, formula: FormulaDefinition) -> list[str]:
    """Validate a single formula. Returns list of error messages."""
    errors: list[str] = []

    # Check function_ref exists
    if formula.function_ref not in FORMULA_FUNCTIONS:
        errors.append(
            f"function_ref '{formula.function_ref}' not found in FORMULA_FUNCTIONS. "
            f"Available: {list(FORMULA_FUNCTIONS.keys())}"
        )

    # Check output_variable exists in variable_registry (or can be created)
    output_var = get_by_name(db, formula.output_variable)
    if output_var is None:
        errors.append(
            f"output_variable '{formula.output_variable}' is not registered in variable_registry."
        )

    # Check all dependencies exist in variable_registry
    for dep in formula.dependencies:
        dep_var = get_by_name(db, dep)
        if dep_var is None:
            errors.append(
                f"dependency '{dep}' is not registered in variable_registry."
            )

    return errors


def validate_all(db: Session, formulas: list[FormulaDefinition]) -> dict[str, list[str]]:
    """Validate all formulas. Returns dict of formula_id → list of errors."""
    results: dict[str, list[str]] = {}
    for formula in formulas:
        errors = validate_formula_declaration(db, formula)
        if errors:
            results[formula.id] = errors
    return results
