"""Hand-calculated mathematical and transition boundary cases."""

from dataclasses import replace
import pytest
from app.products.shared_modules.buffer_floor import buffer_payoff, floor_payoff
from app.products.shared_modules.indexed_crediting import ptp_cap_pr_crediting, ptp_spread_crediting, monthly_average_return
from app.products.shared_modules.segment import SegmentState, rollforward_segment_term
from app.products.shared_modules.withdrawal import apply_partial_withdrawal
from app.products.shared_modules.fixed_crediting import fixed_crediting
from app.products.shared_modules.account_value import rollforward_account_value
from app.products.shared_modules.option_budget import lookup_factor, derive_option_budget
from app.products.shared_modules.surrender_charge import schedule_rate


@pytest.mark.parametrize("r,expected", [(-.3, -.2), (-.1, 0), (0, 0), (.05, .05), (.08, .08), (.2, .08)])
def test_buffer(r, expected):
    assert buffer_payoff(r, .08, .1) == pytest.approx(expected)
    if r == -.1:
        assert buffer_payoff(r, .08, .1) == 0.0


@pytest.mark.parametrize("r,expected", [(-.3, -.1), (-.1, -.1), (-.05, -.05), (0, 0), (.2, .08)])
def test_floor(r, expected):
    assert floor_payoff(r, .08, .1) == expected


def test_crediting_vectors():
    assert ptp_cap_pr_crediting(.1, .08, .5) == .05
    assert ptp_spread_crediting(.1, .03) == pytest.approx(.07)
    assert monthly_average_return([100, 110, 120], 100) == .1
    assert fixed_crediting(100, .21, 6) == pytest.approx(10)
    assert rollforward_account_value(100, interest=-10, withdrawal=5) == 85


@pytest.mark.parametrize("mode,base", [("pro_rata", 80), ("dollar_for_dollar", 78)])
def test_withdrawal(mode, base):
    assert apply_partial_withdrawal(100, 110, 22, mode) == (base, 88)


def test_factor_lookups_fail_closed():
    rows = [{"strategy": "A", "age_lower": 40, "age_upper": 60, "rate": .03},
            {"strategy": "A", "age_lower": 60, "age_upper": 80, "rate": .04}]
    assert lookup_factor(rows, {"strategy": "A"}, "rate", range_column="age", point=60) == .04
    with pytest.raises(ValueError, match="exactly one"):
        lookup_factor(rows, {"strategy": "A"}, "rate")
    with pytest.raises(ValueError, match="exactly one"):
        lookup_factor(rows, {"strategy": "B"}, "rate")
    assert schedule_rate([{"policy_year": 1, "rate": .07}], 1) == .07
    assert derive_option_budget(.05, .02, .01) == pytest.approx(.02)


def state(**kwargs):
    return replace(SegmentState("s1", "buffer", "SP500", 12, 0, 100, 1000, 1000, .08, .1), **kwargs)


def advance(s, level, **kwargs):
    return rollforward_segment_term(s, level, interim_method="prorated_intrinsic", rollover_rule="auto_renew", **kwargs)


def test_interim_maturity_and_renewal():
    first = advance(state(), 120)
    assert first.current_interim_value == pytest.approx(1000 * (1 + .08 / 12))
    maturity = advance(state(elapsed_months=11), 120)
    assert maturity.current_interim_value == 1080
    assert maturity.is_matured
    renewed = advance(maturity, 120)
    assert renewed.base_balance == 1080
    assert renewed.elapsed_months == 1
    assert renewed.current_interim_value == 1080


def test_sweep_is_emitted_once_and_external_is_explicit():
    matured = rollforward_segment_term(state(elapsed_months=11), 120,
        interim_method="external", rollover_rule="sweep_to_fixed")
    assert matured.swept_value == 1080
    assert matured.current_interim_value == 0
    next_state = rollforward_segment_term(matured, 125,
        interim_method="external", rollover_rule="sweep_to_fixed")
    assert next_state.swept_value == 0
    with pytest.raises(ValueError, match="External interim"):
        rollforward_segment_term(state(), 110, interim_method="external", rollover_rule="auto_renew")


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nonfinite_inputs_rejected(bad):
    for fn, args in [(fixed_crediting, (bad, .03)), (derive_option_budget, (bad,)),
                     (buffer_payoff, (bad, .08, .1)), (floor_payoff, (bad, .08, .1)),
                     (apply_partial_withdrawal, (100, bad, 1))]:
        with pytest.raises(ValueError):
            fn(*args)


def test_invalid_configuration_rejected():
    with pytest.raises(ValueError):
        advance(state(term_months=0), 100)
    with pytest.raises(ValueError):
        advance(state(), 100, withdrawal_amount=1001)
    with pytest.raises(ValueError):
        apply_partial_withdrawal(100, 100, 0, "unknown")
