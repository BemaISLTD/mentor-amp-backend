"""Illustrative RILA vectors, separate from owner-approved reference acceptance."""
import pytest
from app.products.rila_lite import config as rila
from app.products.shared_modules.synthetic import block_rows
from tests.indexed_support import project, value


@pytest.mark.parametrize("strategy,expected", [("buffer", 45000), ("floor", 47500)])
def test_rila_maturity_losses(strategy, expected):
    policy = block_rows("RILA", 1)[0]
    policy.update(fixed_rate=0)
    policy["segment_1_strategy"] = strategy
    result = project(rila, policy, months=12, levels=[70] * 12)
    assert result.error is None, result.error
    assert value(result, "account_value_close", 12) == pytest.approx(expected)


def test_rila_multi_term_sweep_conserves_value():
    policy = block_rows("RILA", 2)[1]
    policy.update(fixed_rate=0, rollover_rule="sweep_to_fixed")
    result = project(rila, policy, months=37, levels=[100] * 37)
    assert result.error is None, result.error
    assert value(result, "segment_1_sweep", 12) == 30000
    assert value(result, "segment_1_sweep", 13) == 0
    assert value(result, "segment_2_sweep", 36) == 30000
    assert value(result, "fixed_av_close", 37) == 75000
    assert value(result, "account_value_close", 37) == 75000


def test_eom_withdrawal_can_use_current_month_credit():
    policy = block_rows("RILA", 1)[0]
    policy.update(fixed_rate=0, gross_withdrawal=50050)
    result = project(rila, policy, months=1, levels=[120])
    assert result.error is None, result.error
    # Indexed pre-withdrawal interim = 25,000 * (1 + .08 / 12).
    assert value(result, "account_value_close", 1) == pytest.approx(116.6666666667)


@pytest.mark.skip(reason="Independent actuarial expected results and tolerances pending ACT-M4-DEC-01..15")
def test_owner_approved_rila_golden():
    pass
