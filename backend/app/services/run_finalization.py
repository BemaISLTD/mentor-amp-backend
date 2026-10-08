"""Terminal finalization of a run attempt: one transaction, idempotent, recoverable.

Promotion of an attempt is the moment its rows become canonical results, so it must be atomic
and must survive an uncertain commit:

1. The worker computes a ``TerminalOutcome`` (status, completion time, counts, summary, metrics,
   warnings/errors, cleanup status) and records it on the attempt (``intended_outcome``) in its
   own commit. From then on the outcome can be finished by anyone, e.g. a future reaper.
2. ``finalize_attempt`` applies it in ONE transaction, under a row lock on the run: run and
   attempt status, completion time, accepted attempt (success / partial_success only), counts,
   summary, metrics, the write-once final manifest and its fingerprint, and the completion event.
3. It is idempotent. It first classifies the stored state:
   - ``pending``: run and attempt still running, no manifest, nothing accepted → apply;
   - ``done``: already finalized with this outcome → return the existing evidence, change nothing;
   - anything else is ``FINALIZATION_INCONSISTENT`` → raise; results stay unavailable.
4. ``commit_terminal_outcome`` handles an exception from the terminal commit, whose outcome is
   unknown (PostgreSQL may have committed before the connection dropped): it discards the broken
   session, opens a FRESH session, reclassifies from the database and either recognises the
   committed state or retries the idempotent finalization (bounded).
5. ``recover_run_finalization(run_id)`` finishes a run whose worker died after recording its
   outcome — the integration point for the future lease/heartbeat reaper. Attempts that died
   before recording an outcome are left untouched (``calculation_incomplete``).

Results are readable only when ``evidence_consistent`` holds (see ``results_service``).
"""

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.execution import run_state
from app.db.models.run import Run
from app.db.models.run_artifact import RunManifest
from app.db.models.run_package import RunAttempt, RunPackage
from app.services.common import ServiceError, not_found
from app.services.run_events import add_event
from app.services.run_manifest_service import write_final_manifest

logger = logging.getLogger(__name__)

PENDING, DONE, INCONSISTENT = "pending", "done", "inconsistent"
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5


class FinalizationError(ServiceError):
    """FINALIZATION_INCONSISTENT (impossible stored state) or FINALIZATION_UNCERTAIN (gave up)."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(409, code, message, details or {})


@dataclass(frozen=True)
class TerminalOutcome:
    run_id: str
    attempt_id: str
    attempt_number: int
    status: str
    completed_at: str  # ISO text, fixed once so a retried finalization builds the same manifest
    output_row_count: int = 0
    trace_row_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    cleanup_status: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    summary: dict | None = None
    metrics: dict | None = None
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    environment: dict | None = None
    event_step: str = "complete"
    event_message: str = ""
    event_level: str = "info"
    event_data: dict | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerminalOutcome":
        return cls(**data)


@dataclass
class FinalizationResult:
    state: str  # finalized | already_finalized | calculation_incomplete | not_started
    run_status: str | None
    final_manifest_fingerprint: str | None
    recovered: bool = False


# =============================================================================
# State classification
# =============================================================================

def _accepted_as_expected(run: Run, attempt: RunAttempt) -> bool:
    if attempt.status in run_state.RESULT_STATUSES:
        return run.accepted_attempt_number == attempt.attempt_number
    return run.accepted_attempt_number is None


def classify(run: Run, attempt: RunAttempt | None, manifest: RunManifest | None,
             expected_status: str | None) -> str:
    if attempt is None:
        return INCONSISTENT
    if (run.status == run_state.RUNNING and attempt.status == run_state.RUNNING
            and manifest is None and run.accepted_attempt_number is None
            and run.final_manifest_fingerprint is None):
        return PENDING
    finished = (
        attempt.status in run_state.TERMINAL_STATUSES
        and run.status == attempt.status
        and (expected_status is None or attempt.status == expected_status)
        and manifest is not None
        and manifest.attempt_number == attempt.attempt_number
        and run.final_manifest_fingerprint == manifest.fingerprint
        and _accepted_as_expected(run, attempt)
    )
    return DONE if finished else INCONSISTENT


def evidence_consistent(db: Session, run: Run) -> bool:
    """A result-bearing run whose terminal evidence agrees: accepted attempt, manifest, status."""
    if run.status not in run_state.RESULT_STATUSES or run.accepted_attempt_number is None:
        return False
    if not run.final_manifest_fingerprint:
        return False
    manifest = db.get(RunManifest, run.id)
    attempt = (
        db.query(RunAttempt)
        .filter(RunAttempt.run_id == run.id, RunAttempt.attempt_number == run.accepted_attempt_number)
        .first()
    )
    return classify(run, attempt, manifest, run.status) == DONE


def _state_details(run: Run, attempt: RunAttempt | None, manifest: RunManifest | None) -> dict:
    return {
        "run_status": run.status,
        "attempt_status": attempt.status if attempt else None,
        "accepted_attempt_number": run.accepted_attempt_number,
        "final_manifest_fingerprint": run.final_manifest_fingerprint,
        "manifest_attempt_number": manifest.attempt_number if manifest else None,
        "manifest_fingerprint": manifest.fingerprint if manifest else None,
    }


# =============================================================================
# Finalization
# =============================================================================

def _commit(db: Session) -> None:
    """The terminal commit (a seam for tests that simulate an uncertain commit)."""
    db.commit()


def record_intended_outcome(db: Session, outcome: TerminalOutcome) -> None:
    """Make the outcome durable before the terminal transaction (idempotent)."""
    attempt = db.get(RunAttempt, outcome.attempt_id)
    if attempt is None or attempt.status != run_state.RUNNING:
        return
    if attempt.intended_outcome == outcome.as_dict():
        return
    attempt.intended_outcome = outcome.as_dict()
    db.commit()


def finalize_attempt(db: Session, outcome: TerminalOutcome) -> FinalizationResult:
    run = db.execute(select(Run).where(Run.id == outcome.run_id).with_for_update()).scalar_one()
    attempt = db.get(RunAttempt, outcome.attempt_id)
    manifest = db.get(RunManifest, run.id)
    state = classify(run, attempt, manifest, outcome.status)
    if state == DONE:
        db.rollback()  # release the row lock; nothing to change
        return FinalizationResult("already_finalized", run.status, run.final_manifest_fingerprint)
    if state == INCONSISTENT or attempt.attempt_number != outcome.attempt_number:
        details = {**_state_details(run, attempt, manifest), "intended_status": outcome.status}
        db.rollback()
        logger.error("Run %s has inconsistent terminal evidence: %s", outcome.run_id, details)
        raise FinalizationError(
            "FINALIZATION_INCONSISTENT",
            f"Run '{outcome.run_id}' has inconsistent terminal evidence; its results stay "
            "unavailable until it is investigated.",
            details,
        )

    from datetime import datetime  # local: only needed when a finalization is applied

    completed_at = datetime.fromisoformat(outcome.completed_at)
    run_state.transition_run(run, outcome.status)
    run_state.transition_attempt(attempt, outcome.status)
    run.completed_at = attempt.completed_at = completed_at
    run.error_count = outcome.error_count
    run.warning_count = outcome.warning_count
    if outcome.summary is not None:
        run.summary = outcome.summary
    if outcome.status in run_state.RESULT_STATUSES:
        run.accepted_attempt_number = attempt.attempt_number
    attempt.output_row_count = outcome.output_row_count
    attempt.trace_row_count = outcome.trace_row_count
    attempt.cleanup_status = outcome.cleanup_status
    attempt.error_type = outcome.error_type
    attempt.error_message = outcome.error_message
    attempt.metrics = outcome.metrics
    package = db.query(RunPackage).filter(RunPackage.run_id == run.id).first()
    write_final_manifest(db, run, attempt, package, list(outcome.warnings), list(outcome.errors),
                         environment=outcome.environment)
    add_event(db, run.id, outcome.event_step, outcome.event_message, level=outcome.event_level,
              data=outcome.event_data)
    _commit(db)
    return FinalizationResult("finalized", outcome.status, run.final_manifest_fingerprint)


def _discard(db: Session) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001 - the connection may already be gone
        logger.warning("Rollback of a broken session failed", exc_info=True)


def commit_terminal_outcome(
    db: Session,
    outcome: TerminalOutcome,
    session_factory: Callable[[], Session],
    retries: int = RETRY_ATTEMPTS,
) -> FinalizationResult:
    """Record and finalize an outcome; survive an uncertain commit by reclassifying afresh."""
    try:
        record_intended_outcome(db, outcome)
        return finalize_attempt(db, outcome)
    except FinalizationError:
        raise
    except Exception as error:  # noqa: BLE001 - commit outcome unknown: verify from the database
        logger.warning("Terminal commit for run %s raised %r; verifying from a fresh session",
                       outcome.run_id, error)
        _discard(db)
        last_error: Exception = error
    for number in range(retries):
        fresh = session_factory()
        try:
            record_intended_outcome(fresh, outcome)
            result = finalize_attempt(fresh, outcome)
            result.recovered = True
            return result
        except FinalizationError:
            raise
        except Exception as error:  # noqa: BLE001
            last_error = error
            _discard(fresh)
            time.sleep(RETRY_BACKOFF_SECONDS * (number + 1))
        finally:
            fresh.close()
    raise FinalizationError(
        "FINALIZATION_UNCERTAIN",
        f"Could not confirm the terminal outcome of run '{outcome.run_id}'; its results stay "
        "unavailable. recover_run_finalization() can finish it later.",
        {"last_error": repr(last_error)},
    )


def recover_run_finalization(db: Session, run_id: str) -> FinalizationResult:
    """Finish (or confirm) the terminal outcome of a run whose worker may have died.

    Integration point for the future lease/heartbeat reaper. Safe to call at any time.
    """
    run = db.get(Run, run_id)
    if run is None:
        raise not_found(f"Run '{run_id}' not found.")
    attempt = (
        db.query(RunAttempt)
        .filter(RunAttempt.run_id == run_id)
        .order_by(RunAttempt.attempt_number.desc())
        .first()
    )
    if attempt is None:
        return FinalizationResult("not_started", run.status, run.final_manifest_fingerprint)
    if attempt.status in run_state.TERMINAL_STATUSES:
        manifest = db.get(RunManifest, run_id)
        if classify(run, attempt, manifest, attempt.status) == DONE:
            return FinalizationResult("already_finalized", run.status, run.final_manifest_fingerprint)
        raise FinalizationError(
            "FINALIZATION_INCONSISTENT",
            f"Run '{run_id}' has inconsistent terminal evidence.",
            _state_details(run, attempt, manifest),
        )
    if attempt.intended_outcome is None:
        return FinalizationResult("calculation_incomplete", run.status, None)
    result = finalize_attempt(db, TerminalOutcome.from_dict(attempt.intended_outcome))
    result.recovered = True
    return result
