import math

import pytest

from app.products.shared_modules.disability import (
    idi_benefit_payable,
    idi_markov_step,
    idi_net_liability_cash_flow,
    monthly_incidence_prob,
    monthly_termination_prob,
)
from app.products.shared_modules.prt_benefits import (
    non_termination_step,
    prt_escalated_payment,
    prt_monthly_benefit_factor,
)
from app.products.shared_modules.variable_annuity import (
    monthly_fee_rate,
    va_death_benefit,
    va_guarantee_step,
    va_nar_death_claim,
    va_subaccount_rollforward,
    va_total_death_benefit,
)


def test_disability_rate_conversions_are_explicit_and_fail_closed():
    expected = 1.0 - 0.88 ** (1.0 / 12.0)
    assert monthly_incidence_prob(0.12) == pytest.approx(expected)
    assert monthly_termination_prob(0.12, "annual") == pytest.approx(expected)
    assert monthly_termination_prob(0.01, "monthly") == 0.01
    with pytest.raises(ValueError):
        monthly_termination_prob(0.01, "daily")
    for invalid in (-0.01, 1.01, math.inf, math.nan):
        with pytest.raises(ValueError):
            monthly_incidence_prob(invalid)


def test_idi_elimination_and_maximum_benefit_windows():
    for month in range(1, 4):
        assert idi_benefit_payable(month, 3, 60, 5_000.0) == 0.0
    assert idi_benefit_payable(4, 3, 60, 5_000.0) == 5_000.0
    assert idi_benefit_payable(63, 3, 60, 5_000.0) == 5_000.0
    assert idi_benefit_payable(64, 3, 60, 5_000.0) == 0.0


def test_idi_markov_step_waives_premium_and_tracks_incurred_new_claims():
    common = dict(
        p_active_open=0.8,
        p_disabled_open=0.1,
        q_mort_active_m=0.01,
        q_lapse_m=0.02,
        q_inc_m=0.03,
        q_term_m=0.04,
        q_mort_disabled_m=0.05,
        claim_months_elapsed=3,
        elimination_months=3,
        max_benefit_months=60,
        monthly_benefit=5_000.0,
        monthly_premium=100.0,
        expense_per_policy_monthly=10.0,
        new_claim_dlr_factor=2.0,
    )
    waived = idi_markov_step(**common, waive_premium_on_disability=True)
    continued = idi_markov_step(**common, waive_premium_on_disability=False)
    assert waived["new_disabled"] == pytest.approx(0.024)
    assert waived["active_prob_close"] == pytest.approx(0.752)
    assert waived["disabled_surviving"] == pytest.approx(0.0912)
    assert waived["disabled_prob_close"] == pytest.approx(0.1152)
    assert waived["expected_claim_benefit_outgo"] == pytest.approx(456.0)
    assert waived["expected_new_claim_incurred"] == pytest.approx(240.0)
    assert waived["expected_premium_inflow"] == pytest.approx(75.2)
    assert continued["expected_premium_inflow"] == pytest.approx(86.72)
    assert waived["net_liability_cash_flow"] == pytest.approx(
        idi_net_liability_cash_flow(456.0, 8.672, 75.2, 240.0)
    )


def test_idi_rejects_invalid_competing_decrements_and_nonfinite_values():
    kwargs = dict(
        p_active_open=1.0,
        p_disabled_open=0.0,
        q_mort_active_m=0.4,
        q_lapse_m=0.4,
        q_inc_m=0.3,
        q_term_m=0.0,
        q_mort_disabled_m=0.0,
        claim_months_elapsed=0,
        elimination_months=3,
        max_benefit_months=60,
        monthly_benefit=1_000.0,
        monthly_premium=100.0,
    )
    with pytest.raises(ValueError, match="Competing active decrements"):
        idi_markov_step(**kwargs)
    kwargs["q_inc_m"] = math.nan
    with pytest.raises(ValueError):
        idi_markov_step(**kwargs)


def test_va_fee_conversion_methods_and_validation():
    assert monthly_fee_rate(0.012, "simple_12th") == pytest.approx(0.001)
    assert monthly_fee_rate(0.012, "effective_monthly_rate") == pytest.approx(
        1.012 ** (1 / 12) - 1
    )
    assert monthly_fee_rate(0.012, "constant_force_deduction") == pytest.approx(
        1 - 0.988 ** (1 / 12)
    )
    with pytest.raises(ValueError):
        monthly_fee_rate(0.012, "implicit")
    with pytest.raises(ValueError):
        monthly_fee_rate(1.0, "constant_force_deduction")


def test_va_subaccount_rollforward_conserves_balances_and_caps_fees():
    result = va_subaccount_rollforward(
        eq_av_open=60.0,
        bond_av_open=30.0,
        fixed_av_open=10.0,
        eq_return=0.10,
        bond_return=0.0,
        fixed_rate=0.0,
        me_fee_rate=0.12,
        admin_fee_rate=0.0,
        rider_fee_rate=0.0,
        fee_conversion_method="simple_12th",
        gross_withdrawal=20.0,
    )
    assert result["account_value_after_growth"] == pytest.approx(106.0)
    assert result["fee_collected"] == pytest.approx(1.06)
    assert result["account_value_close"] == pytest.approx(84.94)
    assert sum(result[key] for key in ("eq_av_close", "bond_av_close", "fixed_av_close")) == pytest.approx(
        result["account_value_close"]
    )

    exhausted = va_subaccount_rollforward(
        eq_av_open=1.0,
        bond_av_open=0.0,
        fixed_av_open=0.0,
        eq_return=0.0,
        bond_return=0.0,
        fixed_rate=0.0,
        me_fee_rate=0.0,
        admin_fee_rate=0.0,
        rider_fee_rate=0.0,
        fee_conversion_method="simple_12th",
        gross_withdrawal=5.0,
    )
    assert exhausted["account_value_close"] == 0.0
    assert exhausted["av_paid_withdrawal"] == 1.0
    assert exhausted["rider_shortfall_raw"] == 4.0
    assert exhausted["av_exhausted"] == 1.0


def test_va_gmwb_exhausts_but_glwb_continues_for_life():
    gmwb = va_guarantee_step(
        rider_type="GMWB",
        benefit_base_open=100_000.0,
        gmwb_remaining_open=0.0,
        gmdb_base_open=0.0,
        account_value_before_withdrawal=0.0,
        account_value_close=0.0,
        av_paid_withdrawal=0.0,
        gross_withdrawal=10_000 / 12,
        gwb_pct=0.10,
    )
    assert gmwb["rider_claim_payment"] == 0.0
    assert gmwb["gmwb_remaining_close"] == 0.0

    glwb = va_guarantee_step(
        rider_type="GLWB",
        benefit_base_open=100_000.0,
        gmwb_remaining_open=0.0,
        gmdb_base_open=0.0,
        account_value_before_withdrawal=0.0,
        account_value_close=0.0,
        av_paid_withdrawal=0.0,
        gross_withdrawal=5_000 / 12,
        gwb_pct=0.05,
        survival_open=1.0,
        ratchet_enabled=False,
    )
    assert glwb["benefit_base_close"] == 100_000.0
    assert glwb["rider_claim_payment"] == pytest.approx(5_000 / 12)


def test_va_glwb_excess_withdrawal_reduces_base_pro_rata():
    result = va_guarantee_step(
        rider_type="GLWB",
        benefit_base_open=100_000.0,
        gmwb_remaining_open=0.0,
        gmdb_base_open=0.0,
        account_value_before_withdrawal=80_000.0,
        account_value_close=67_000.0,
        av_paid_withdrawal=13_000.0,
        gross_withdrawal=13_000.0,
        gwb_pct=0.05,
        survival_open=1.0,
        ratchet_enabled=False,
    )
    # Annual-step benchmark expressed with an annual allowance of 5,000.
    annual_result = va_guarantee_step(
        rider_type="GLWB",
        benefit_base_open=100_000.0,
        gmwb_remaining_open=0.0,
        gmdb_base_open=0.0,
        account_value_before_withdrawal=80_000.0,
        account_value_close=67_000.0,
        av_paid_withdrawal=13_000.0,
        gross_withdrawal=13_000.0,
        gwb_pct=0.60,
        survival_open=1.0,
        ratchet_enabled=False,
    )
    assert result["benefit_base_close"] < 100_000.0
    assert annual_result["monthly_income_allowance"] == 5_000.0
    assert annual_result["benefit_base_close"] == pytest.approx(89_333.33333333333)


def test_va_death_benefit_structured_contract_and_scalar_wrappers():
    result = va_death_benefit(80_000.0, 100_000.0, "GMDB_ROP")
    assert result == {"total_death_benefit": 100_000.0, "nar_death_claim": 20_000.0}
    assert va_total_death_benefit(80_000.0, 100_000.0, "GMDB_ROP") == result["total_death_benefit"]
    assert va_nar_death_claim(80_000.0, 100_000.0, "GMDB_ROP") == result["nar_death_claim"]
    with pytest.raises(ValueError):
        va_death_benefit(1.0, 1.0, "UNKNOWN")


def test_prt_non_termination_and_life_only_zero_termination():
    assert non_termination_step(1.0, 0.0) == 1.0
    factor = prt_monthly_benefit_factor(
        "in_pay", 1, 0, 0, "life_only", 0.0, 0.90, 1.0
    )
    assert factor == pytest.approx(0.90)
    assert 1_000.0 * factor == pytest.approx(900.0)


def test_prt_deferred_certain_and_joint_survivor_examples():
    stay = 0.998**24
    assert prt_monthly_benefit_factor(
        "deferred", 24, 24, 0, "life_only", 0.0, 0.96, stay
    ) == 0.0
    assert prt_monthly_benefit_factor(
        "deferred", 25, 24, 0, "life_only", 0.0, 0.96, stay
    ) == pytest.approx(stay * 0.96)
    assert prt_monthly_benefit_factor(
        "in_pay", 60, 0, 120, "certain_and_life", 0.0, 0.85
    ) == 1.0
    assert prt_monthly_benefit_factor(
        "in_pay", 121, 0, 120, "certain_and_life", 0.0, 0.85
    ) == 0.85
    assert prt_monthly_benefit_factor(
        "in_pay", 1, 0, 0, "joint_survivor", 0.5, 0.8, 1.0, 0.9
    ) == pytest.approx(0.89)


def test_prt_cola_cap_and_invalid_decrements_fail_closed():
    assert prt_escalated_payment(1_000.0, 3, 0.05, 0.03) == pytest.approx(1_060.90)
    assert prt_escalated_payment(1_000.0, 3, 0.05, 0.0) == 1_000.0
    for invalid in (-0.1, 1.2, math.inf, math.nan):
        with pytest.raises(ValueError):
            non_termination_step(1.0, invalid)
    with pytest.raises(ValueError):
        prt_monthly_benefit_factor(
            "in_pay", 1, 0, 0, "joint_survivor", 0.5, 0.8, 1.0, None
        )
    with pytest.raises(ValueError):
        prt_monthly_benefit_factor(
            "in_pay", 1, 0, 0, "joint_survivor", 0.0, 0.8, 1.0, 0.9
        )
    with pytest.raises(ValueError):
        prt_monthly_benefit_factor(
            "in_pay", 1, 0, 0, "certain_and_life", 0.0, 0.8
        )
