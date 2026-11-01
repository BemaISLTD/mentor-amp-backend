"""Serializable multi-segment state and rollforward primitives."""

from dataclasses import dataclass, replace
import math

from .buffer_floor import buffer_payoff, floor_payoff
from .withdrawal import apply_partial_withdrawal
from .indexed_crediting import ptp_cap_pr_crediting, ptp_spread_crediting


@dataclass(frozen=True)
class SegmentState:
    segment_id: str
    strategy_type: str
    index_code: str
    term_months: int
    elapsed_months: int
    start_index_level: float
    base_balance: float
    current_interim_value: float
    cap: float
    downside_rate: float
    is_matured: bool = False
    participation_rate: float = 1.0
    spread: float = 0.0
    swept_value: float = 0.0
    credited_rate: float = 0.0


def rollforward_segment_term(segment: SegmentState, current_index_level: float,
                             withdrawal_amount: float = 0.0,
                             withdrawal_mode: str = "pro_rata", *,
                             interim_method: str,
                             rollover_rule: str,
                             external_interim_value: float | None = None) -> SegmentState:
    """Advance, value, then withdraw. Prorated intrinsic is illustrative, not AG54.

    External interim values are pre-withdrawal. Auto-renewal starts next month
    at the maturity observation; a sweep is emitted once into the fixed account.
    """
    if not all(math.isfinite(v) for v in (
        current_index_level, segment.start_index_level, segment.base_balance,
        segment.current_interim_value, segment.cap, segment.downside_rate,
        segment.participation_rate, segment.spread, withdrawal_amount,
    )):
        raise ValueError("Segment inputs must be finite")
    if (segment.term_months <= 0 or int(segment.term_months) != segment.term_months
            or not 0 <= segment.elapsed_months <= segment.term_months
            or int(segment.elapsed_months) != segment.elapsed_months):
        raise ValueError("Segment term and elapsed months are invalid")
    if segment.is_matured != (segment.elapsed_months == segment.term_months):
        raise ValueError("Maturity flag must agree with elapsed months")
    if segment.start_index_level <= 0 or current_index_level <= 0:
        raise ValueError("Index levels must be positive")
    if min(segment.base_balance, segment.current_interim_value, withdrawal_amount) < 0:
        raise ValueError("Segment balances cannot be negative")
    if interim_method not in {"prorated_intrinsic", "external"}:
        raise ValueError("Unsupported interim method")
    if rollover_rule not in {"auto_renew", "sweep_to_fixed"}:
        raise ValueError("Unsupported rollover rule")
    if withdrawal_mode not in {"pro_rata", "dollar_for_dollar"}:
        raise ValueError("Unsupported withdrawal mode")
    if segment.is_matured:
        if rollover_rule == "sweep_to_fixed":
            if withdrawal_amount:
                raise ValueError("Cannot withdraw from a swept segment")
            return replace(segment, swept_value=0.0, credited_rate=0.0)
        segment = replace(segment, base_balance=segment.current_interim_value,
                          elapsed_months=0, is_matured=False)
    index_return = current_index_level / segment.start_index_level - 1
    strategy = segment.strategy_type.strip().lower()
    if strategy == "floor":
        credited_rate = floor_payoff(index_return, segment.cap, segment.downside_rate,
                                     segment.participation_rate)
    elif strategy == "buffer":
        credited_rate = buffer_payoff(index_return, segment.cap, segment.downside_rate,
                                      segment.participation_rate)
    elif strategy == "ptp":
        credited_rate = ptp_cap_pr_crediting(index_return, segment.cap, segment.participation_rate)
    elif strategy == "spread":
        credited_rate = min(segment.cap, ptp_spread_crediting(index_return, segment.spread))
    else:
        raise ValueError(f"Unsupported segment strategy_type: {segment.strategy_type}")
    elapsed = segment.elapsed_months + 1
    matured = elapsed == segment.term_months
    if matured:
        value = segment.base_balance * (1 + credited_rate)
    elif interim_method == "prorated_intrinsic":
        value = segment.base_balance * (1 + credited_rate * elapsed / segment.term_months)
    else:
        if external_interim_value is None or not math.isfinite(external_interim_value):
            raise ValueError("External interim value is required and must be finite")
        value = external_interim_value
    if value < 0:
        raise ValueError("Segment value cannot be negative")
    base, value = apply_partial_withdrawal(
        segment.base_balance, value, withdrawal_amount, withdrawal_mode)
    sweep = value if matured and rollover_rule == "sweep_to_fixed" else 0.0
    return replace(segment, elapsed_months=elapsed, is_matured=matured,
                   start_index_level=current_index_level if matured else segment.start_index_level,
                   base_balance=0.0 if sweep else base,
                   current_interim_value=0.0 if sweep else value, swept_value=sweep,
                   credited_rate=credited_rate if matured else 0.0)
