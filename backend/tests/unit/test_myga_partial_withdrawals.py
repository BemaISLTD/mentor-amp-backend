"""Actuarial tests for MYGA partial withdrawal economics (MYGA-PW-01 through MYGA-PW-08).

Verifies the distinction between:
- gross withdrawal debited from opening account value;
- free withdrawal allowance available at BOM;
- chargeable withdrawal excess;
- contractual surrender charge assessed on excess;
- linear MVA applied to excess (symmetrically capped by withdrawal charge);
- net withdrawal benefit paid to the policyholder;
- insurer liability cash outgo.
"""

import math
import pytest

from app.products.myga_lite import config as myga


def test_myga_pw_01_withdrawal_below_free_allowance():
    """MYGA-PW-01: Withdrawal below free allowance incurs no charge, no excess, full net payout."""
    # Policy: Year 1, month 0 (BOM), free_wd_base = $100,000, AV_open = $100,000
    # Free allowance = 10% = $10,000. Requested gross withdrawal = $4,000.
    av_open = 100_000.0
    wd_requested = 4_000.0
    sc_rate = 0.07

    gross = myga.gross_withdrawal(av_open, wd_requested)
    allowance = myga.free_withdrawal_allowance(1, 0, 100_000.0, av_open, 0.0)
    chargeable = myga.chargeable_withdrawal(gross, allowance)
    charge = myga.surrender_charge_on_withdrawal(chargeable, sc_rate)
    mva = myga.withdrawal_mva(0, 0.05, 0.05, 4.0, chargeable, charge)
    net_benefit = myga.net_withdrawal_benefit(gross, charge, mva)
    rem_free = myga.free_withdrawal_available(1, 0, 100_000.0, av_open, 0.0, wd_requested)

    assert gross == 4_000.0
    assert allowance == 10_000.0
    assert chargeable == 0.0
    assert charge == 0.0
    assert mva == 0.0
    assert net_benefit == 4_000.0
    assert rem_free == 6_000.0
    # Insurer cash outgo equals net benefit
    assert myga.withdrawal_payment(1.0, net_benefit) == 4_000.0


def test_myga_pw_02_withdrawal_equal_to_free_allowance():
    """MYGA-PW-02: Withdrawal exactly equal to free allowance incurs zero surrender charge."""
    av_open = 100_000.0
    wd_requested = 10_000.0
    sc_rate = 0.07

    gross = myga.gross_withdrawal(av_open, wd_requested)
    allowance = myga.free_withdrawal_allowance(1, 0, 100_000.0, av_open, 0.0)
    chargeable = myga.chargeable_withdrawal(gross, allowance)
    charge = myga.surrender_charge_on_withdrawal(chargeable, sc_rate)
    net_benefit = myga.net_withdrawal_benefit(gross, charge, 0.0)
    rem_free = myga.free_withdrawal_available(1, 0, 100_000.0, av_open, 0.0, wd_requested)

    assert gross == 10_000.0
    assert allowance == 10_000.0
    assert chargeable == 0.0
    assert charge == 0.0
    assert net_benefit == 10_000.0
    assert rem_free == 0.0


def test_myga_pw_03_withdrawal_above_free_allowance():
    """MYGA-PW-03: Withdrawal above free allowance incurs charge only on the excess amount."""
    av_open = 100_000.0
    wd_requested = 15_000.0
    sc_rate = 0.07

    gross = myga.gross_withdrawal(av_open, wd_requested)
    allowance = myga.free_withdrawal_allowance(1, 0, 100_000.0, av_open, 0.0)
    chargeable = myga.chargeable_withdrawal(gross, allowance)
    charge = myga.surrender_charge_on_withdrawal(chargeable, sc_rate)
    net_benefit = myga.net_withdrawal_benefit(gross, charge, 0.0)

    assert gross == 15_000.0
    assert allowance == 10_000.0
    assert chargeable == 5_000.0
    assert charge == pytest.approx(5_000.0 * 0.07)  # $350.0
    assert net_benefit == pytest.approx(15_000.0 - 350.0)  # $14,650.0


def test_myga_pw_04_development_reference_excess_withdrawal_case():
    """MYGA-PW-04: Match exact MentorAmp development reference $20,000 excess withdrawal case.

    Reference numbers:
      prior anniversary AV = 104,500.00
      10% free allowance = 10,450.00
      gross withdrawal = 20,000.00
      chargeable withdrawal = 9,550.00
      surrender charge (6%) = 573.00
      net benefit without MVA = 19,427.00
    """
    av_open = 108_802.67  # month 24 BOM
    free_base_anniv = 104_500.00  # anniversary AV from month 12
    wd_requested = 20_000.0
    sc_rate = 0.06

    gross = myga.gross_withdrawal(av_open, wd_requested)
    # Year 2, month 0 (month 13) or non-anniversary month tracking prior available:
    allowance = 0.10 * free_base_anniv  # 10,450.00
    chargeable = myga.chargeable_withdrawal(gross, allowance)
    charge = myga.surrender_charge_on_withdrawal(chargeable, sc_rate)
    mva = myga.withdrawal_mva(0, 0.05, 0.05, 3.0, chargeable, charge)  # MVA excluded / flat yields
    net_benefit = myga.net_withdrawal_benefit(gross, charge, mva)

    assert gross == 20_000.0
    assert allowance == 10_450.0
    assert chargeable == 9_550.0
    assert math.isclose(charge, 573.0, abs_tol=1e-6)
    assert math.isclose(net_benefit, 19_427.0, abs_tol=1e-6)
    assert math.isclose(myga.withdrawal_payment(1.0, net_benefit), 19_427.0, abs_tol=1e-6)


def test_myga_pw_05_partial_withdrawal_with_positive_mva():
    """MYGA-PW-05: Partial withdrawal with positive MVA where market yields fell."""
    # locked_yield = 5.0%, current_yield = 4.0% (+100bp gain for policyholder)
    # term = 3 years. Chargeable = 9,550. Charge = 573.00.
    # raw MVA = (0.05 - 0.04) * 3 * 9,550 = +286.50
    # Symmetric cap = charge ($573.00) -> MVA = +286.50
    # net_benefit = 20,000 - 573 + 286.50 = 19,713.50
    gross = 20_000.0
    chargeable = 9_550.0
    charge = 573.0
    mva = myga.withdrawal_mva(
        renewal_window=0,
        locked_mva_reference_yield=0.05,
        mva_ref_yield=0.04,
        mva_term=3.0,
        chargeable_withdrawal=chargeable,
        surrender_charge_on_withdrawal=charge,
    )
    assert math.isclose(mva, 286.50, abs_tol=1e-6)
    net_benefit = myga.net_withdrawal_benefit(gross, charge, mva)
    assert math.isclose(net_benefit, 19_713.50, abs_tol=1e-6)


def test_myga_pw_06_partial_withdrawal_with_negative_mva():
    """MYGA-PW-06: Partial withdrawal with negative MVA where market yields rose."""
    # locked_yield = 5.0%, current_yield = 6.0% (-100bp haircut for policyholder)
    # term = 3 years. Chargeable = 9,550. Charge = 573.00.
    # raw MVA = (0.05 - 0.06) * 3 * 9,550 = -286.50
    # Symmetrically capped at -charge (-$573.00) -> MVA = -286.50
    # net_benefit = 20,000 - 573 - 286.50 = 19,140.50
    gross = 20_000.0
    chargeable = 9_550.0
    charge = 573.0
    mva = myga.withdrawal_mva(
        renewal_window=0,
        locked_mva_reference_yield=0.05,
        mva_ref_yield=0.06,
        mva_term=3.0,
        chargeable_withdrawal=chargeable,
        surrender_charge_on_withdrawal=charge,
    )
    assert math.isclose(mva, -286.50, abs_tol=1e-6)
    net_benefit = myga.net_withdrawal_benefit(gross, charge, mva)
    assert math.isclose(net_benefit, 19_140.50, abs_tol=1e-6)


def test_myga_pw_07_partial_withdrawal_cannot_exceed_account_value():
    """MYGA-PW-07: Withdrawal cannot reduce AV below zero / exceed opening account value."""
    av_open = 50_000.0
    # Exact AV is allowed
    assert myga.gross_withdrawal(av_open, 50_000.0) == 50_000.0

    # Greater than AV raises ValueError
    with pytest.raises(ValueError, match="opening account value"):
        myga.gross_withdrawal(av_open, 50_000.01)

    # Negative withdrawal raises ValueError
    with pytest.raises(ValueError, match="between zero and opening account value"):
        myga.gross_withdrawal(av_open, -100.0)


def test_myga_pw_08_trace_and_formula_graph_lineage():
    """MYGA-PW-08: Formula graph captures lineage for all partial withdrawal stages."""
    myga.register_functions()
    formula_map = {row["output_variable"]: row for row in myga.FORMULAS}

    # Verify all partial-withdrawal variables exist and have correct dependency paths
    assert "gross_withdrawal" in formula_map
    assert formula_map["gross_withdrawal"]["dependencies"] == ["account_value_open", "withdrawal"]

    assert "free_withdrawal_allowance" in formula_map
    assert set(formula_map["free_withdrawal_allowance"]["dependencies"]) == {
        "policy_year", "month_in_policy_year", "free_wd_base",
        "account_value_open", "free_withdrawal_available_previous",
    }

    assert "chargeable_withdrawal" in formula_map
    assert formula_map["chargeable_withdrawal"]["dependencies"] == [
        "gross_withdrawal", "free_withdrawal_allowance",
    ]

    assert "surrender_charge_on_withdrawal" in formula_map
    assert formula_map["surrender_charge_on_withdrawal"]["dependencies"] == [
        "chargeable_withdrawal", "surrender_charge_rate",
    ]

    assert "withdrawal_mva" in formula_map
    assert set(formula_map["withdrawal_mva"]["dependencies"]) == {
        "renewal_window", "locked_mva_reference_yield", "mva_ref_yield",
        "mva_term", "chargeable_withdrawal", "surrender_charge_on_withdrawal",
    }

    assert "net_withdrawal_benefit" in formula_map
    assert formula_map["net_withdrawal_benefit"]["dependencies"] == [
        "gross_withdrawal", "surrender_charge_on_withdrawal", "withdrawal_mva",
    ]

    assert "withdrawal_payment" in formula_map
    assert formula_map["withdrawal_payment"]["dependencies"] == [
        "inforce_open", "net_withdrawal_benefit",
    ]
