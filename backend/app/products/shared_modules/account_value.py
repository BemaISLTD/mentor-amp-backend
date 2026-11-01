"""Account value rollforward helpers."""

import math

def rollforward_account_value(open_value: float, premium: float = 0.0,
                              interest: float = 0.0, withdrawal: float = 0.0,
                              fee: float = 0.0) -> float:
    if not all(math.isfinite(v) for v in (open_value, premium, interest, withdrawal, fee)):
        raise ValueError("Account value inputs must be finite")
    if min(open_value, premium, withdrawal, fee) < 0:
        raise ValueError("Account value inputs cannot be negative")
    close = open_value + premium + interest - withdrawal - fee
    if close < -1e-12:
        raise ValueError("Account value cannot be negative")
    return max(0.0, close)
