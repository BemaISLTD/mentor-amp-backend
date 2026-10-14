"""Validation of a stored lookup table (assumption or factor) before it may be marked validated.

Checks exactly what the engine relies on: every row has the lookup keys and a numeric value,
key combinations are unique (exact-match lookups must be unambiguous), and values lie in the
declared range (for example 0 <= qx <= 1).
"""

from typing import Any


def validate_lookup_table(
    rows: list[dict[str, Any]],
    lookup_keys: list[str],
    value_column: str | None,
    value_range: tuple[float | None, float | None] = (None, None),
) -> list[dict[str, Any]]:
    """Return error dicts (empty list = valid)."""
    errors: list[dict[str, Any]] = []
    if not rows:
        return [{"type": "empty_table", "message": "The table has no rows."}]
    if not value_column:
        return [{"type": "missing_value_column", "message": "The table has no value column."}]
    seen: set[tuple] = set()
    low, high = value_range
    for index, row in enumerate(rows):
        missing = [column for column in [*lookup_keys, value_column] if row.get(column) in (None, "")]
        if missing:
            errors.append({"type": "missing_value", "row": index, "message": f"Row {index} is missing {missing}."})
            continue
        key = tuple(str(row[column]).strip().upper() for column in lookup_keys)
        if key in seen:
            errors.append({"type": "duplicate_key", "row": index, "message": f"Duplicate lookup key {key}."})
        seen.add(key)
        try:
            value = float(row[value_column])
        except (TypeError, ValueError):
            errors.append({"type": "invalid_number", "row": index,
                           "message": f"Row {index}: '{value_column}' is not a number."})
            continue
        if (low is not None and value < low) or (high is not None and value > high):
            errors.append({"type": "out_of_range", "row": index,
                           "message": f"Row {index}: {value_column}={value} is outside {value_range}."})
    return errors
