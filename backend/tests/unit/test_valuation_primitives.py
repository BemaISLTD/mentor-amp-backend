"""Tests for generic mathematical valuation primitives (M5E Foundation).

Verifies:
1. Exact present value discounting math across discrete time steps.
2. LDTI Net Premium Ratio (NPR) solving with 100% cap and immediate loss recognition.
3. Tail-risk metric calculations: Value-at-Risk (VaR) and Conditional Tail Expectation (CTE).
4. Constant-level DAC amortization schedule generation without interest accretion.
"""

import math
import pytest

from app.core.valuation.primitives import (
    annual_to_monthly_discount_factor,
    conditional_tail_expectation,
    constant_level_amortization,
    curve_to_discount_factors,
    discount_cash_flows,
    present_value,
    solve_net_premium_ratio,
    value_at_risk,
)


def test_annual_to_monthly_discount_factor():
    # At t=0, discount factor must be 1.0
    assert annual_to_monthly_discount_factor(0.05, 0) == 1.0

    # At month 12 (1 year), v^12 = (1 + 0.05)^(-1) = 1 / 1.05
    expected_1yr = 1.0 / 1.05
    assert annual_to_monthly_discount_factor(0.05, 12) == pytest.approx(expected_1yr, rel=1e-6)

    # At month 6, v^6 = (1.05)^(-0.5)
    expected_half_yr = math.pow(1.05, -0.5)
    assert annual_to_monthly_discount_factor(0.05, 6) == pytest.approx(expected_half_yr, rel=1e-6)


def test_present_value_discounting():
    cash_flows = [100.0, 100.0, 100.0]
    factors = [1.0, 0.95, 0.90]
    discounted = discount_cash_flows(cash_flows, factors)
    assert discounted == [100.0, 95.0, 90.0]

    pv = present_value(cash_flows, factors)
    assert pv == 285.0


def test_net_premium_ratio_normal():
    # Normal case: Benefits (800k) < Premiums (1,000k) -> NPR = 0.80
    npr, is_capped, loss = solve_net_premium_ratio(800_000.0, 1_000_000.0)
    assert npr == pytest.approx(0.80)
    assert is_capped is False
    assert loss == 0.0


def test_net_premium_ratio_capped_at_100_percent():
    # Loss recognition case: Benefits (1,250k) > Premiums (1,000k)
    # Raw NPR would be 1.25 -> must cap at 1.0 with $250k loss recognized
    npr, is_capped, loss = solve_net_premium_ratio(1_250_000.0, 1_000_000.0, cap_at_one=True)
    assert npr == 1.0
    assert is_capped is True
    assert loss == pytest.approx(250_000.0)


def test_tail_metrics_var_and_cte():
    # Generate 100 deterministic path losses: 1, 2, 3, ..., 100
    path_losses = list(range(1, 101))

    # VaR 70: 70th percentile of 1..100 is 70
    var_70 = value_at_risk(path_losses, alpha=0.70)
    assert var_70 == 70

    # CTE 70: average of the worst 30% (paths 71 to 100)
    # Average of 71 + ... + 100 = (71 + 100) / 2 = 85.5
    cte_70 = conditional_tail_expectation(path_losses, alpha=0.70)
    assert cte_70 == pytest.approx(85.5)

    # CTE 90: average of the worst 10% (paths 91 to 100) -> 95.5
    cte_90 = conditional_tail_expectation(path_losses, alpha=0.90)
    assert cte_90 == pytest.approx(95.5)


def test_constant_level_amortization():
    initial_dac = 100_000.0
    units = [250.0, 250.0, 250.0, 250.0]  # Total 1,000 units
    schedule = constant_level_amortization(initial_dac, units)

    assert len(schedule) == 4
    assert schedule == [25_000.0, 25_000.0, 25_000.0, 25_000.0]
    assert sum(schedule) == pytest.approx(initial_dac)
