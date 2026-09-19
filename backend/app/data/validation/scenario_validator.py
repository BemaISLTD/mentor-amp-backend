"""Validation rules for scenario override files."""

from app.data.validation.common import check_required_columns

REQUIRED_COLUMNS = ["scenario_name", "target_variable", "operation", "value"]

VALID_OPERATIONS = {"set", "add", "subtract", "multiply", "percent_change"}


def validate_scenarios(rows: list[dict]) -> list[dict]:
    """Run all validation checks on scenario data."""
    errors: list[dict] = []

    columns = list(rows[0].keys()) if rows else []

    missing = check_required_columns(columns, REQUIRED_COLUMNS)
    for col in missing:
        errors.append({
            "type": "missing_column",
            "message": f"Required column '{col}' is missing from scenario file.",
            "column": col,
        })

    if missing:
        return errors

    for i, row in enumerate(rows):
        row_num = i + 1
        op = str(row.get("operation", ""))
        if op and op not in VALID_OPERATIONS:
            errors.append({
                "type": "invalid_operation",
                "message": f"Invalid operation '{op}' on row {row_num}. Must be one of: {', '.join(sorted(VALID_OPERATIONS))}.",
                "column": "operation",
                "row": row_num,
                "value": op,
            })

    return errors
