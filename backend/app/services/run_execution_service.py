"""Executing runs: atomic claim → verify the frozen package and its data → calculate → promote.

Execution reads the run's immutable package and the datasets it references by ID; it never
re-reads the editable Projection Set, formula, variable or scenario tables.

1. **Claim** — ``UPDATE runs SET status='running' ... WHERE id=:id AND status='pending'``. Only
   one worker can win; the winner creates attempt N (UNIQUE (run_id, attempt_number) backs it).
2. **Verify** — package fingerprint, engine version, registered functions, and every dataset's
   fingerprint and record count. Any mismatch fails the run *before* calculation.
3. **Calculate** — the pure engine, policy by policy; result/trace rows carry the attempt number
   and are written as Parquet artifacts every few policies.
4. **Finish** — in one transaction: final status, accepted attempt (only for success /
   partial_success), summary and the write-once final manifest.

Artifacts from failed attempts are retained as noncanonical evidence. Results APIs read only
``runs.accepted_attempt_number``, which a failed attempt never sets.
"""

import json
import logging
import os
import socket
import threading
from time import perf_counter
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.execution import run_package, run_state
from app.core.artifacts import get_artifact_store
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import ENGINE_VERSION, EngineError, run_policy
from app.db.database import SessionLocal
from app.db.models.projection import RunSet
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunAttempt, RunPackage
from app.products.registry import register_all_products
from app.services import dataset_loader
from app.services.build_info import build_identity
from app.services.common import iso, now_utc
from app.services.run_events import add_event
from app.services.run_manifest_service import write_final_manifest

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
    attempt = RunAttempt(
        run_id=run_id,
        attempt_number=number,
        status=run_state.RUNNING,
        worker_id=worker_id,
        started_at=now,
        heartbeat_at=now,
        executed_build=build_identity(),
    )
    db.add(attempt)
    add_event(db, run_id, "claimed", f"Attempt {number} started on {worker_id}.",
              data={"attempt_number": number, "worker_id": worker_id})
    db.commit()
    return attempt


# =============================================================================
# Run Sets
# =============================================================================

def refresh_run_set_status(db: Session, run_set: RunSet) -> None:
    statuses = [row[0] for row in db.query(Run.status).filter(Run.run_set_id == run_set.id).all()]
    run_set.status = run_state.derive_run_set_status(statuses)
    if run_set.status in run_state.TERMINAL_STATUSES and run_set.completed_at is None:
        run_set.completed_at = now_utc()


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
    """Verify the package and its datasets; return (configuration, RunData) or raise."""
    if package is None or not run.run_package_fingerprint:
        raise _PreflightFailure(
            "PACKAGE_MISSING", "The run has no frozen run package, so it cannot execute reproducibly.",
        )
    try:
        configuration = run_package.verify(package.package, run.run_package_fingerprint)
    except run_package.RunPackageError as error:
        raise _PreflightFailure(error.code, error.message, error.details) from error
    if package.fingerprint != run.run_package_fingerprint:
        raise _PreflightFailure(
            "PACKAGE_TAMPERED", "The run and its package record different fingerprints.",
            {"run": run.run_package_fingerprint, "package": package.fingerprint},
        )
    frozen_engine = configuration["build"]["engine_version"]
    if frozen_engine != ENGINE_VERSION:
        raise _PreflightFailure(
            "ENGINE_VERSION_MISMATCH",
            f"The run was frozen for engine '{frozen_engine}' but this worker runs "
            f"'{ENGINE_VERSION}'; results would not be reproducible.",
        )
    missing = [ref for ref in run_package.function_refs(configuration) if ref not in FORMULA_FUNCTIONS]
    if missing:
        raise _PreflightFailure(
            "FUNCTION_NOT_REGISTERED", f"Formula functions not available on this worker: {missing}.",
        )
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
    return configuration, data


def execute_run(db: Session, run_id: str, worker_id: str | None = None) -> str | None:
    """Claim and execute one run; return its final status, or None if it was not claimed."""
    register_all_products()
    attempt = claim_run(db, run_id, worker_id or default_worker_id())
    if attempt is None:
        return None
    run = db.get(Run, run_id)
    package = db.query(RunPackage).filter(RunPackage.run_id == run_id).first()
    metrics: dict[str, float] = {}
    started = perf_counter()
    try:
        configuration, data = _load(db, run, package)
    except _PreflightFailure as failure:
        return _finish_failed(
            db, run, attempt, package, failure.error_type,
            f"Run refused before calculation: {failure.message}", failure.details, rows_written=False,
        )
    except Exception as error:  # noqa: BLE001 - e.g. a lost connection while loading
        logger.exception("Loading run %s failed", run_id)
        db.rollback()
        run, attempt = db.get(Run, run_id), db.get(RunAttempt, attempt.id)
        return _finish_failed(
            db, run, attempt, package, "LOAD_ERROR", f"Run could not be loaded: {error}", {},
            rows_written=False,
        )
    metrics["load_seconds"] = perf_counter() - started

    run.progress_total = len(data.policies)
    add_event(
        db, run.id, "load",
        f"Verified run package {package.fingerprint[:12]}… and {len(configuration['datasets']['inforce'])} "
        f"inforce file(s) + {len(data.tables)} table(s); loaded {len(data.policies)} policies.",
        data={"policies": len(data.policies), "formulas": len(data.formulas),
              "run_package_fingerprint": package.fingerprint},
    )
    db.commit()

    product_code = configuration["model"]["product_code"]
    scenario_id = run.scenario_id or ""
    output_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    counts = {"outputs": 0, "traces": 0}
    timing = {"calc": 0.0, "outputs": 0.0, "traces": 0.0, "commit": 0.0}
    failed_policies = 0
    warnings: list[str] = []
    errors: list[str] = []

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
            began = perf_counter()
            result = run_policy(data, policy, FORMULA_FUNCTIONS)
            timing["calc"] += perf_counter() - began
            if result.error is not None:
                failed_policies += 1
                run.error_count = (run.error_count or 0) + 1
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
                        "attempt_number": attempt.attempt_number,
                        "policy_id": policy.policy_id,
                        "scenario_id": scenario_id,
                        "projection_month": month,
                        "variable_name": variable,
                        "value": {"value": value},
                        "product": product_code,
                        "created_at": created_at,
                    })
                for name, value in result.sums.items():
                    totals[name] = totals.get(name, 0.0) + value
                add_event(db, run.id, "policy",
                          f"Policy {policy.policy_id} complete ({result.months_computed} months).",
                          data={"policy_id": policy.policy_id})
            for warning in result.warnings:
                run.warning_count = (run.warning_count or 0) + 1
                warnings.append(f"{policy.policy_id}: {warning}")
                add_event(db, run.id, "policy", f"{policy.policy_id}: {warning}", level="warning",
                          data={"policy_id": policy.policy_id})
            for row in result.trace_rows:
                trace_rows.append({
                    **row,
                    "run_id": run.id,
                    "attempt_number": attempt.attempt_number,
                    "scenario_id": scenario_id,
                    "created_at": now_utc(),
                })
            run.progress_done = index
            if index % FLUSH_EVERY_POLICIES == 0 or index == len(data.policies):
                flush()
    except Exception as error:  # noqa: BLE001 - any failure fails the attempt, never the process
        logger.exception("Run %s failed during execution", run_id)
        db.rollback()
        run = db.get(Run, run_id)
        attempt = db.get(RunAttempt, attempt.id)
        return _finish_failed(
            db, run, attempt, package, "EXECUTION_ERROR",
            f"Run failed during execution: {error}", {}, rows_written=True,
        )

    status = run_state.outcome_status(len(data.policies), failed_policies)
    metrics.update({
        "calculation_seconds": timing["calc"],
        "output_write_seconds": timing["outputs"],
        "trace_write_seconds": timing["traces"],
        "commit_seconds": timing["commit"],
        "total_seconds": perf_counter() - started,
    })
    if status == run_state.FAILED:
        # Every policy failed: nothing is canonical; remove whatever the attempt wrote.
        _delete_attempt_rows(db, run, attempt)
        attempt.error_type = "ALL_POLICIES_FAILED"
        attempt.error_message = errors[0] if errors else None
    now = now_utc()
    run_state.transition_run(run, status)
    run_state.transition_attempt(attempt, status)
    run.completed_at = attempt.completed_at = now
    if status in run_state.RESULT_STATUSES:
        run.accepted_attempt_number = attempt.attempt_number
        attempt.cleanup_status = "not_needed"
    attempt.output_row_count = counts["outputs"]
    attempt.trace_row_count = counts["traces"]
    attempt.metrics = {key: round(value, 4) for key, value in metrics.items()}
    run.summary = _build_summary(run, configuration, data, totals, counts, failed_policies, attempt)
    write_final_manifest(db, run, attempt, package, warnings, errors)
    succeeded = len(data.policies) - failed_policies
    add_event(
        db, run.id, "complete",
        f"Run completed: {succeeded} of {len(data.policies)} policies ({status}).",
        level="info" if failed_policies == 0 else "warning",
        data={"status": status, "attempt_number": attempt.attempt_number, "metrics": attempt.metrics},
    )
    db.commit()
    return status


def _delete_attempt_rows(db: Session, run: Run, attempt: RunAttempt) -> None:
    """Keep failed-attempt artifacts for evidence; accepted-attempt filtering hides them."""
    attempt.cleanup_status = "retained_noncanonical"


def _finish_failed(
    db: Session,
    run: Run,
    attempt: RunAttempt,
    package: RunPackage | None,
    error_type: str,
    message: str,
    details: dict,
    *,
    rows_written: bool,
) -> str:
    if rows_written:
        _delete_attempt_rows(db, run, attempt)
    else:
        attempt.cleanup_status = "not_needed"
    now = now_utc()
    run_state.transition_run(run, run_state.FAILED)
    run_state.transition_attempt(attempt, run_state.FAILED)
    run.completed_at = attempt.completed_at = now
    run.error_count = (run.error_count or 0) + 1
    attempt.error_type = error_type
    attempt.error_message = message
    add_event(db, run.id, "failed", message, level="error",
              data={"error_type": error_type, "attempt_number": attempt.attempt_number, **details})
    write_final_manifest(db, run, attempt, package, [], [message])
    db.commit()
    return run_state.FAILED


def _build_summary(
    run: Run,
    configuration: dict[str, Any],
    data,
    totals: dict[str, float],
    counts: dict[str, int],
    failed_policies: int,
    attempt: RunAttempt,
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
        "status": run.status,
        "complete": run.status == run_state.SUCCESS,
        "illustrative": illustrative,
        "attempt_number": attempt.attempt_number,
        "run_package_fingerprint": run.run_package_fingerprint,
        "scenario": {"id": data.scenario.id, "name": data.scenario.name},
        "valuation_date": iso(data.valuation_date),
        "policy_count": len(data.policies),
        "failed_policy_count": failed_policies,
        "period_count": data.horizon_months,
        "output_variable_count": len(data.output_variables),
        "output_row_count": counts["outputs"],
        "error_count": run.error_count,
        "warning_count": run.warning_count,
        "headline": headline,
        "totals": summary_totals,
        "trace": {
            "captured": bool(data.traced_policy_ids),
            "mode": (configuration["outputs"].get("trace_scope") or {}).get("mode", "none"),
            "captured_policy_count": len(data.traced_policy_ids),
            "policy_ids": sorted(data.traced_policy_ids),
        },
    }
