"""Fixed-account crediting primitives."""

import math

def fixed_crediting(account_value: float, annual_rate: float, months: int = 1) -> float:
    if (not all(math.isfinite(v) for v in (account_value, annual_rate, months))
            or account_value < 0 or annual_rate < -1 or months < 0):
        raise ValueError("Invalid fixed-crediting inputs")
    return account_value * ((1 + annual_rate) ** (months / 12) - 1)


def fixed_account_rollforward(account_value: float, premium: float = 0.0,
                              withdrawal: float = 0.0, annual_rate: float = 0.0,
                              months: int = 1) -> float:
    balance = account_value + premium
    if not all(math.isfinite(v) for v in (account_value, premium, withdrawal)) or min(account_value, premium) < 0:
        raise ValueError("Invalid fixed-account balances")
    available = balance + fixed_crediting(balance, annual_rate, months)
    if withdrawal < 0 or withdrawal > available:
        raise ValueError("Withdrawal exceeds fixed account balance")
    return available - withdrawal
