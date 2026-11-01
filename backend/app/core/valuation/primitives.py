"""Pure mathematical primitives for actuarial valuation (Work Package M5E Foundation).

Reusable, deterministic mathematical kernels:
1. Discounting & present value summation
2. LDTI Net Premium Ratio (NPR) solver with mandatory 100% cap
3. Tail-risk metrics: Value-at-Risk (VaR) and Conditional Tail Expectation (CTE)
4. Constant-level amortization (DAC/VOBA) without interest accretion
"""

import math
from typing import Sequence


def annual_to_monthly_discount_factor(annual_rate: float, month: int) -> float:
    """Compute discount factor v^t for monthly time step t given an annual effective rate i.
    
    v = (1 + i)^(-1/12)
    factor = v^month = (1 + i)^(-month / 12)
    """
    if annual_rate <= -1.0:
        raise ValueError(f"Annual interest rate must be > -100%, got {annual_rate}.")
    if month < 0:
        raise ValueError(f"Month must be non-negative, got {month}.")
    return math.pow(1.0 + annual_rate, -month / 12.0)


def curve_to_discount_factors(annual_spot_rates: Sequence[float]) -> list[float]:
    """Convert a sequence of annual spot rates for months t=0, 1, 2, ... into discount factors."""
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


def solve_net_premium_ratio(
    pv_future_benefits_and_expenses: float,
    pv_future_gross_premiums: float,
    cap_at_one: bool = True,
) -> tuple[float, bool, float]:
    """Solve for the LDTI Net Premium Ratio (NPR).
    
    Formula:
        Raw NPR = PV(Benefits + Expenses) / PV(Gross Premiums)
        Capped NPR = min(1.0, Raw NPR) if cap_at_one else Raw NPR
        Immediate Loss Recognized = max(0.0, PV(Benefits + Expenses) - 1.0 * PV(Gross Premiums))
    
    Returns:
        (capped_npr, is_capped, immediate_loss)
    """
    if pv_future_gross_premiums <= 0.0:
        if pv_future_benefits_and_expenses > 0.0:
            # When no future premiums exist, full benefits are liability (100% loss)
            return (1.0, True, pv_future_benefits_and_expenses)
        return (0.0, False, 0.0)

    raw_npr = pv_future_benefits_and_expenses / pv_future_gross_premiums

    if cap_at_one and raw_npr > 1.0:
        immediate_loss = pv_future_benefits_and_expenses - (1.0 * pv_future_gross_premiums)
        return (1.0, True, immediate_loss)

    return (raw_npr, False, 0.0)


def value_at_risk(path_losses: Sequence[float], alpha: float = 0.70) -> float:
    """Compute Value-at-Risk (VaR_alpha) across accumulated scenario path losses.
    
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
    """Compute Conditional Tail Expectation (CTE_alpha).
    
    Standard regulatory actuarial metric:
    Average of the worst (1 - alpha) accumulated path losses.
    E.g. CTE 70 is the average of the worst 30% of scenarios.
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


def constant_level_amortization(
    initial_asset_balance: float,
    service_units_by_period: Sequence[float],
) -> list[float]:
    """Compute constant-level amortization schedule (e.g. for DAC / VOBA).
    
    Per ASU 2018-12: Amortization is constant-level over contract lifetime without interest accretion.
    Amortization_t = Initial_Asset * (Units_t / Sum(Units))
    """
    total_units = sum(service_units_by_period)
    if total_units <= 0.0 or initial_asset_balance <= 0.0:
        return [0.0] * len(service_units_by_period)

    return [initial_asset_balance * (u / total_units) for u in service_units_by_period]
