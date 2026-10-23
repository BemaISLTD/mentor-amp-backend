"""Actuarial decrement methodology boundary for MYGA valuation.

Separates contract mechanics (account value rollforward, GMSV accumulation, surrender
charges, MVA settlement) from behavioral and actuarial decrement assumptions
(mortality, base lapses, dynamic lapse sensitivity, and annuitization election rates).

Allows swappable methodologies such as:
- IllustrativeBestEstimateDecrements (current baseline)
- VM22PrescribedDecrements (future statutory framework)
- PricingDecrements / ExperienceDecrements
without modifying contract formula logic.
"""

from __future__ import annotations

import contextlib
import contextvars
import math
from typing import Any, Protocol, runtime_checkable


class DecrementMethodology(Protocol):
    """Contract specification for swappable decrement providers."""

    def get_monthly_mortality(self, mortality_rate_annual: float) -> float:
        """Convert annual mortality to equivalent monthly probability."""
        ...

    def get_base_lapse_rate(self, policy_year: float, guarantee_period_years: float) -> float:
        """Annual baseline lapse rate by policy duration."""
        ...

    def get_gmir_lapse_factor(self, gmir: float) -> float:
        """GMIR floor sensitivity adjustment multiplier."""
        ...

    def get_annual_lapse_rate(
        self,
        base_lapse_rate: float,
        gmir_lapse_factor: float,
        dynamic_lapse_rate: float,
        mva_lapse_factor_at: float,
    ) -> float:
        """Total annual lapse rate combining base and dynamic adjustments."""
        ...

    def get_monthly_lapse_rate(self, annual_lapse_rate: float) -> float:
        """Convert annual lapse rate to equivalent monthly probability."""
        ...

    def get_annuitization_rate(self, renewal_window: float) -> float:
        """Monthly annuitization election rate."""
        ...


class IllustrativeBestEstimateDecrements:
    """Current default illustrative best-estimate decrement methodology."""

    def get_monthly_mortality(self, mortality_rate_annual: float) -> float:
        rate = min(1.0, max(0.0, float(mortality_rate_annual)))
        return 1.0 - (1.0 - rate) ** (1.0 / 12.0)

    def get_base_lapse_rate(self, policy_year: float, guarantee_period_years: float) -> float:
        year = int(policy_year)
        period = int(guarantee_period_years)
        if period < 1:
            raise ValueError("guarantee period must be positive")
        return 0.75 if year > period and (year - 1) % period == 0 else 0.01

    def get_gmir_lapse_factor(self, gmir: float) -> float:
        g = float(gmir)
        if g <= 0.01:
            return 1.25
        if g <= 0.025:
            return 1.0
        return 0.70

    def get_annual_lapse_rate(
        self,
        base_lapse_rate: float,
        gmir_lapse_factor: float,
        dynamic_lapse_rate: float,
        mva_lapse_factor_at: float,
    ) -> float:
        value = (
            float(base_lapse_rate) * float(gmir_lapse_factor)
            + float(dynamic_lapse_rate) * float(mva_lapse_factor_at)
        )
        return min(0.90, max(0.005, value))

    def get_monthly_lapse_rate(self, annual_lapse_rate: float) -> float:
        value = min(1.0, max(0.0, float(annual_lapse_rate)))
        return 1.0 - (1.0 - value) ** (1.0 / 12.0)

    def get_annuitization_rate(self, renewal_window: float) -> float:
        return 0.01 if float(renewal_window) >= 0.5 else 0.0


class BenchmarkZeroDecrements:
    """Zero decrements methodology for pure contract mechanics reference benchmarks."""

    def get_monthly_mortality(self, mortality_rate_annual: float) -> float:
        return 0.0

    def get_base_lapse_rate(self, policy_year: float, guarantee_period_years: float) -> float:
        return 0.0

    def get_gmir_lapse_factor(self, gmir: float) -> float:
        return 0.0

    def get_annual_lapse_rate(
        self,
        base_lapse_rate: float,
        gmir_lapse_factor: float,
        dynamic_lapse_rate: float,
        mva_lapse_factor_at: float,
    ) -> float:
        return 0.0

    def get_monthly_lapse_rate(self, annual_lapse_rate: float) -> float:
        return 0.0

    def get_annuitization_rate(self, renewal_window: float) -> float:
        return 0.0


# Run-scoped execution context variable — NOT a process-global singleton.
# Each thread, task, or execution run maintains its own decrement context.
_EXECUTION_METHODOLOGY_CV: contextvars.ContextVar[DecrementMethodology | None] = (
    contextvars.ContextVar("myga_decrement_methodology", default=None)
)


def resolve_decrement_methodology(
    methodology: DecrementMethodology | str | None,
) -> DecrementMethodology:
    """Resolve a decrement methodology from an instance, identifier string, or None."""
    if methodology is None:
        return IllustrativeBestEstimateDecrements()
    if isinstance(methodology, str):
        normalized = methodology.strip().lower().replace("-", "_")
        if normalized in {"illustrative_best_estimate", "best_estimate", "default"}:
            return IllustrativeBestEstimateDecrements()
        if normalized in {"benchmark_zero_decrements", "zero_decrements", "zero"}:
            return BenchmarkZeroDecrements()
        raise ValueError(f"Unknown decrement methodology identifier: '{methodology}'")
    return methodology


def get_current_decrement_methodology() -> DecrementMethodology:
    """Return the active decrement methodology provider for the current execution run/context."""
    active = _EXECUTION_METHODOLOGY_CV.get()
    if active is not None:
        return active
    return IllustrativeBestEstimateDecrements()


def set_execution_decrement_methodology(
    methodology: DecrementMethodology | str | None,
) -> contextvars.Token:
    """Set the decrement methodology in the current execution context and return the token."""
    resolved = resolve_decrement_methodology(methodology)
    return _EXECUTION_METHODOLOGY_CV.set(resolved)


def reset_execution_decrement_methodology(token: contextvars.Token) -> None:
    """Reset the execution context decrement methodology using the provided token."""
    _EXECUTION_METHODOLOGY_CV.reset(token)


@contextlib.contextmanager
def decrement_scope(methodology: DecrementMethodology | str | None):
    """Context manager to scope a decrement methodology to a block of code."""
    token = set_execution_decrement_methodology(methodology)
    try:
        yield get_current_decrement_methodology()
    finally:
        reset_execution_decrement_methodology(token)


def set_decrement_methodology(methodology: DecrementMethodology | str) -> None:
    """Context-safe setter for test fixtures or local scope (sets contextvar, not process global)."""
    set_execution_decrement_methodology(methodology)


def reset_decrement_methodology() -> None:
    """Reset the active context decrement methodology to default."""
    _EXECUTION_METHODOLOGY_CV.set(None)
