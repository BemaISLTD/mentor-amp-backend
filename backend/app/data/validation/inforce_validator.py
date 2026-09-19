"""Validation rules for inforce (policy) data files."""

from app.data.validation.common import (
    check_data_types,
    check_duplicate_ids,
    check_plausible_ranges,
    check_required_columns,
)

REQUIRED_COLUMNS = ["policy_id"]

COLUMN_TYPES: dict[str, str] = {
    "policy_id": "string",
    "issue_age": "integer",
    "premium": "number",
    "account_value": "number",
    "gender": "string",
    "product_type": "string",
}

PLAUSIBLE_RANGES: dict[str, dict] = {
    "issue_age": {"min": 0, "max": 120, "allow_warnings": True},
    "premium": {"min": 0, "allow_warnings": True},
    "account_value": {"min": 0, "allow_warnings": True},
}


def validate_inforce(rows: list[dict]) -> list[dict]:
    """Run all validation checks on inforce data. Returns list of error dicts."""
    errors: list[dict] = []

    columns = list(rows[0].keys()) if rows else []

    # Required columns
    missing = check_required_columns(columns, REQUIRED_COLUMNS)
    for col in missing:
        errors.append({
            "type": "missing_column",
            "message": f"Required column '{col}' is missing from inforce file.",
            "column": col,
        })

    if missing:
        return errors

    # Duplicate policy IDs
    errors.extend(check_duplicate_ids(rows, "policy_id"))

    # Type checks — only check columns that exist
    available_types = {k: v for k, v in COLUMN_TYPES.items() if k in columns}
    if available_types:
        errors.extend(check_data_types(rows, available_types))

    # Plausible ranges
    available_ranges = {k: v for k, v in PLAUSIBLE_RANGES.items() if k in columns}
    if available_ranges:
        errors.extend(check_plausible_ranges(rows, available_ranges))

    return errors
