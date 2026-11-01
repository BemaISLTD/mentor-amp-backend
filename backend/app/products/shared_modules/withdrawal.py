"""Configurable segment withdrawal adjustments."""

import math


def apply_partial_withdrawal(base_balance: float, current_value: float,
                             withdrawal_amount: float,
                             mode: str = "pro_rata") -> tuple[float, float]:
    """Return balances after a pro-rata or dollar-for-dollar withdrawal."""
    if not all(math.isfinite(v) for v in (base_balance, current_value, withdrawal_amount)):
        raise ValueError("Withdrawal inputs must be finite")
    if base_balance < 0 or current_value < 0 or withdrawal_amount < 0:
        raise ValueError("Balances and withdrawal cannot be negative")
    if mode not in {"pro_rata", "dollar_for_dollar"}:
        raise ValueError(f"Unsupported withdrawal mode: {mode}")
    if withdrawal_amount == 0:
        return base_balance, current_value
    if withdrawal_amount > current_value:
        raise ValueError("Withdrawal exceeds available balance")
    if mode == "pro_rata":
        if current_value == 0:
            return 0.0, 0.0
        ratio = withdrawal_amount / current_value
        return base_balance * (1 - ratio), current_value - withdrawal_amount
    if mode == "dollar_for_dollar":
        return max(0.0, base_balance - withdrawal_amount), current_value - withdrawal_amount
    raise ValueError(f"Unsupported withdrawal mode: {mode}")
