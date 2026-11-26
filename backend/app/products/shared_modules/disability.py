"""Pure illustrative disability-income calculation primitives.

The functions in this module make timing, claim-duration cohort tracking, and
competing-decrement conventions explicit.  They contain no product defaults,
table lookups, or approval claims.

Selected Illustrative Convention (Pending ACT-P7-DEC-01 & ACT-P7-DEC-02):
- Existing disabled lives are tracked explicitly by individual claim duration
  (``{claim_months_elapsed: probability}``) so claims incurred in different
  months never lose their elimination or maximum-benefit boundaries.
- Newly incident claims from the active state follow one of two mutually
  exclusive treatments so claims are NEVER double-counted:
  1. ``"cohort_runoff"`` (Option A, requires ``new_claim_dlr_factor == 0.0``):
     New incident claims enter the disabled cohort map at duration ``0`` at EOM
     of their incidence month (paying no benefit in the incidence month) and
     advance by ``+1`` month each subsequent period through elimination and
     maximum-benefit windows.
  2. ``"dlr_incurred"`` (Option B, used when valuing new claims via DLR):
     New incident claims are settled at incidence via
     ``expected_new_claim_incurred = new_disabled * monthly_benefit * new_claim_dlr_factor``
     and are NOT added to the benefit-paying disabled cohort map in subsequent
     months, while any pre-existing disabled cohorts run off by exact duration.
"""

from __future__ import annotations

import json
import math
from typing import Any, Mapping


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


def parse_disabled_cohorts(raw: str | Mapping[Any, Any] | None) -> dict[int, float]:
    """Parse and validate a ``{claim_duration_months: probability}`` cohort map."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("disabled_cohorts must be valid JSON.") from exc
    elif isinstance(raw, Mapping):
        parsed = raw
    else:
        raise ValueError("disabled_cohorts must be a mapping or JSON object string.")
    if not isinstance(parsed, Mapping):
        raise ValueError("disabled_cohorts must decode to a JSON object.")
    cohorts: dict[int, float] = {}
    for raw_duration, raw_prob in parsed.items():
        if isinstance(raw_duration, bool):
            raise ValueError("Claim duration key cannot be boolean.")
        try:
            duration = int(raw_duration)
            if isinstance(raw_duration, str) and str(duration) != raw_duration.strip():
                raise ValueError
            if isinstance(raw_duration, float) and raw_duration != duration:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid claim duration key {raw_duration!r}.") from exc
        if duration < 0:
            raise ValueError("Claim duration key must be a non-negative integer.")
        prob = _probability(f"disabled_cohorts[{duration}]", raw_prob)
        if prob > 0.0:
            cohorts[duration] = cohorts.get(duration, 0.0) + prob
    if math.fsum(cohorts.values()) > 1.0 + 1e-12:
        raise ValueError("Total disabled cohort probability cannot exceed 1.0.")
    return cohorts


def serialize_disabled_cohorts(cohorts: Mapping[int, float]) -> str:
    """Serialize validated disabled cohorts into deterministic canonical JSON."""
    validated = parse_disabled_cohorts(cohorts)
    ordered = {str(duration): validated[duration] for duration in sorted(validated)}
    return json.dumps(ordered, separators=(",", ":"), sort_keys=False)


def advance_disabled_cohorts(
    cohorts_open: Mapping[int, float],
    q_term_m: float,
    q_mort_disabled_m: float,
) -> dict[int, float]:
    """Advance each existing disabled cohort by +1 month under termination and mortality."""
    cohorts = parse_disabled_cohorts(cohorts_open)
    q_term = _probability("q_term_m", q_term_m)
    q_mort_disabled = _probability("q_mort_disabled_m", q_mort_disabled_m)
    retention = (1.0 - q_term) * (1.0 - q_mort_disabled)
    if retention == 0.0:
        return {}
    surviving: dict[int, float] = {}
    for duration, prob in sorted(cohorts.items()):
        next_prob = prob * retention
        if next_prob > 0.0:
            surviving[duration + 1] = next_prob
    return surviving


def idi_cohorts_benefit_payable(
    cohorts_surviving: Mapping[int, float],
    elimination_months: int,
    max_benefit_months: int,
    monthly_benefit: float,
) -> float:
    """Return total expected EOM benefit across all surviving disabled duration cohorts."""
    cohorts = parse_disabled_cohorts(cohorts_surviving)
    elimination = _nonnegative_integer("elimination_months", elimination_months)
    maximum = _nonnegative_integer("max_benefit_months", max_benefit_months)
    if maximum == 0:
        raise ValueError("max_benefit_months must be positive.")
    benefit = _nonnegative("monthly_benefit", monthly_benefit)
    return math.fsum(
        idi_benefit_payable(duration, elimination, maximum, benefit, prob)
        for duration, prob in sorted(cohorts.items())
    )


def idi_markov_step(
    *,
    p_active_open: float,
    p_disabled_open: float | None = None,
    q_mort_active_m: float,
    q_lapse_m: float,
    q_inc_m: float,
    q_term_m: float,
    q_mort_disabled_m: float,
    claim_months_elapsed: int = 0,
    elimination_months: int,
    max_benefit_months: int,
    monthly_benefit: float,
    monthly_premium: float,
    expense_per_policy_monthly: float = 0.0,
    waive_premium_on_disability: bool = True,
    new_claim_dlr_factor: float = 0.0,
    disabled_cohorts_open: Mapping[int, float] | None = None,
    new_claim_treatment: str = "cohort_runoff",
) -> dict[str, Any]:
    """Advance illustrative active/disabled states one month using EOM timing.

    Existing disabled lives are tracked by exact claim duration in
    ``disabled_cohorts_open`` (or initialized from ``p_disabled_open`` and
    ``claim_months_elapsed``).

    Active decrements are mutually exclusive additive probabilities.
    Existing disabled cohorts experience sequential recovery/termination
    followed by disabled mortality, advancing each cohort's duration by ``+1``
    month independently.

    ``new_claim_treatment`` must be one of:
    - ``"cohort_runoff"`` (Option A): requires ``new_claim_dlr_factor == 0.0``.
      New incident claims enter ``disabled_cohorts_close`` at duration ``0``
      and pay duration-gated monthly benefits in subsequent months.
    - ``"dlr_incurred"`` (Option B): new incident claims are valued at
      incidence via ``new_disabled * monthly_benefit * new_claim_dlr_factor``
      and are NOT added to ``disabled_cohorts_close`` (preventing double
      counting).
    """
    if new_claim_treatment not in {"cohort_runoff", "dlr_incurred"}:
        raise ValueError("new_claim_treatment must be 'cohort_runoff' or 'dlr_incurred'.")

    active = _probability("p_active_open", p_active_open)
    elapsed_open = _nonnegative_integer("claim_months_elapsed", claim_months_elapsed)
    if disabled_cohorts_open is not None:
        cohorts_open = parse_disabled_cohorts(disabled_cohorts_open)
        disabled = math.fsum(cohorts_open.values())
        if p_disabled_open is not None:
            explicit_disabled = _probability("p_disabled_open", p_disabled_open)
            if not math.isclose(explicit_disabled, disabled, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(
                    "p_disabled_open must equal the sum of disabled_cohorts_open probabilities."
                )
    else:
        disabled = _probability("p_disabled_open", 0.0 if p_disabled_open is None else p_disabled_open)
        cohorts_open = {elapsed_open: disabled} if disabled > 0.0 else {}

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

    if new_claim_treatment == "cohort_runoff" and dlr_factor > 0.0:
        raise ValueError(
            "Cannot combine new_claim_treatment='cohort_runoff' with positive "
            "new_claim_dlr_factor (would double-count newly incident claims)."
        )

    new_disabled = active * q_inc
    active_close = active * (1.0 - active_decrements)
    cohorts_surviving = advance_disabled_cohorts(cohorts_open, q_term, q_mort_disabled)
    disabled_surviving = math.fsum(cohorts_surviving.values())

    expected_benefit = idi_cohorts_benefit_payable(
        cohorts_surviving,
        elimination,
        maximum,
        benefit,
    )

    cohorts_close = dict(cohorts_surviving)
    if new_claim_treatment == "cohort_runoff":
        if new_disabled > 0.0:
            cohorts_close[0] = cohorts_close.get(0, 0.0) + new_disabled
        disabled_close = math.fsum(cohorts_close.values())
        expected_new_claim = 0.0
    else:
        # Option B ("dlr_incurred"): newly incident claims are settled at incidence
        # via the DLR factor and do NOT enter benefit-paying disabled cohorts.
        disabled_close = disabled_surviving
        expected_new_claim = new_disabled * benefit * dlr_factor

    claim_elapsed_close = max(cohorts_close) if cohorts_close else 0
    premium_weight = active_close if waive_premium_on_disability else active_close + disabled_close
    expected_premium = premium_weight * premium
    expected_expense = (active_close + disabled_close) * expense

    return {
        "active_prob_close": active_close,
        "disabled_prob_close": disabled_close,
        "new_disabled": new_disabled,
        "disabled_surviving": disabled_surviving,
        "disabled_cohorts_surviving": cohorts_surviving,
        "disabled_cohorts_close": cohorts_close,
        "new_claim_treatment": new_claim_treatment,
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
