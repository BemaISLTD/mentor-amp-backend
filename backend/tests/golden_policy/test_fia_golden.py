"""Illustrative engine vectors; formal actuarial golden acceptance stays pending."""
import pytest
from app.products.fia_lite import config as fia
from app.products.shared_modules.synthetic import block_rows
from tests.indexed_support import project, value


def test_fia_anniversary_credit_and_compounding():
    policy = block_rows("FIA", 1)[0]
    policy["fixed_rate"] = 0
    result = project(fia, policy, months=24, levels=[120] * 24)
    assert result.error is None, result.error
    # 25,000 fixed + 25,000 indexed, with an 8% annual cap.
    assert value(result, "account_value_close", 12) == 52000
    assert value(result, "account_value_close", 24) == 52000  # renewed at 120: flat second year
    assert value(result, "cash_surrender_value", 12) == 49400
    assert value(result, "death_benefit", 12) == 52000


def test_fia_withdrawals_and_fees_feed_state():
    policy = block_rows("FIA", 1)[0]
    policy.update(fixed_rate=0, gross_withdrawal=100, fee=10)
    result = project(fia, policy, months=2, levels=[100, 100])
    assert result.error is None, result.error
    assert value(result, "account_value_close", 1) == pytest.approx(49890)
    assert value(result, "account_value_close", 2) == pytest.approx(49780)
    assert value(result, "net_cash_flow", 2) == -90


@pytest.mark.skip(reason="Independent actuarial expected results and tolerances pending ACT-M4-DEC-01..15")
def test_owner_approved_fia_golden():
    pass
