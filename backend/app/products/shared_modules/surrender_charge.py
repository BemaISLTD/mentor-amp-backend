"""Surrender charge primitives."""

import math
from .option_budget import lookup_factor


def schedule_rate(rows, policy_year):
    return lookup_factor(rows, {"policy_year": policy_year}, "rate")

def surrender_charge(account_value: float, charge_rate: float) -> float:
    if not math.isfinite(account_value) or account_value < 0 or not 0 <= charge_rate <= 1:
        raise ValueError("Invalid surrender charge inputs")
    return account_value * charge_rate


def cash_surrender_value(account_value: float, charge_rate: float) -> float:
    return max(0.0, account_value - surrender_charge(account_value, charge_rate))
