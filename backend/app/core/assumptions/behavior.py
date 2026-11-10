"""Illustrative policyholder-behavior models for architecture validation only.

Nothing in this module is a prescribed, regulatory, or production-calibrated assumption.
Parameters must be governed and approved before use in a production valuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import Any

import numpy as np


class BehaviorModelError(ValueError):
    """Invalid behavioral-model input or parameter."""


@dataclass(frozen=True)
class IllustrativeLapseSchedule:
    """Duration lapse schedule with an explicit surrender-expiry shock.

    ``early_duration_rates`` contains one annual rate for each duration in the
    surrender-charge period.  The shock occurs exactly in the following duration,
    when the surrender charge has expired.  Later durations grade from
    ``post_shock_rate`` to ``ultimate_rate``.
    """

    early_duration_rates: tuple[float, ...]
    expiration_shock_rate: float
    post_shock_rate: float
    ultimate_rate: float
    grading_years: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.early_duration_rates, (str, bytes)):
            raise BehaviorModelError("Early-duration lapse rates must be a numerical sequence.")
        try:
            frozen_rates = tuple(float(rate) for rate in self.early_duration_rates)
        except (TypeError, ValueError) as exc:
            raise BehaviorModelError(
                "Early-duration lapse rates must be a numerical sequence."
            ) from exc
        object.__setattr__(self, "early_duration_rates", frozen_rates)
        if not self.early_duration_rates:
            raise BehaviorModelError("At least one surrender-period lapse rate is required.")
        rates = (
            *self.early_duration_rates,
            self.expiration_shock_rate,
            self.post_shock_rate,
            self.ultimate_rate,
        )
        if any(not np.isfinite(rate) or not 0.0 <= rate <= 1.0 for rate in rates):
            raise BehaviorModelError("All lapse rates must be finite and in [0, 1].")
        if (
            isinstance(self.grading_years, bool)
            or not isinstance(self.grading_years, Integral)
            or self.grading_years < 1
        ):
            raise BehaviorModelError("grading_years must be a positive integer.")

    @property
    def surrender_period_years(self) -> int:
        return len(self.early_duration_rates)

    @property
    def shock_duration(self) -> int:
        return self.surrender_period_years + 1


def baseline_lapse_rate(
    duration: int | np.ndarray,
    schedule: IllustrativeLapseSchedule,
) -> float | np.ndarray:
    """Return the illustrative annual baseline lapse for policy duration(s)."""

    raw = np.asarray(duration)
    if np.issubdtype(raw.dtype, np.bool_):
        raise BehaviorModelError("Policy duration must be a positive integer.")
    try:
        numeric = raw.astype(np.float64)
    except (TypeError, ValueError) as exc:
        raise BehaviorModelError("Policy duration must be numeric.") from exc
    if np.any(~np.isfinite(numeric)) or np.any(numeric < 1) or np.any(numeric != np.floor(numeric)):
        raise BehaviorModelError("Policy duration must be a positive integer.")
    years = numeric.astype(np.int64)
    result = np.empty(years.shape, dtype=np.float64)

    early = np.asarray(schedule.early_duration_rates, dtype=np.float64)
    in_early = years <= schedule.surrender_period_years
    result[in_early] = early[years[in_early] - 1]

    shock = years == schedule.shock_duration
    result[shock] = schedule.expiration_shock_rate

    later = years > schedule.shock_duration
    if np.any(later):
        elapsed = years[later] - schedule.shock_duration
        progress = np.minimum(elapsed / schedule.grading_years, 1.0)
        result[later] = schedule.post_shock_rate + progress * (
            schedule.ultimate_rate - schedule.post_shock_rate
        )

    if raw.ndim == 0:
        return float(result)
    return result


def dynamic_lapse_multiplier(
    market_rate: float | np.ndarray,
    contract_rate: float | np.ndarray,
    *,
    buffer: float,
    threshold: float,
    alpha: float,
    beta: float,
) -> float | np.ndarray:
    """Calculate the illustrative competitor-rate lapse multiplier.

    ``1 + alpha * (max(0, market-contract-buffer) / threshold) ** beta``
    """

    parameters = np.asarray([buffer, threshold, alpha, beta], dtype=np.float64)
    if np.any(~np.isfinite(parameters)):
        raise BehaviorModelError("Dynamic-lapse parameters must be finite.")
    if buffer < 0.0 or threshold <= 0.0 or alpha < 0.0 or beta <= 0.0:
        raise BehaviorModelError(
            "buffer and alpha must be non-negative; threshold and beta must be positive."
        )
    try:
        market, contract = np.broadcast_arrays(
            np.asarray(market_rate, dtype=np.float64),
            np.asarray(contract_rate, dtype=np.float64),
        )
    except (TypeError, ValueError) as exc:
        raise BehaviorModelError("Market and contract rates must be broadcast-compatible.") from exc
    if np.any(~np.isfinite(market)) or np.any(~np.isfinite(contract)):
        raise BehaviorModelError("Market and contract rates must be finite.")
    spread = np.maximum(0.0, market - contract - buffer)
    result = 1.0 + alpha * np.power(spread / threshold, beta)
    if result.ndim == 0:
        return float(result)
    return result


def effective_lapse_rate(
    base_lapse: float | np.ndarray,
    market_rate: float | np.ndarray,
    contract_rate: float | np.ndarray,
    *,
    buffer: float,
    threshold: float,
    alpha: float,
    beta: float,
    max_lapse_rate: float,
) -> float | np.ndarray:
    """Apply the dynamic multiplier and governed lapse-rate ceiling."""

    if not np.isfinite(max_lapse_rate) or not 0.0 <= max_lapse_rate <= 1.0:
        raise BehaviorModelError("max_lapse_rate must be finite and in [0, 1].")
    base = np.asarray(base_lapse, dtype=np.float64)
    if np.any(~np.isfinite(base)) or np.any((base < 0.0) | (base > 1.0)):
        raise BehaviorModelError("base_lapse must be finite and in [0, 1].")
    multiplier = np.asarray(dynamic_lapse_multiplier(
        market_rate,
        contract_rate,
        buffer=buffer,
        threshold=threshold,
        alpha=alpha,
        beta=beta,
    ))
    try:
        base, multiplier = np.broadcast_arrays(base, multiplier)
    except ValueError as exc:
        raise BehaviorModelError("Behavioral inputs must be broadcast-compatible.") from exc
    result = np.minimum(max_lapse_rate, base * multiplier)
    if result.ndim == 0:
        return float(result)
    return result
