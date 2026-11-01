"""Downside-protection payoffs shared by RILA strategies."""

import math


def _normalize(value: float) -> float:
    return 0.0 if abs(value) < 1e-12 else value


def buffer_payoff(index_return: float, cap: float, buffer_rate: float,
                  participation_rate: float = 1.0) -> float:
    """Apply a buffer to losses and a cap to gains."""
    if not all(math.isfinite(v) for v in
               (index_return, cap, buffer_rate, participation_rate)):
        raise ValueError("Payoff inputs must be finite")
    if cap < 0 or not 0 <= buffer_rate <= 1 or participation_rate < 0:
        raise ValueError("cap/participation must be nonnegative and buffer must lie in [0, 1]")
    if index_return >= 0:
        return _normalize(min(cap, participation_rate * index_return))
    if index_return >= -buffer_rate or math.isclose(index_return, -buffer_rate,
                                                     rel_tol=0.0, abs_tol=1e-12):
        return 0.0
    return _normalize(index_return + buffer_rate)


def floor_payoff(index_return: float, cap: float, floor_rate: float,
                 participation_rate: float = 1.0) -> float:
    """Apply a floor to losses and a cap to gains."""
    if not all(math.isfinite(v) for v in
               (index_return, cap, floor_rate, participation_rate)):
        raise ValueError("Payoff inputs must be finite")
    if cap < 0 or not 0 <= floor_rate <= 1 or participation_rate < 0:
        raise ValueError("cap/participation must be nonnegative and floor must lie in [0, 1]")
    if index_return >= 0:
        return _normalize(min(cap, participation_rate * index_return))
    return _normalize(max(-floor_rate, index_return))
