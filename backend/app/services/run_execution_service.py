"""Executing runs: atomic claim → verify → calculate → idempotent terminal promotion.

Execution reads the run's immutable package and the datasets it references by ID; it never
re-reads the editable Projection Set, formula, variable or scenario tables.

1. **Claim** — ``UPDATE runs SET status='running' ... WHERE id=:id AND status='pending'``. Only
   one worker can win; the winner creates attempt N (UNIQUE (run_id, attempt_number) backs it).
2. **Verify** (``run_package_verification`` + ``dataset_loader``) — package envelope and run
   index, configuration fingerprint, build identity, every formula implementation fingerprint,
   every dataset fingerprint. Any mismatch fails the run *before* calculation.
3. **Calculate** — the pure engine with the verified callables, policy by policy. Result and
   trace rows carry the attempt number and the FROZEN scenario ID, and are written as Parquet
   artifacts every few policies.
4. **Promote** (``run_finalization``) — the outcome is recorded on the attempt, then applied in
   one idempotent transaction (status, accepted attempt, summary, final manifest); an uncertain
   commit is verified from a fresh session and finished if needed.

Artifacts from failed attempts are retained as noncanonical evidence. Results require the
accepted attempt and consistent terminal evidence, which a failed attempt never has.
"""

import json
import logging
import math
import os
import socket
import threading
from time import perf_counter
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.execution import run_package, run_state
from app.core.artifacts import get_artifact_store
from app.core.execution.run_package import RunPackageError
from app.core.projection_engine.engine import EngineError, run_policy
from app.db.database import SessionLocal
from app.db.models.projection import RunSet
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunAttempt, RunPackage
from app.products.registry import register_all_products
from app.services import dataset_loader, governance_service, run_finalization
from app.services.build_info import build_identity, runtime_environment
from app.services.common import iso, now_utc
from app.services.run_events import add_event
from app.services.run_finalization import FinalizationError, TerminalOutcome
from app.services.run_package_verification import verify_for_execution

logger = logging.getLogger(__name__)

FLUSH_EVERY_POLICIES = 5
TOTAL_LABELS = {
    "expected_payment": "Total expected payments (undiscounted)",
    "pv_expected_payment": "PV of expected payments",
}
RUNS = Run.__table__


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


# =============================================================================
# Claiming
# =============================================================================

def claim_run(db: Session, run_id: str, worker_id: str) -> RunAttempt | None:
    """Atomically move a pending run to running and open its next attempt.

    Returns ``None`` when another worker already claimed the run or it is no longer pending.
    """
    run_state.check_run_transition(run_state.PENDING, run_state.RUNNING)
    now = now_utc()
    claimed = db.execute(
        update(RUNS)
        .where(RUNS.c.id == run_id, RUNS.c.status == run_state.PENDING)
        .values(status=run_state.RUNNING, started_at=now, attempt_count=RUNS.c.attempt_count + 1)
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    number = db.execute(select(RUNS.c.attempt_count).where(RUNS.c.id == run_id)).scalar_one()
    try:
        executed_build = build_identity()
    except Exception:  # noqa: BLE001 - recorded as unknown; verification refuses the run below
        executed_build = None
    attempt = RunAttempt(
        run_id=run_id,
        attempt_number=number,
        status=run_state.RUNNING,
        worker_id=worker_id,
        started_at=now,
        heartbeat_at=now,
        executed_build=executed_build,
    )
    db.add(attempt)
    governance_service.reset_run_steps(db, run_id)
    add_event(db, run_id, "claimed", f"Attempt {number} started on {worker_id}.",
              data={"attempt_number": number, "worker_id": worker_id})
    db.commit()
    return attempt


# =============================================================================
# Run Sets
# =============================================================================

def refresh_run_set_status(db: Session, run_set: RunSet) -> None:
    db.flush()
    statuses = [row[0] for row in db.query(Run.status).filter(Run.run_set_id == run_set.id).all()]
    run_set.status = run_state.derive_run_set_status(statuses)
    if run_set.status in run_state.TERMINAL_STATUSES and run_set.completed_at is None:
        run_set.completed_at = now_utc()
    elif run_set.status not in run_state.TERMINAL_STATUSES:
        run_set.completed_at = None


def execute_run_set(run_set_id: str, worker_id: str | None = None) -> None:
    """Execute every pending run of a Run Set in its own database session (background task)."""
    register_all_products()
    worker_id = worker_id or default_worker_id()
    db = SessionLocal()
    try:
        run_set = db.get(RunSet, run_set_id)
        if run_set is None:
            return
        run_ids = [
            row[0]
            for row in db.query(Run.id)
            .filter(Run.run_set_id == run_set_id, Run.status == run_state.PENDING)
            .order_by(Run.created_at, Run.name)
            .all()
        ]
        if run_set.status == run_state.RUN_SET_QUEUED:
            run_set.status = run_state.RUNNING
            db.commit()
        for run_id in run_ids:
            execute_run(db, run_id, worker_id)
        run_set = db.get(RunSet, run_set_id)
        refresh_run_set_status(db, run_set)
        db.commit()
    except Exception:  # noqa: BLE001 - a background task must never crash silently
        logger.exception("Run Set %s failed", run_set_id)
        db.rollback()
    finally:
        db.close()


def execute_run_by_id(run_id: str, worker_id: str | None = None) -> None:
    """Execute one queued run in a background-safe session and refresh its Run Set."""
    db = SessionLocal()
    try:
        execute_run(db, run_id, worker_id)
        run = db.get(Run, run_id)
        run_set = db.get(RunSet, run.run_set_id) if run and run.run_set_id else None
        if run_set is not None:
            refresh_run_set_status(db, run_set)
            db.commit()
    except Exception:  # noqa: BLE001 - background execution must be observable, never escape
        logger.exception("Run %s failed in its background task", run_id)
        db.rollback()
    finally:
        db.close()


# =============================================================================
# One run
# =============================================================================

class _PreflightFailure(Exception):
    def __init__(self, error_type: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.details = details or {}


def _load(db: Session, run: Run, package: RunPackage | None):
    """Verify the package, build, implementations and datasets; return what execution needs."""
    try:
        configuration, functions, _build = verify_for_execution(run, package)
    except RunPackageError as error:
        raise _PreflightFailure(error.code, error.message, error.details) from error
    try:
        policies = dataset_loader.load_policies(db, configuration)
        tables = dataset_loader.load_tables(db, configuration)
        data = run_package.build_run_data(configuration, policies, tables)
    except dataset_loader.DataIntegrityError as error:
        raise _PreflightFailure(error.code, error.message, error.details) from error
    except EngineError as error:
        raise _PreflightFailure(error.error_type, error.message) from error
    if not data.policies:
        raise _PreflightFailure("NO_POLICIES", "The run's inforce files contain no policies.")
    return configuration, functions, data


def _finish(db: Session, outcome: TerminalOutcome) -> str | None:
    """Promote the outcome; an unrecoverable finalization leaves the run for recovery."""
    began = perf_counter()
    try:
        result = run_finalization.commit_terminal_outcome(db, outcome, SessionLocal)
    except FinalizationError as error:
        logger.error("Finalization of run %s failed (%s): %s", outcome.run_id, error.code, error.details)
        return None
    seconds = perf_counter() - began
    try:  # informational log entry, outside the terminal evidence
        add_event(db, outcome.run_id, "finalized",
                  f"Terminal outcome committed in {seconds:.2f}s"
                  + (" after verification from a fresh session." if result.recovered else "."),
                  data={"finalization_seconds": round(seconds, 4), "recovered": result.recovered,
                        "state": result.state})
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Could not record the finalization event for run %s", outcome.run_id, exc_info=True)
        db.rollback()
    return result.run_status


def execute_run(db: Session, run_id: str, worker_id: str | None = None) -> str | None:
    """Claim and execute one run; return its final status, or None if it was not claimed."""
    register_all_products()
    attempt = claim_run(db, run_id, worker_id or default_worker_id())
    if attempt is None:
        return None
    attempt_id, attempt_number = attempt.id, attempt.attempt_number
    run = db.get(Run, run_id)
    package = db.query(RunPackage).filter(RunPackage.run_id == run_id).first()
    metrics: dict[str, float] = {}
    started = perf_counter()
    governance_service.update_run_step(db, run_id, "verify_inputs", "running")
    db.commit()
    try:
        configuration, functions, data = _load(db, run, package)
    except _PreflightFailure as failure:
        governance_service.update_run_step(
            db, run_id, "verify_inputs", "failed", error_code=failure.error_type,
            error_message=failure.message,
        )
        governance_service.update_run_step(db, run_id, "calculate", "skipped")
        governance_service.update_run_step(db, run_id, "finalize", "running")
        db.commit()
        result = _fail(db, run_id, attempt_id, attempt_number, failure.error_type,
                       f"Run refused before calculation: {failure.message}", failure.details,
                       rows_written=False, metrics={"load_seconds": perf_counter() - started})
        governance_service.update_run_step(
            db, run_id, "finalize", "success" if result is not None else "failed",
            error_code=None if result is not None else "FINALIZATION_ERROR",
        )
        db.commit()
        return result
    except Exception as error:  # noqa: BLE001 - e.g. a lost connection while loading
        logger.exception("Loading run %s failed", run_id)
        db.rollback()
        governance_service.update_run_step(
            db, run_id, "verify_inputs", "failed", error_code="LOAD_ERROR",
            error_message=str(error),
        )
        governance_service.update_run_step(db, run_id, "calculate", "skipped")
        governance_service.update_run_step(db, run_id, "finalize", "running")
        db.commit()
        result = _fail(db, run_id, attempt_id, attempt_number, "LOAD_ERROR",
                       f"Run could not be loaded: {error}", {}, rows_written=False)
        governance_service.update_run_step(
            db, run_id, "finalize", "success" if result is not None else "failed",
            error_code=None if result is not None else "FINALIZATION_ERROR",
        )
        db.commit()
        return result
    metrics["load_seconds"] = perf_counter() - started

    run.progress_total = len(data.policies)
    governance_service.update_run_step(
        db, run_id, "verify_inputs", "success",
        metrics={"load_seconds": round(metrics["load_seconds"], 4)},
    )
    governance_service.update_run_step(
        db, run_id, "calculate", "running", progress_total=len(data.policies),
    )
    add_event(
        db, run.id, "load",
        f"Verified run package {package.fingerprint[:12]}…, build and {len(functions)} formula "
        f"implementation(s); {len(configuration['datasets']['inforce'])} inforce file(s) + "
        f"{len(data.tables)} table(s); loaded {len(data.policies)} policies.",
        data={"policies": len(data.policies), "formulas": len(data.formulas),
              "run_package_fingerprint": package.fingerprint},
    )
    db.commit()

    product_code = configuration["model"]["product_code"]
    # Evidence is labelled with the FROZEN scenario identity (verified equal to the run index).
    scenario_id = configuration["scenario"]["id"] or ""
    output_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    total_values: dict[str, list[float]] = {}
    counts = {"outputs": 0, "traces": 0}
    timing = {"calc": 0.0, "outputs": 0.0, "traces": 0.0, "commit": 0.0}
    failed_policies = 0
    warnings: list[str] = []
    errors: list[str] = []
    cancelled = False

    def flush() -> None:
        nonlocal output_rows, trace_rows
        store = get_artifact_store()
        for artifact_type, rows in (("outputs", output_rows), ("traces", trace_rows)):
            if not rows:
                continue
            began = perf_counter()
            encoded = []
            for row in rows:
                common = {
                    "run_id": run.id,
                    "attempt_number": attempt.attempt_number,
                    "policy_id": row["policy_id"],
                    "scenario_id": row["scenario_id"],
                    "projection_month": row["projection_month"],
                    "variable_name": row["variable_name"],
                }
                if artifact_type == "outputs":
                    encoded.append({
                        **common,
                        "value_json": json.dumps(row["value"], sort_keys=True, default=str),
                        "product": row.get("product"),
                    })
                else:
                    encoded.append({
                        **common,
                        "formula_id": row.get("formula_id"),
                        "input_values_json": json.dumps(row.get("input_values"), sort_keys=True, default=str),
                        "output_value_json": json.dumps(row.get("output_value"), sort_keys=True, default=str),
                        "source_type": row.get("source_type"),
                        "source_table": row.get("source_table"),
                        "lookup_keys_json": json.dumps(row.get("lookup_keys"), sort_keys=True, default=str),
                        "error_message": row.get("error_message"),
                    })
            stored = store.write_rows(run.id, artifact_type, encoded)
            db.add(RunArtifact(
                run_id=run.id,
                attempt_number=attempt.attempt_number,
                artifact_type=artifact_type,
                storage_uri=stored.uri,
                storage_backend=store.backend_name,
                file_format="parquet",
                schema_version="m1-v1",
                row_count=stored.row_count,
                checksum_sha256=stored.checksum_sha256,
                partition={"attempt_number": attempt.attempt_number},
            ))
            timing[artifact_type] += perf_counter() - began
        counts["outputs"] += len(output_rows)
        counts["traces"] += len(trace_rows)
        output_rows, trace_rows = [], []
        attempt.heartbeat_at = now_utc()
        began = perf_counter()
        db.commit()
        timing["commit"] += perf_counter() - began

    try:
        for index, policy in enumerate(data.policies, start=1):
            db.refresh(run, attribute_names=["cancel_requested_at"])
            if run.cancel_requested_at is not None:
                cancelled = True
                break
            began = perf_counter()
            result = run_policy(data, policy, functions)
            timing["calc"] += perf_counter() - began
            if result.error is not None:
                failed_policies += 1
                message = (f"Policy {policy.policy_id} failed at month {result.error.month}: "
                           f"{result.error.message}")
                errors.append(message)
                add_event(db, run.id, "policy", message, level="error",
                          data={"policy_id": policy.policy_id, **result.error.as_dict()})
            else:
                created_at = now_utc()
                for month, variable, value in result.outputs:
                    output_rows.append({
                        "run_id": run.id,
                        "attempt_number": attempt_number,
                        "policy_id": policy.policy_id,
                        "scenario_id": scenario_id,
                        "projection_month": month,
                        "variable_name": variable,
                        "value": {"value": value},
                        "product": product_code,
                        "created_at": created_at,
                    })
                for name, value in result.sums.items():
                    total_values.setdefault(name, []).append(value)
                add_event(db, run.id, "policy",
                          f"Policy {policy.policy_id} complete ({result.months_computed} months).",
                          data={"policy_id": policy.policy_id})
            for warning in result.warnings:
                warnings.append(f"{policy.policy_id}: {warning}")
                add_event(db, run.id, "policy", f"{policy.policy_id}: {warning}", level="warning",
                          data={"policy_id": policy.policy_id})
            for row in result.trace_rows:
                trace_rows.append({
                    **row,
                    "run_id": run.id,
                    "attempt_number": attempt_number,
                    "scenario_id": scenario_id,
                    "created_at": now_utc(),
                })
            run.progress_done = index
            governance_service.update_run_step(
                db, run_id, "calculate", "running",
                progress_total=len(data.policies), progress_done=index,
            )
            if index % FLUSH_EVERY_POLICIES == 0 or index == len(data.policies):
                flush()
        if not cancelled:
            db.refresh(run, attribute_names=["cancel_requested_at"])
            cancelled = run.cancel_requested_at is not None
        if cancelled and (output_rows or trace_rows):
            flush()
    except Exception as error:  # noqa: BLE001 - any failure fails the attempt, never the process
        logger.exception("Run %s failed during execution", run_id)
        db.rollback()
        governance_service.update_run_step(
            db, run_id, "calculate", "failed", error_code="EXECUTION_ERROR",
            error_message=str(error), progress_total=len(data.policies),
            progress_done=run.progress_done,
        )
        governance_service.update_run_step(db, run_id, "finalize", "running")
        db.commit()
        result = _fail(db, run_id, attempt_id, attempt_number, "EXECUTION_ERROR",
                       f"Run failed during execution: {error}", {}, rows_written=True,
                       metrics=metrics, prior_errors=errors, warnings=warnings)
        governance_service.update_run_step(
            db, run_id, "finalize", "success" if result is not None else "failed",
            error_code=None if result is not None else "FINALIZATION_ERROR",
        )
        db.commit()
        return result

    totals = {name: math.fsum(values) for name, values in total_values.items()}
    status = (
        run_state.CANCELLED
        if cancelled
        else run_state.outcome_status(len(data.policies), failed_policies)
    )
    metrics.update({
        "calculation_seconds": timing["calc"],
        "output_write_seconds": timing["outputs"],
        "trace_write_seconds": timing["traces"],
        "commit_seconds": timing["commit"],
        "total_seconds": perf_counter() - started,
    })
    cleanup_status = "not_needed"
    error_type = error_message = None
    if status == run_state.CANCELLED:
        cleanup_status = "retained_noncanonical"
    elif status == run_state.FAILED:
        # Every policy failed: nothing is canonical; remove whatever the attempt wrote.
        cleanup_status = _delete_attempt_rows(db, run_id, attempt_number)
        error_type, error_message = "ALL_POLICIES_FAILED", (errors[0] if errors else None)
    succeeded = len(data.policies) - failed_policies
    rounded = {key: round(value, 4) for key, value in metrics.items()}
    outcome = TerminalOutcome(
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        status=status,
        completed_at=now_utc().isoformat(),
        output_row_count=counts["outputs"],
        trace_row_count=counts["traces"],
        error_count=len(errors),
        warning_count=len(warnings),
        cleanup_status=cleanup_status,
        error_type=error_type,
        error_message=error_message,
        summary=_build_summary(run, status, configuration, data, totals, counts, failed_policies,
                               attempt_number, len(errors), len(warnings)),
        metrics=rounded,
        warnings=warnings,
        errors=errors,
        environment=runtime_environment(),
        event_step="cancelled" if cancelled else "complete",
        event_message=(
            f"Run cancelled after {run.progress_done} of {len(data.policies)} policies."
            if cancelled
            else f"Run completed: {succeeded} of {len(data.policies)} policies ({status})."
        ),
        event_level="warning" if cancelled or failed_policies else "info",
        event_data={"status": status, "attempt_number": attempt_number, "metrics": rounded},
    )
    governance_service.update_run_step(
        db, run_id, "calculate",
        "cancelled" if cancelled else ("success" if status != run_state.FAILED else "failed"),
        progress_total=len(data.policies),
        progress_done=run.progress_done if cancelled else len(data.policies), metrics=rounded,
        error_code=error_type, error_message=error_message,
    )
    governance_service.update_run_step(db, run_id, "finalize", "running")
    db.commit()
    result = _finish(db, outcome)
    governance_service.update_run_step(
        db, run_id, "finalize", "success" if result is not None else "failed",
        error_code=None if result is not None else "FINALIZATION_ERROR",
    )
    db.commit()
    return result


def _delete_attempt_rows(db: Session, run_id: str, attempt_number: int) -> str:
    """Keep failed-attempt Parquet artifacts as noncanonical evidence."""
    return "retained_noncanonical"


def _fail(
    db: Session,
    run_id: str,
    attempt_id: str,
    attempt_number: int,
    error_type: str,
    message: str,
    details: dict,
    *,
    rows_written: bool,
    metrics: dict[str, float] | None = None,
    prior_errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> str | None:
    cleanup_status = _delete_attempt_rows(db, run_id, attempt_number) if rows_written else "not_needed"
    errors = [*(prior_errors or []), message]
    outcome = TerminalOutcome(
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        status=run_state.FAILED,
        completed_at=now_utc().isoformat(),
        error_count=len(errors),
        warning_count=len(warnings or []),
        cleanup_status=cleanup_status,
        error_type=error_type,
        error_message=message,
        metrics={key: round(value, 4) for key, value in (metrics or {}).items()} or None,
        warnings=list(warnings or []),
        errors=errors,
        environment=runtime_environment(),
        event_step="failed",
        event_message=message,
        event_level="error",
        event_data={"error_type": error_type, "attempt_number": attempt_number, **details},
    )
    return _finish(db, outcome)


def _build_summary(
    run: Run,
    status: str,
    configuration: dict[str, Any],
    data,
    totals: dict[str, float],
    counts: dict[str, int],
    failed_policies: int,
    attempt_number: int,
    error_count: int,
    warning_count: int,
) -> dict[str, Any]:
    published = configuration["outputs"].get("published") or {}
    illustrative = bool(configuration["model_version"]["illustrative"])
    headline = None
    for spec in data.valuation_variables():
        name = spec.name
        if name in data.output_variables and f"{name}_0" in totals:
            headline = {
                "metric": name,
                "label": "Reserve at valuation (illustrative)" if illustrative else "Reserve at valuation",
                "value": totals[f"{name}_0"],
                "unit": (published.get(name) or {}).get("unit") or "USD",
                "as_of": iso(data.valuation_date),
            }
            break
    summary_totals: dict[str, Any] = {}
    for name in data.output_variables:
        output = published.get(name)
        if not output or output.get("aggregation") != "sum" or output.get("unit") != "USD" or name not in totals:
            continue
        summary_totals[name] = {
            "label": TOTAL_LABELS.get(name, f"Total {output.get('display_name')}"),
            "value": totals[name],
            "unit": output.get("unit"),
        }
    return {
        "run_id": run.id,
        "status": status,
        "complete": status == run_state.SUCCESS,
        "illustrative": illustrative,
        "attempt_number": attempt_number,
        "run_package_fingerprint": run.run_package_fingerprint,
        "scenario": {"id": data.scenario.id, "name": data.scenario.name},
        "valuation_date": iso(data.valuation_date),
        "policy_count": len(data.policies),
        "failed_policy_count": failed_policies,
        "period_count": data.horizon_months,
        "output_variable_count": len(data.output_variables),
        "output_row_count": counts["outputs"],
        "error_count": error_count,
        "warning_count": warning_count,
        "headline": headline,
        "totals": summary_totals,
        "trace": {
            "captured": bool(data.traced_policy_ids),
            "mode": (configuration["outputs"].get("trace_scope") or {}).get("mode", "none"),
            "captured_policy_count": len(data.traced_policy_ids),
            "policy_ids": sorted(data.traced_policy_ids),
        },
    }
