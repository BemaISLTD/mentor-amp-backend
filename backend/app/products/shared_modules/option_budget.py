"""Pure option-budget arithmetic and unambiguous governed factor lookup."""

import math


def lookup_factor(rows, keys, value_column, *, range_column=None, point=None):
    """Exact keys plus optional half-open [lower, upper) range; never first-match."""
    matches = []
    if range_column and (point is None or not math.isfinite(point)):
        raise ValueError("A finite range point is required")
    for row in rows:
        if not all(row.get(key) == value for key, value in keys.items()):
            continue
        if range_column:
            lower, upper = row[f"{range_column}_lower"], row[f"{range_column}_upper"]
            if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
                raise ValueError("Invalid factor interval")
            if not lower <= point < upper:
                continue
        matches.append(row)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one factor row, found {len(matches)}")
    value = matches[0][value_column]
    if not math.isfinite(value):
        raise ValueError("Factor must be finite")
    return value

def derive_option_budget(earned_amount: float, spread: float = 0.0,
                         fee: float = 0.0, minimum: float = 0.0) -> float:
    if not all(math.isfinite(v) for v in (earned_amount, spread, fee, minimum)):
        raise ValueError("Option-budget inputs must be finite")
    if min(earned_amount, spread, fee, minimum) < 0:
        raise ValueError("Option-budget inputs cannot be negative")
    return max(minimum, earned_amount - spread - fee)
