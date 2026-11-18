"""Pure illustrative variable-annuity account and guarantee primitives."""

from __future__ import annotations

import math


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite.")
    return value


def _nonnegative(name: str, value: float) -> float:
    value = _finite(name, value)
    if value < 0.0:
        raise ValueError(f"{name} must be non-negative.")
    return value


def _probability(name: str, value: float) -> float:
    value = _finite(name, value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1].")
    return value


def monthly_fee_rate(annual_fee: float, fee_conversion_method: str) -> float:
    """Convert an annual fee using the explicitly selected convention."""
    fee = _nonnegative("annual_fee", annual_fee)
    if fee_conversion_method == "simple_12th":
        return fee / 12.0
    if fee_conversion_method == "effective_monthly_rate":
        return (1.0 + fee) ** (1.0 / 12.0) - 1.0
    if fee_conversion_method == "constant_force_deduction":
        if fee >= 1.0:
            raise ValueError("annual_fee must be below 1 for constant-force deduction.")
        return 1.0 - (1.0 - fee) ** (1.0 / 12.0)
    raise ValueError(f"Unsupported fee_conversion_method '{fee_conversion_method}'.")


def va_subaccount_rollforward(
    *,
    eq_av_open: float,
    bond_av_open: float,
    fixed_av_open: float,
    eq_return: float,
    bond_return: float,
    fixed_rate: float,
    me_fee_rate: float,
    admin_fee_rate: float,
    rider_fee_rate: float,
    fee_conversion_method: str,
    gross_withdrawal: float,
    benefit_base: float = 0.0,
    rider_fee_basis: str = "account_value",
) -> dict[str, float]:
    """Apply growth, capped fees, and withdrawal in an explicit EOM waterfall."""
    openings = {
        "eq": _nonnegative("eq_av_open", eq_av_open),
        "bond": _nonnegative("bond_av_open", bond_av_open),
        "fixed": _nonnegative("fixed_av_open", fixed_av_open),
    }
    returns = {
        "eq": _finite("eq_return", eq_return),
        "bond": _finite("bond_return", bond_return),
        "fixed": _finite("fixed_rate", fixed_rate),
    }
    if any(value <= -1.0 for value in returns.values()):
        raise ValueError("Subaccount returns must be greater than -1.0.")
    withdrawal = _nonnegative("gross_withdrawal", gross_withdrawal)
    base = _nonnegative("benefit_base", benefit_base)
    if rider_fee_basis not in {"account_value", "benefit_base"}:
        raise ValueError("rider_fee_basis must be 'account_value' or 'benefit_base'.")

    grown = {name: openings[name] * (1.0 + returns[name]) for name in openings}
    av_after_growth = sum(grown.values())
    asset_fee_rate = monthly_fee_rate(me_fee_rate, fee_conversion_method) + monthly_fee_rate(
        admin_fee_rate, fee_conversion_method
    )
    rider_basis_amount = av_after_growth if rider_fee_basis == "account_value" else base
    fee_due = av_after_growth * asset_fee_rate + rider_basis_amount * monthly_fee_rate(
        rider_fee_rate, fee_conversion_method
    )
    fee_collected = min(av_after_growth, fee_due)
    uncollected_fee = fee_due - fee_collected
    av_after_fees = av_after_growth - fee_collected
    av_paid_withdrawal = min(av_after_fees, withdrawal)
    av_close = av_after_fees - av_paid_withdrawal

    retained_ratio = av_close / av_after_growth if av_after_growth > 0.0 else 0.0
    closes = {name: amount * retained_ratio for name, amount in grown.items()}
    return {
        "eq_av_after_growth": grown["eq"],
        "bond_av_after_growth": grown["bond"],
        "fixed_av_after_growth": grown["fixed"],
        "account_value_after_growth": av_after_growth,
        "fee_due": fee_due,
        "fee_collected": fee_collected,
        "uncollected_fee": uncollected_fee,
        "account_value_after_fees": av_after_fees,
        "av_paid_withdrawal": av_paid_withdrawal,
        "rider_shortfall_raw": withdrawal - av_paid_withdrawal,
        "av_exhausted": float(av_close == 0.0 and withdrawal > 0.0),
        "eq_av_close": closes["eq"],
        "bond_av_close": closes["bond"],
        "fixed_av_close": closes["fixed"],
        "account_value_close": av_close,
    }


def va_guarantee_step(
    *,
    rider_type: str,
    benefit_base_open: float,
    gmwb_remaining_open: float,
    gmdb_base_open: float,
    account_value_before_withdrawal: float,
    account_value_close: float,
    av_paid_withdrawal: float,
    gross_withdrawal: float,
    gwb_pct: float,
    rollup_rate: float = 0.0,
    projection_month: int = 1,
    survival_open: float = 1.0,
    withdrawal_mode: str = "pro_rata",
    ratchet_enabled: bool = True,
) -> dict[str, float]:
    """Apply distinct finite GMWB, lifetime GLWB, and GMDB mechanics."""
    if rider_type not in {"NONE", "GMWB", "GLWB", "GMDB_ROP", "GMDB_RATCHET"}:
        raise ValueError(f"Unsupported VA rider_type '{rider_type}'.")
    if withdrawal_mode not in {"dollar_for_dollar", "pro_rata"}:
        raise ValueError("Unsupported withdrawal_mode.")
    if isinstance(projection_month, bool) or not isinstance(projection_month, int) or projection_month < 1:
        raise ValueError("projection_month must be a positive integer.")
    if not isinstance(ratchet_enabled, bool):
        raise ValueError("ratchet_enabled must be boolean.")

    benefit_base = _nonnegative("benefit_base_open", benefit_base_open)
    gmwb_remaining = _nonnegative("gmwb_remaining_open", gmwb_remaining_open)
    gmdb_base = _nonnegative("gmdb_base_open", gmdb_base_open)
    av_before = _nonnegative("account_value_before_withdrawal", account_value_before_withdrawal)
    av_close = _nonnegative("account_value_close", account_value_close)
    av_paid = _nonnegative("av_paid_withdrawal", av_paid_withdrawal)
    withdrawal = _nonnegative("gross_withdrawal", gross_withdrawal)
    gwb = _nonnegative("gwb_pct", gwb_pct)
    rollup = _nonnegative("rollup_rate", rollup_rate)
    survival = _probability("survival_open", survival_open)
    if av_paid > withdrawal + 1e-12:
        raise ValueError("av_paid_withdrawal cannot exceed gross_withdrawal.")

    if rider_type == "NONE":
        return {
            "guaranteed_withdrawal_paid": 0.0,
            "rider_claim_payment": 0.0,
            "benefit_base_close": 0.0,
            "gmwb_remaining_close": 0.0,
            "gmdb_base_close": 0.0,
            "monthly_income_allowance": 0.0,
        }

    rider_claim = 0.0
    guaranteed = 0.0
    monthly_allowance = benefit_base * gwb / 12.0
    benefit_base_close = benefit_base
    gmwb_close = gmwb_remaining
    gmdb_close = gmdb_base

    if rider_type == "GMWB":
        guaranteed = min(withdrawal, gmwb_remaining, monthly_allowance)
        rider_claim = max(0.0, guaranteed - av_paid) if survival > 0.0 else 0.0
        gmwb_close = max(0.0, gmwb_remaining - guaranteed)
        benefit_base_close = max(0.0, benefit_base - guaranteed)
    elif rider_type == "GLWB":
        if withdrawal == 0.0:
            benefit_base_close *= (1.0 + rollup) ** (1.0 / 12.0)
        if ratchet_enabled and projection_month % 12 == 0:
            benefit_base_close = max(benefit_base_close, av_before)
        monthly_allowance = benefit_base_close * gwb / 12.0
        guaranteed = min(withdrawal, monthly_allowance)
        rider_claim = max(0.0, guaranteed - av_paid) if survival > 0.0 else 0.0
        excess = max(0.0, withdrawal - monthly_allowance)
        av_before_excess = max(0.0, av_before - monthly_allowance)
        if excess > 0.0:
            benefit_base_close = (
                benefit_base_close * max(0.0, 1.0 - excess / av_before_excess)
                if av_before_excess > 0.0
                else 0.0
            )
        gmwb_close = 0.0
    else:
        if rider_type == "GMDB_RATCHET" and ratchet_enabled and projection_month % 12 == 0:
            gmdb_close = max(gmdb_close, av_before)
        if withdrawal_mode == "dollar_for_dollar":
            gmdb_close = max(0.0, gmdb_close - withdrawal)
        else:
            gmdb_close = gmdb_close * max(0.0, 1.0 - withdrawal / av_before) if av_before else 0.0

    return {
        "guaranteed_withdrawal_paid": guaranteed,
        "rider_claim_payment": rider_claim,
        "benefit_base_close": benefit_base_close,
        "gmwb_remaining_close": gmwb_close,
        "gmdb_base_close": gmdb_close,
        "monthly_income_allowance": monthly_allowance,
    }


def va_death_benefit(
    account_value_close: float,
    gmdb_base: float,
    rider_type: str,
) -> dict[str, float]:
    """Return total death benefit and insurer net-amount-at-risk claim."""
    account_value = _nonnegative("account_value_close", account_value_close)
    base = _nonnegative("gmdb_base", gmdb_base)
    if rider_type in {"GMDB_ROP", "GMDB_RATCHET"}:
        total = max(account_value, base)
    elif rider_type in {"NONE", "GMWB", "GLWB"}:
        total = account_value
    else:
        raise ValueError(f"Unsupported VA rider_type '{rider_type}'.")
    return {"total_death_benefit": total, "nar_death_claim": max(0.0, total - account_value)}


def va_total_death_benefit(account_value_close: float, gmdb_base: float, rider_type: str) -> float:
    return va_death_benefit(account_value_close, gmdb_base, rider_type)["total_death_benefit"]


def va_nar_death_claim(account_value_close: float, gmdb_base: float, rider_type: str) -> float:
    return va_death_benefit(account_value_close, gmdb_base, rider_type)["nar_death_claim"]
