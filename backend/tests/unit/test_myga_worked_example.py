"""Cent-level reproduction of the supplied five-year MYGA worked example."""

import pytest

from app.products.myga_lite import config as myga

CENT = 0.01


def _rollforward(months: int) -> dict[int, dict[str, float]]:
    prior_av = 0.0
    prior_floor = 0.0
    prior_free = 0.0
    rows = {}
    for month in range(1, months + 1):
        policy_year = (month - 1) // 12 + 1
        month_in_year = (month - 1) % 12
        withdrawal = 4_000.0 if month == 13 else 0.0
        av_open = myga.account_value_open(month, 100_000.0, prior_av)
        floor_open = myga.mgsv_open(month, 87_500.0, prior_floor)
        free = myga.free_withdrawal_available(
            policy_year, month_in_year, 100_000.0, av_open, prior_free, withdrawal,
        )
        av_close = myga.account_value_close(
            myga.account_value_before_credit(av_open, withdrawal), 0.0445,
        )
        floor_close = myga.mgsv_close(floor_open, withdrawal, 0.0, 0.028)
        rows[month] = {
            "av_open": av_open, "av_close": av_close, "mgsv_close": floor_close,
            "free": free,
        }
        prior_av, prior_floor, prior_free = av_close, floor_close, free
    return rows


def test_anchor_account_value_and_floor_checkpoints_to_cent():
    rows = _rollforward(30)

    assert rows[1]["av_close"] == pytest.approx(100_363.48, abs=CENT)
    assert rows[1]["mgsv_close"] == pytest.approx(87_701.59, abs=CENT)
    assert rows[12]["av_open"] == pytest.approx(104_071.72, abs=CENT)
    assert rows[12]["av_close"] == pytest.approx(104_450.00, abs=CENT)
    assert rows[12]["mgsv_close"] == pytest.approx(89_950.00, abs=CENT)
    assert rows[13]["av_open"] == pytest.approx(104_450.00, abs=CENT)
    assert rows[13]["av_close"] == pytest.approx(100_815.11, abs=CENT)
    assert rows[13]["mgsv_close"] == pytest.approx(86_148.02, abs=CENT)
    assert rows[24]["av_open"] == pytest.approx(104_540.04, abs=CENT)
    assert rows[24]["av_close"] == pytest.approx(104_920.03, abs=CENT)
    assert rows[24]["mgsv_close"] == pytest.approx(88_356.60, abs=CENT)


def test_month_30_surrender_example_to_cent():
    row = _rollforward(30)[30]
    excess = myga.surrender_excess(row["av_close"], row["free"])
    charge = myga.surrender_charge(excess, 0.07)
    term = myga.mva_term(3, 5, 5, 1)
    mva = myga.market_value_adjustment(0, 0.05, 0.065, term, excess, charge)
    value = myga.surrender_value(row["av_close"], mva, charge)
    benefit = myga.surrender_benefit(value, row["mgsv_close"])

    assert row["free"] == pytest.approx(10_492.00, abs=CENT)
    assert excess == pytest.approx(96_737.09, abs=CENT)
    assert charge == pytest.approx(6_771.60, abs=CENT)
    assert term == 2.5
    assert mva == pytest.approx(-3_627.64, abs=CENT)
    assert value == pytest.approx(96_829.85, abs=CENT)
    assert row["mgsv_close"] == pytest.approx(89_585.05, abs=CENT)
    assert benefit == pytest.approx(96_829.85, abs=CENT)


def test_stress_mva_cap_and_floor_binding_to_cent():
    row = _rollforward(6)[6]
    excess = myga.surrender_excess(row["av_close"], row["free"])
    charge = myga.surrender_charge(excess, 0.09)
    term = myga.mva_term(1, 5, 5, 1)
    mva = myga.market_value_adjustment(0, 0.05, 0.10, term, excess, charge)
    value = myga.surrender_value(row["av_close"], mva, charge)
    benefit = myga.surrender_benefit(value, row["mgsv_close"])

    assert row["av_close"] == pytest.approx(102_200.78, abs=CENT)
    assert excess == pytest.approx(92_200.78, abs=CENT)
    assert charge == pytest.approx(8_298.07, abs=CENT)
    assert term == 4.5
    assert mva == pytest.approx(-8_298.07, abs=CENT)
    assert value == pytest.approx(85_604.64, abs=CENT)
    assert row["mgsv_close"] == pytest.approx(88_716.54, abs=CENT)
    assert benefit == pytest.approx(88_716.54, abs=CENT)
