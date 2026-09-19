"""Validation rules for factor table files."""

from app.data.validation.common import check_required_columns, check_plausible_ranges

REQUIRED_COLUMNS = ["table_name", "table_type"]

PLAUSIBLE_RANGES: dict[str, dict] = {
    "cap": {"min": 0, "max": 1, "allow_warnings": True},
    "participation_rate": {"min": 0, "max": 2, "allow_warnings": True},
    "spread": {"min": 0, "max": 0.5, "allow_warnings": True},
    "option_budget": {"min": 0, "allow_warnings": True},
    "duration": {"min": 0, "max": 50, "allow_warnings": True},
}


def validate_factors(rows: list[dict]) -> list[dict]:
    """Run all validation checks on factor data."""
    errors: list[dict] = []

    columns = list(rows[0].keys()) if rows else []

    missing = check_required_columns(columns, REQUIRED_COLUMNS)
    for col in missing:
        errors.append({
            "type": "missing_column",
            "message": f"Required column '{col}' is missing from factor file.",
            "column": col,
        })

    if missing:
        return errors

    errors.extend(check_plausible_ranges(rows, PLAUSIBLE_RANGES))

    return errors
