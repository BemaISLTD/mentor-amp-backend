"""Pure illustrative disability-income calculation primitives.

The functions in this module make timing and competing-decrement conventions
explicit.  They contain no product defaults, table lookups, or approval claims.
"""

from __future__ import annotations

import math
from typing import Any


def _probability(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be finite and in [0, 1].")
    return value


def _nonnegative(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and non-negative.")
    return value


def _nonnegative_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def monthly_incidence_prob(annual_incidence_rate: float) -> float:
    """Convert an annual incidence probability to a monthly probability."""
    rate = _probability("annual_incidence_rate", annual_incidence_rate)
    return 1.0 - (1.0 - rate) ** (1.0 / 12.0)


def monthly_termination_prob(termination_rate: float, rate_basis: str = "annual") -> float:
    """Return a monthly termination probability for an explicit rate basis."""
    rate = _probability("termination_rate", termination_rate)
    if rate_basis == "annual":
        return 1.0 - (1.0 - rate) ** (1.0 / 12.0)
    if rate_basis == "monthly":
        return rate
    raise ValueError("rate_basis must be 'annual' or 'monthly'.")


def idi_benefit_payable(
    claim_months_elapsed: int,
    elimination_months: int,
    max_benefit_months: int,
    monthly_benefit: float,
    disabled_probability: float = 1.0,
) -> float:
    """Return expected EOM benefit for an existing disabled cohort.

    ``claim_months_elapsed`` is the duration after the current monthly step.
    Benefits are payable only after elimination and through the maximum benefit
    period, inclusive.
    """
    elapsed = _nonnegative_integer("claim_months_elapsed", claim_months_elapsed)
    elimination = _nonnegative_integer("elimination_months", elimination_months)
    maximum = _nonnegative_integer("max_benefit_months", max_benefit_months)
    if maximum == 0:
        raise ValueError("max_benefit_months must be positive.")
    benefit = _nonnegative("monthly_benefit", monthly_benefit)
    disabled = _probability("disabled_probability", disabled_probability)
    if elimination < elapsed <= elimination + maximum:
        return disabled * benefit
    return 0.0


def idi_net_liability_cash_flow(
    expected_claim_benefit_outgo: float,
    expected_expense_outgo: float,
    expected_premium_inflow: float,
    expected_new_claim_incurred: float = 0.0,
) -> float:
    """Return insurer liability cash flow: outgo plus incurred cost less premium."""
    claim = _nonnegative("expected_claim_benefit_outgo", expected_claim_benefit_outgo)
    expense = _nonnegative("expected_expense_outgo", expected_expense_outgo)
    premium = _nonnegative("expected_premium_inflow", expected_premium_inflow)
    incurred = _nonnegative("expected_new_claim_incurred", expected_new_claim_incurred)
    return claim + expense + incurred - premium


def idi_markov_step(
    *,
    p_active_open: float,
    p_disabled_open: float,
    q_mort_active_m: float,
    q_lapse_m: float,
    q_inc_m: float,
    q_term_m: float,
    q_mort_disabled_m: float,
    claim_months_elapsed: int,
    elimination_months: int,
    max_benefit_months: int,
    monthly_benefit: float,
    monthly_premium: float,
    expense_per_policy_monthly: float = 0.0,
    waive_premium_on_disability: bool = True,
    new_claim_dlr_factor: float = 0.0,
) -> dict[str, Any]:
    """Advance illustrative active/disabled states one month using EOM timing.

    Active decrements are treated as mutually exclusive additive probabilities.
    Existing disabled lives experience sequential recovery/termination followed
    by mortality.  New incident claims enter the disabled closing state but do
    not receive a current-month benefit; their incurred cost is represented by
    the explicit ``new_claim_dlr_factor``.
    """
    active = _probability("p_active_open", p_active_open)
    disabled = _probability("p_disabled_open", p_disabled_open)
    if active + disabled > 1.0 + 1e-12:
        raise ValueError("Opening active and disabled probabilities cannot exceed 1.0.")

    q_mort_active = _probability("q_mort_active_m", q_mort_active_m)
    q_lapse = _probability("q_lapse_m", q_lapse_m)
    q_inc = _probability("q_inc_m", q_inc_m)
    q_term = _probability("q_term_m", q_term_m)
    q_mort_disabled = _probability("q_mort_disabled_m", q_mort_disabled_m)
    active_decrements = q_mort_active + q_lapse + q_inc
    if active_decrements > 1.0 + 1e-12:
        raise ValueError("Competing active decrements cannot exceed 1.0.")

    elapsed_open = _nonnegative_integer("claim_months_elapsed", claim_months_elapsed)
    elimination = _nonnegative_integer("elimination_months", elimination_months)
    maximum = _nonnegative_integer("max_benefit_months", max_benefit_months)
    if maximum == 0:
        raise ValueError("max_benefit_months must be positive.")
    benefit = _nonnegative("monthly_benefit", monthly_benefit)
    premium = _nonnegative("monthly_premium", monthly_premium)
    expense = _nonnegative("expense_per_policy_monthly", expense_per_policy_monthly)
    dlr_factor = _nonnegative("new_claim_dlr_factor", new_claim_dlr_factor)
    if not isinstance(waive_premium_on_disability, bool):
        raise ValueError("waive_premium_on_disability must be boolean.")

    new_disabled = active * q_inc
    active_close = active * (1.0 - active_decrements)
    disabled_surviving = disabled * (1.0 - q_term) * (1.0 - q_mort_disabled)
    disabled_close = disabled_surviving + new_disabled
    claim_elapsed_close = elapsed_open + 1 if disabled > 0.0 else 0

    expected_benefit = idi_benefit_payable(
        claim_elapsed_close,
        elimination,
        maximum,
        benefit,
        disabled_surviving,
    )
    premium_weight = active_close if waive_premium_on_disability else active_close + disabled_close
    expected_premium = premium_weight * premium
    expected_expense = (active_close + disabled_close) * expense
    expected_new_claim = new_disabled * benefit * dlr_factor

    return {
        "active_prob_close": active_close,
        "disabled_prob_close": disabled_close,
        "new_disabled": new_disabled,
        "disabled_surviving": disabled_surviving,
        "claim_months_elapsed_close": claim_elapsed_close,
        "expected_premium_inflow": expected_premium,
        "expected_claim_benefit_outgo": expected_benefit,
        "expected_expense_outgo": expected_expense,
        "expected_new_claim_incurred": expected_new_claim,
        "net_liability_cash_flow": idi_net_liability_cash_flow(
            expected_benefit,
            expected_expense,
            expected_premium,
            expected_new_claim,
        ),
    }
