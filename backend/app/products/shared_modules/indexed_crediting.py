"""Pure indexed-crediting calculations shared by FIA and RILA models."""

import math


def _validate_rate(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _normalize(value: float) -> float:
    return 0.0 if abs(value) < 1e-12 else value


def ptp_cap_pr_crediting(
    index_return: float,
    cap: float,
    participation_rate: float = 1.0,
    floor: float = 0.0,
) -> float:
    """Return a point-to-point indexed credit with a cap, participation and floor."""
    for name, value in (("index_return", index_return), ("cap", cap),
                        ("participation_rate", participation_rate), ("floor", floor)):
        _validate_rate(name, value)
    if cap < floor or participation_rate < 0:
        raise ValueError("cap must be at least floor and participation_rate cannot be negative")
    return _normalize(max(floor, min(cap, participation_rate * index_return)))


def ptp_spread_crediting(index_return: float, spread: float, floor: float = 0.0) -> float:
    """Return a point-to-point indexed credit after a deductible spread."""
    for name, value in (("index_return", index_return), ("spread", spread), ("floor", floor)):
        _validate_rate(name, value)
    return _normalize(max(floor, index_return - spread))


def monthly_average_return(monthly_index_levels: list[float], start_index_level: float) -> float:
    """Return the arithmetic-average index return for the supplied observations."""
    if not monthly_index_levels or not math.isfinite(start_index_level) or start_index_level <= 0:
        raise ValueError("Invalid index levels for monthly averaging")
    if any(not math.isfinite(level) or level <= 0 for level in monthly_index_levels):
        raise ValueError("Index levels must be finite and positive")
    return _normalize((sum(monthly_index_levels) / len(monthly_index_levels)
                       - start_index_level) / start_index_level)