"""Run and run-attempt lifecycle: the legal statuses and transitions.

A *run* is the logical unit a user submits (one Projection Set × one scenario). An *attempt* is
one execution of that run by one worker. Statuses are strings in the database (with CHECK
constraints) and are only ever changed through ``transition``.

Run transitions::

    pending ──► running ──► success | partial_success | failed | cancelled
       │
       └──────► cancelled

Terminal statuses never change again. Executing a finished run again requires a new run (or,
later, a new attempt through an explicit retry — not implemented in Work Package 1).
"""

from collections.abc import Iterable

PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
PARTIAL_SUCCESS = "partial_success"
FAILED = "failed"
CANCELLED = "cancelled"

RUN_STATUSES: tuple[str, ...] = (PENDING, RUNNING, SUCCESS, PARTIAL_SUCCESS, FAILED, CANCELLED)
TERMINAL_STATUSES = frozenset({SUCCESS, PARTIAL_SUCCESS, FAILED, CANCELLED})
ACTIVE_STATUSES = frozenset({PENDING, RUNNING})
CLAIMABLE_STATUSES = frozenset({PENDING})
# Statuses whose accepted attempt holds results; partial_success is incomplete by definition.
RESULT_STATUSES = frozenset({SUCCESS, PARTIAL_SUCCESS})

RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, CANCELLED}),
    RUNNING: frozenset({SUCCESS, PARTIAL_SUCCESS, FAILED, CANCELLED}),
    SUCCESS: frozenset(),
    PARTIAL_SUCCESS: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}

ATTEMPT_STATUSES: tuple[str, ...] = (RUNNING, SUCCESS, PARTIAL_SUCCESS, FAILED, CANCELLED)
ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    RUNNING: frozenset({SUCCESS, PARTIAL_SUCCESS, FAILED, CANCELLED}),
    SUCCESS: frozenset(),
    PARTIAL_SUCCESS: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}

# Run Sets summarise their runs; "queued" means no run has started yet.
RUN_SET_QUEUED = "queued"
RUN_SET_STATUSES: tuple[str, ...] = (
    RUN_SET_QUEUED, RUNNING, SUCCESS, PARTIAL_SUCCESS, FAILED, CANCELLED,
)


class IllegalTransition(Exception):
    """Raised when code tries to move a run or attempt to a status it may not reach."""

    def __init__(self, kind: str, current: str, new: str):
        super().__init__(f"Illegal {kind} transition: {current} -> {new}.")
        self.kind = kind
        self.current = current
        self.new = new


def check_run_transition(current: str, new: str) -> None:
    if new not in RUN_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition("run", current, new)


def check_attempt_transition(current: str, new: str) -> None:
    if new not in ATTEMPT_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition("attempt", current, new)


def transition_run(run, new: str) -> None:
    """Move a run (any object with a ``status`` attribute) to ``new`` or raise."""
    check_run_transition(run.status, new)
    run.status = new


def transition_attempt(attempt, new: str) -> None:
    check_attempt_transition(attempt.status, new)
    attempt.status = new


def outcome_status(policy_count: int, failed_policies: int) -> str:
    """The run status implied by policy-level outcomes (policy errors are isolated)."""
    if policy_count == 0 or failed_policies >= policy_count:
        return FAILED
    if failed_policies == 0:
        return SUCCESS
    return PARTIAL_SUCCESS


def derive_run_set_status(statuses: Iterable[str]) -> str:
    statuses = list(statuses)
    if not statuses or all(status == PENDING for status in statuses):
        return RUN_SET_QUEUED
    if any(status in ACTIVE_STATUSES for status in statuses):
        return RUNNING
    if all(status == SUCCESS for status in statuses):
        return SUCCESS
    if all(status == FAILED for status in statuses):
        return FAILED
    if all(status == CANCELLED for status in statuses):
        return CANCELLED
    return PARTIAL_SUCCESS


def sql_in_list(values: Iterable[str]) -> str:
    """``'a', 'b'`` for CHECK constraints (values are fixed identifiers, never user input)."""
    return ", ".join(f"'{value}'" for value in values)
