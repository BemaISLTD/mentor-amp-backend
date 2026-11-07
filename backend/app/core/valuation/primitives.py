"""Generic mathematical primitives for actuarial calculation kernels (Work Package M5E).

DISCLAIMER & GOVERNANCE SCOPE (Directive 7):
These functions are pure mathematical utilities and generic algorithmic building blocks.
They do NOT constitute an approved implementation or proof of compliance with:
- FASB LDTI (ASU 2018-12 / ASC 944);
- US GAAP DAC / VOBA amortization (ASC 340-40 / ASC 944);
- NAIC VM-21 (Requirements for Principle-Based Reserves for Variable Annuities);
- NAIC VM-22 (Statutory Maximum Valuation Interest Rates for Income Annuities).

Final regulatory and accounting reference implementations remain gated under ACT-DEC decisions
and require independent sign-off and validated benchmark datasets.
"""

import math
from typing import Sequence


def annual_to_monthly_discount_factor(annual_rate: float, month: int) -> float:
    """Compute discount factor v^t for monthly time step t given an annual effective rate i.
    
    Mathematical formula:
        v = (1 + i)^(-1/12)
        factor = v^month = (1 + i)^(-month / 12)
    """
    if annual_rate <= -1.0:
        raise ValueError(f"Annual interest rate must be > -100%, got {annual_rate}.")
    if month < 0:
        raise ValueError(f"Month must be non-negative, got {month}.")
    return math.pow(1.0 + annual_rate, -month / 12.0)


def curve_to_discount_factors(annual_spot_rates: Sequence[float]) -> list[float]:
    """Convert a sequence of annual spot rates for months t=0, 1, 2, ... into monthly discount factors."""
    factors = []
    for month, rate in enumerate(annual_spot_rates):
        factors.append(annual_to_monthly_discount_factor(rate, month))
    return factors


def discount_cash_flows(
    cash_flows: Sequence[float],
    discount_factors: Sequence[float],
) -> list[float]:
    """Compute period-by-period discounted cash flows: CF_t * v_t."""
    if len(cash_flows) != len(discount_factors):
        raise ValueError(
            f"Cash flows length ({len(cash_flows)}) must match discount factors length ({len(discount_factors)})."
        )
    return [cf * df for cf, df in zip(cash_flows, discount_factors)]


def present_value(
    cash_flows: Sequence[float],
    discount_factors: Sequence[float],
) -> float:
    """Compute present value summation: sum(CF_t * v_t)."""
    return sum(discount_cash_flows(cash_flows, discount_factors))


def solve_capped_ratio(
    pv_numerator: float,
    pv_denominator: float,
    cap_at_one: bool = True,
) -> tuple[float, bool, float]:
    """Solve for a capped ratio of present values (generic ratio kernel).
    
    Formula:
        Raw Ratio = pv_numerator / pv_denominator
        Capped Ratio = min(1.0, Raw Ratio) if cap_at_one else Raw Ratio
        Excess Liability Recognized = max(0.0, pv_numerator - 1.0 * pv_denominator)
    
    Returns:
        (capped_ratio, is_capped, excess_amount)
    """
    if pv_denominator <= 0.0:
        if pv_numerator > 0.0:
            return (1.0, True, pv_numerator)
        return (0.0, False, 0.0)

    raw_ratio = pv_numerator / pv_denominator

    if cap_at_one and raw_ratio > 1.0:
        excess = pv_numerator - (1.0 * pv_denominator)
        return (1.0, True, excess)

    return (raw_ratio, False, 0.0)


# Backwards compatibility alias for existing callers
solve_net_premium_ratio = solve_capped_ratio


def value_at_risk(path_losses: Sequence[float], alpha: float = 0.70) -> float:
    """Compute generic Value-at-Risk (VaR_alpha) across accumulated loss distribution.
    
    Losses are sorted ascending; VaR_alpha is the loss at the alpha percentile.
    """
    if not path_losses:
        raise ValueError("Cannot compute VaR on empty path sequence.")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"Alpha must be between 0 and 1, got {alpha}.")

    sorted_losses = sorted(path_losses)
    n = len(sorted_losses)
    idx = int(math.ceil(alpha * n)) - 1
    idx = max(0, min(n - 1, idx))
    return sorted_losses[idx]


def conditional_tail_expectation(path_losses: Sequence[float], alpha: float = 0.70) -> float:
    """Compute generic Conditional Tail Expectation (CTE_alpha) across path loss distribution.
    
    Mathematical average of the upper (1 - alpha) fraction of sorted losses.
    """
    if not path_losses:
        raise ValueError("Cannot compute CTE on empty path sequence.")
    if not (0.0 <= alpha < 1.0):
        raise ValueError(f"Alpha must be in [0, 1), got {alpha}.")

    sorted_losses = sorted(path_losses)
    n = len(sorted_losses)
    cutoff_index = int(math.floor(alpha * n))
    tail = sorted_losses[cutoff_index:]

    if not tail:
        return sorted_losses[-1]

    return sum(tail) / len(tail)


def calculate_proportional_amortization(
    initial_asset_balance: float,
    service_units_by_period: Sequence[float],
) -> list[float]:
    """Compute proportional constant-level amortization schedule across service units.
    
    Formula: Amortization_t = Initial_Balance * (Units_t / Sum(Units))
    """
    total_units = sum(service_units_by_period)
    if total_units <= 0.0 or initial_asset_balance <= 0.0:
        return [0.0] * len(service_units_by_period)

    return [initial_asset_balance * (u / total_units) for u in service_units_by_period]


# Backwards compatibility alias
constant_level_amortization = calculate_proportional_amortization
