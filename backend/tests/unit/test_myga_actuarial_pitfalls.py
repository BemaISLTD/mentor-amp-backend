"""Explicit guards for the actuarial implementation pitfalls in the M2B specification."""

import pytest

from app.products.myga_lite import config as myga


def test_mva_uses_excess_before_charge_is_deducted():
    excess = myga.surrender_excess(100_000, 10_000)
    charge = myga.surrender_charge(excess, 0.05)
    mva = myga.market_value_adjustment(0, 0.05, 0.06, 3, excess, charge)
    assert mva == pytest.approx(-2_700)


def test_floor_is_applied_after_mva_and_charge():
    contractual = myga.surrender_value(100_000, -5_000, 8_000)
    assert contractual == 87_000
    assert myga.surrender_benefit(contractual, 90_000) == 90_000


def test_free_withdrawal_is_exempt_from_charge_and_mva():
    excess = myga.surrender_excess(100_000, 10_000)
    charge = myga.surrender_charge(excess, 0.09)
    mva = myga.market_value_adjustment(0, 0.05, 0.06, 2, excess, charge)
    assert excess == 90_000
    assert charge == 8_100
    assert mva == pytest.approx(-1_800)


def test_floor_deducts_withdrawal_gross():
    expected = (87_500 - 4_000) * (1.0 + 0.028) ** (1.0 / 12.0)
    assert myga.mgsv_close(87_500, 4_000, 0, 0.028) == pytest.approx(expected)


def test_model_805_corridor_has_fifteen_basis_point_floor():
    assert myga.statutory_floor_rate(0.005) == 0.0015
    assert myga.statutory_floor_rate(0.10) == 0.03


def test_renewal_charge_resets_and_window_waives_charge():
    assert myga.surrender_charge_rate(0, 0.05, 1.0, "renewal") == 0.05
    assert myga.surrender_charge_rate(1, 0.05, 1.0, "renewal") == 0.0
    assert myga.surrender_charge_rate(0, 0.05, 0.03, "renewal") == 0.03


def test_renewal_window_waives_both_charge_and_mva():
    assert myga.surrender_charge_rate(1, 0.05, 1.0, "renewal") == 0.0
    assert myga.market_value_adjustment(1, 0.05, 0.08, 4, 90_000, 5_000) == 0.0


def test_shock_lapse_repeats_and_is_capped_at_ninety_percent():
    assert myga.base_lapse_rate(5, 5) == 0.01
    assert myga.base_lapse_rate(6, 5) == 0.75
    assert myga.base_lapse_rate(11, 5) == 0.75
    assert myga.annual_lapse_rate(0.75, 1.25, 0, 0.35) == 0.90


def test_ledger_excludes_interest_charge_mva_and_floor_movements():
    outgo = myga.liability_outgo(100, 200, 300, 400, 500, 50, 2_000)
    assert outgo == 3_550
    assert "surrender_charge" not in myga.liability_outgo.__code__.co_varnames
    assert "market_value_adjustment" not in myga.liability_outgo.__code__.co_varnames
    assert "mgsv_close" not in myga.liability_outgo.__code__.co_varnames


def test_dynamic_lapse_buffer_haircut_and_mva_factor():
    assert myga.dynamic_lapse_market(0.0445, 0.0445, 2.0) == 0.0
    assert myga.dynamic_lapse_market(0.0445, 0.0490, 2.0) == 0.0
    raw = myga.dynamic_lapse_market(0.04, 0.06, 2.0)
    assert raw > 0
    assert myga.dynamic_lapse_rate(raw, 80, 100) == pytest.approx(0.0)
    assert myga.annual_lapse_rate(0.01, 1.25, raw, 0.35) <= 0.90
    assert myga.mva_lapse_factor_at(1 / 12, 0, 0.35) == 0.35
    assert myga.mva_lapse_factor_at(0, 0, 0.35) == 1.0
    assert myga.mva_lapse_factor_at(5, 1, 0.35) == 1.0


def test_maturity_occurs_at_month_end_when_age_100_is_reached():
    assert myga.maturity_rate(99, 10) == 0.0
    assert myga.maturity_rate(99, 11) == 1.0
    assert myga.maturity_rate(100, 0) == 1.0
