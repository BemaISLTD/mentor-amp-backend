"""Pure illustrative pension-risk-transfer benefit primitives."""

from __future__ import annotations

import math


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


def non_termination_step(
    p_stay_open: float,
    optional_termination_rate_monthly: float,
) -> float:
    """Advance cumulative pre-commencement non-termination survival."""
    stay = _probability("p_stay_open", p_stay_open)
    termination = _probability(
        "optional_termination_rate_monthly", optional_termination_rate_monthly
    )
    return stay * (1.0 - termination)


def prt_monthly_benefit_factor(
    participant_status: str,
    projection_month: int,
    deferral_months: int,
    certain_months: int,
    form_of_annuity: str,
    joint_survivor_pct: float,
    annuitant_survival_prob: float,
    non_termination_survival_prob: float = 1.0,
    spouse_survival_prob: float | None = None,
) -> float:
    """Return the exact expected fraction of monthly pension benefit payable."""
    month = _nonnegative_integer("projection_month", projection_month)
    if month == 0:
        raise ValueError("projection_month must be positive.")
    deferral = _nonnegative_integer("deferral_months", deferral_months)
    certain = _nonnegative_integer("certain_months", certain_months)
    annuitant = _probability("annuitant_survival_prob", annuitant_survival_prob)
    stay = _probability("non_termination_survival_prob", non_termination_survival_prob)
    joint_pct = _probability("joint_survivor_pct", joint_survivor_pct)

    if participant_status == "deferred":
        if deferral == 0:
            raise ValueError("Deferred participants require positive deferral_months.")
        if month <= deferral:
            return 0.0
        months_since_commencement = month - deferral
    elif participant_status == "in_pay":
        if deferral != 0:
            raise ValueError("In-pay participants require deferral_months == 0.")
        if not math.isclose(stay, 1.0, abs_tol=1e-12):
            raise ValueError("In-pay participants require non-termination survival of 1.0.")
        months_since_commencement = month
    else:
        raise ValueError(f"Unsupported participant_status '{participant_status}'.")

    if form_of_annuity == "life_only":
        raw_factor = annuitant
    elif form_of_annuity == "certain_and_life":
        if certain == 0:
            raise ValueError("certain_and_life requires positive certain_months.")
        raw_factor = 1.0 if months_since_commencement <= certain else annuitant
    elif form_of_annuity == "joint_survivor":
        if spouse_survival_prob is None:
            raise ValueError("joint_survivor requires spouse_survival_prob.")
        spouse = _probability("spouse_survival_prob", spouse_survival_prob)
        if joint_pct <= 0.0:
            raise ValueError("joint_survivor_pct must be positive for joint_survivor.")
        raw_factor = annuitant + joint_pct * (1.0 - annuitant) * spouse
    else:
        raise ValueError(f"Unsupported form_of_annuity '{form_of_annuity}'.")
    return stay * raw_factor


def prt_escalated_payment(
    base_monthly_benefit: float,
    policy_year: int,
    cola_rate: float,
    cola_cap: float | None = None,
) -> float:
    """Apply capped annual compound COLA to a monthly pension benefit."""
    base = _nonnegative("base_monthly_benefit", base_monthly_benefit)
    if isinstance(policy_year, bool) or not isinstance(policy_year, int) or policy_year < 1:
        raise ValueError("policy_year must be a positive integer.")
    cola = _nonnegative("cola_rate", cola_rate)
    if cola_cap is not None:
        cola = min(cola, _nonnegative("cola_cap", cola_cap))
    return base * (1.0 + cola) ** (policy_year - 1)
