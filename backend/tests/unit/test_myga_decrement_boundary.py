"""Verify the architectural boundary between MYGA contract mechanics and decrement methodologies.

Proves that:
1. The default methodology produces identical illustrative best-estimate decrements.
2. Swapping the decrement methodology provider changes decrement rates (lapse, mortality, annuitization)
   without modifying any underlying MYGA contract formulas or rollforward mechanics.
"""

import pytest

from app.products.myga_lite import config as myga
from app.products.myga_lite.decrements import (
    DecrementMethodology,
    IllustrativeBestEstimateDecrements,
    get_current_decrement_methodology,
    reset_decrement_methodology,
    set_decrement_methodology,
)


class StressTestDecrementMethodology:
    """Alternative methodology: stress scenario with doubled lapses and zero mortality."""

    def get_monthly_mortality(self, mortality_rate_annual: float) -> float:
        return 0.0

    def get_base_lapse_rate(self, policy_year: float, guarantee_period_years: float) -> float:
        # Doubled baseline lapses
        return 0.04 if float(policy_year) < float(guarantee_period_years) else 0.50

    def get_gmir_lapse_factor(self, gmir: float) -> float:
        return 2.0

    def get_annual_lapse_rate(
        self,
        base_lapse_rate: float,
        gmir_lapse_factor: float,
        dynamic_lapse_rate: float,
        mva_lapse_factor_at: float,
    ) -> float:
        # Fixed stress lapse of 30%
        return 0.30

    def get_monthly_lapse_rate(self, annual_lapse_rate: float) -> float:
        # Standard monthly compounding
        return 1.0 - (1.0 - float(annual_lapse_rate)) ** (1.0 / 12.0)

    def get_annuitization_rate(self, renewal_window: float) -> float:
        # Elevated 5% election rate
        return 0.05 if float(renewal_window) >= 0.5 else 0.0


def test_default_illustrative_methodology_behavior():
    """Verify default provider reproduces standard baseline values."""
    reset_decrement_methodology()
    prov = get_current_decrement_methodology()
    assert isinstance(prov, IllustrativeBestEstimateDecrements)

    # In-period base lapse: 1%
    assert myga.base_lapse_rate(1, 5) == 0.01
    assert myga.base_lapse_rate(4, 5) == 0.01

    # Renewal window annuitization: 1%
    assert myga.annuitization_rate(1.0) == 0.01
    assert myga.annuitization_rate(0.0) == 0.0

    # GMIR factor
    assert myga.gmir_lapse_factor(0.0025) == 1.25


def test_swapping_methodology_provider_changes_decrements_without_modifying_contract():
    """Prove same contract formulas produce different decrement behavior with alternate methodology."""
    try:
        reset_decrement_methodology()
        default_base_lapse = myga.base_lapse_rate(1, 5)
        default_annual_lapse = myga.annual_lapse_rate(0.01, 1.0, 0.0, 1.0)
        default_annuitization = myga.annuitization_rate(1.0)
        default_mortality = myga.monthly_mortality(0.012)

        # Switch to stress methodology
        set_decrement_methodology(StressTestDecrementMethodology())

        stress_base_lapse = myga.base_lapse_rate(1, 5)
        stress_annual_lapse = myga.annual_lapse_rate(0.01, 1.0, 0.0, 1.0)
        stress_annuitization = myga.annuitization_rate(1.0)
        stress_mortality = myga.monthly_mortality(0.012)

        # Confirm differences in decrement outputs
        assert stress_base_lapse == 0.04
        assert stress_base_lapse != default_base_lapse

        assert stress_annual_lapse == 0.30
        assert stress_annual_lapse != default_annual_lapse

        assert stress_annuitization == 0.05
        assert stress_annuitization != default_annuitization

        assert stress_mortality == 0.0
        assert stress_mortality != default_mortality

        # Contract calculation mechanics (e.g. gross withdrawal, account value rollforward)
        # remain 100% identical and intact
        av_open = 100_000.0
        wd = 5_000.0
        assert myga.gross_withdrawal(av_open, wd) == 5_000.0
        assert myga.account_value_before_credit(av_open, wd) == 95_000.0
    finally:
        reset_decrement_methodology()
