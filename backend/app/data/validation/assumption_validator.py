"""Validation rules for assumption table files."""

from app.data.validation.common import check_required_columns, check_plausible_ranges

REQUIRED_COLUMNS = ["table_name", "table_type"]

PLAUSIBLE_RANGES: dict[str, dict] = {
    "age": {"min": 0, "max": 120, "allow_warnings": True},
    "duration": {"min": 0, "max": 100, "allow_warnings": True},
    "mortality_rate": {"min": 0, "max": 1, "allow_warnings": False},
    "lapse_rate": {"min": 0, "max": 1, "allow_warnings": False},
}


def validate_assumptions(rows: list[dict]) -> list[dict]:
    """Run all validation checks on assumption data."""
    errors: list[dict] = []

    columns = list(rows[0].keys()) if rows else []

    missing = check_required_columns(columns, REQUIRED_COLUMNS)
    for col in missing:
        errors.append({
            "type": "missing_column",
            "message": f"Required column '{col}' is missing from assumption file.",
            "column": col,
        })

    if missing:
        return errors

    errors.extend(check_plausible_ranges(rows, PLAUSIBLE_RANGES))

    return errors
