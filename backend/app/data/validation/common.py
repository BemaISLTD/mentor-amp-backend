"""Shared validation helpers for all data types."""


def check_required_columns(
    columns: list[str], required: list[str]
) -> list[str]:
    """Return list of missing required columns."""
    missing = [col for col in required if col not in columns]
    return missing


def check_duplicate_ids(
    rows: list[dict], id_column: str
) -> list[dict]:
    """Return list of duplicate ID errors with the IDs that repeat."""
    seen: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        val = str(row.get(id_column, ""))
        if val not in seen:
            seen[val] = []
        seen[val].append(i + 1)  # 1-based row numbers

    errors = []
    for val, row_nums in seen.items():
        if len(row_nums) > 1:
            errors.append({
                "type": "duplicate_id",
                "message": f"Duplicate {id_column} '{val}' found on rows {row_nums}.",
                "column": id_column,
                "value": val,
                "rows": row_nums,
            })
    return errors


def check_data_types(
    rows: list[dict], column_types: dict[str, str]
) -> list[dict]:
    """Check that column values match expected types.
    
    column_types: {"column_name": "number|string|integer"}
    Returns list of error dicts with row number and column.
    """
    errors = []
    for i, row in enumerate(rows):
        row_num = i + 1
        for col, expected_type in column_types.items():
            if col not in row:
                continue
            raw = row[col]
            if raw is None or raw == "":
                continue  # skip empties — separate required check handles those

            if expected_type in ("number", "integer"):
                try:
                    float(str(raw))
                except (ValueError, TypeError):
                    errors.append({
                        "type": "type_mismatch",
                        "message": f"Expected {expected_type} for column '{col}', got '{raw}' on row {row_num}.",
                        "column": col,
                        "row": row_num,
                        "value": raw,
                    })
            elif expected_type == "string":
                # anything is valid as string
                pass
    return errors


def check_plausible_ranges(
    rows: list[dict], range_rules: dict[str, dict]
) -> list[dict]:
    """Check that numeric columns fall within plausible ranges.
    
    range_rules: {"column_name": {"min": ..., "max": ..., "allow_warnings": True}}
    Returns list of error/warning dicts.
    """
    issues = []
    for i, row in enumerate(rows):
        row_num = i + 1
        for col, rules in range_rules.items():
            if col not in row:
                continue
            raw = row[col]
            if raw is None or raw == "":
                continue
            try:
                val = float(str(raw))
            except (ValueError, TypeError):
                continue  # type check handles this

            min_val = rules.get("min")
            max_val = rules.get("max")
            severity = "warning" if rules.get("allow_warnings", True) else "error"

            if min_val is not None and val < min_val:
                issues.append({
                    "type": f"{severity}_range",
                    "message": f"Value {val} in column '{col}' is below minimum {min_val} on row {row_num}.",
                    "column": col,
                    "row": row_num,
                    "value": val,
                    "severity": severity,
                })
            if max_val is not None and val > max_val:
                issues.append({
                    "type": f"{severity}_range",
                    "message": f"Value {val} in column '{col}' is above maximum {max_val} on row {row_num}.",
                    "column": col,
                    "row": row_num,
                    "value": val,
                    "severity": severity,
                })
    return issues
