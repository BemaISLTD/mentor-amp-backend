"""Cancellation and retry controls for immutable packaged runs."""

import copy
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.execution import run_state
from app.core.execution.run_package import RunPackageError
from app.core.project_lifecycle import require_active_project
from app.db.models.projection import RunSet
from app.db.models.run import Run
from app.db.models.run_package import RunPackage
from app.services import run_package_verification, run_query_service
from app.services.common import ServiceError, iso, not_found, now_utc
from app.services.run_events import add_event
from app.services.run_manifest_service import write_final_manifest

RETRYABLE_STATUSES = frozenset({
    run_state.FAILED, run_state.CANCELLED, run_state.PARTIAL_SUCCESS,
})


def _locked_run(db: Session, run_id: str) -> Run:
    row = db.query(Run).filter(Run.id == run_id).with_for_update().first()
    if row is None:
        raise not_found(f"Run '{run_id}' not found.")
    return row


def _refresh_run_set(db: Session, run: Run) -> None:
    if not run.run_set_id:
        return
    from app.services.run_execution_service import refresh_run_set_status

    run_set = db.get(RunSet, run.run_set_id)
    if run_set is not None:
        refresh_run_set_status(db, run_set)


def cancel_run(db: Session, run_id: str, user: Any) -> dict[str, Any]:
    run = _locked_run(db, run_id)
    require_active_project(db, run.project_id)
    before = {"status": run.status, "cancel_requested_at": iso(run.cancel_requested_at)}
    now = now_utc()
    if run.status == run_state.PENDING:
        run_state.transition_run(run, run_state.CANCELLED)
        run.cancel_requested_at = now
        run.cancel_requested_by = user.id
        run.completed_at = now
        package = db.query(RunPackage).filter(RunPackage.run_id == run.id).first()
        write_final_manifest(db, run, None, package, [], [], environment=None)
        add_event(
            db, run.id, "cancelled", "Run cancelled before execution started.",
            data={"status": run_state.CANCELLED, "requested_by": user.id},
        )
        action = "run.cancelled"
    elif run.status == run_state.RUNNING:
        if run.cancel_requested_at is None:
            run.cancel_requested_at = now
            run.cancel_requested_by = user.id
            add_event(
                db, run.id, "cancel_requested",
                "Cancellation requested; the worker will stop between policies.",
                data={"requested_by": user.id},
            )
        action = "run.cancel_requested"
    else:
        db.rollback()
        raise ServiceError(
            409, "RUN_NOT_CANCELLABLE",
            f"Run '{run.id}' cannot be cancelled from status '{run.status}'.",
            {"status": run.status},
        )
    _refresh_run_set(db, run)
    record_audit(
        db, actor_user_id=user.id, action=action, entity_type="run", entity_id=run.id,
        before_state=before,
        after_state={"status": run.status, "cancel_requested_at": iso(run.cancel_requested_at)},
    )
    db.commit()
    db.refresh(run)
    return run_query_service.run_view(db, run)


def retry_run(db: Session, run_id: str, user: Any) -> dict[str, Any]:
    source = _locked_run(db, run_id)
    require_active_project(db, source.project_id)
    if source.status not in RETRYABLE_STATUSES:
        db.rollback()
        raise ServiceError(
            409, "RUN_NOT_RETRYABLE",
            f"Run '{source.id}' cannot be retried from status '{source.status}'.",
            {"status": source.status},
        )
    package = db.query(RunPackage).filter(RunPackage.run_id == source.id).first()
    if package is None:
        db.rollback()
        raise ServiceError(
            409, "PACKAGE_MISSING", "The run has no frozen package and cannot be retried.",
        )
    configuration = package.package.get("configuration") or {}
    try:
        run_package_verification.verify_build(configuration)
    except RunPackageError as error:
        db.rollback()
        raise ServiceError(409, error.code, error.message, error.details) from error

    new_run_id = str(uuid.uuid4())
    new_package_id = str(uuid.uuid4())
    now = now_utc()
    document = copy.deepcopy(package.package)
    document["identity"] = {
        **document["identity"],
        "package_id": new_package_id,
        "frozen_for_run_id": new_run_id,
        "run_set_id": source.run_set_id,
        "created_at": iso(now),
        "created_by": {"id": user.id, "full_name": getattr(user, "full_name", None)},
    }
    retried = Run(
        id=new_run_id,
        project_id=source.project_id,
        name=f"{source.name or 'Run'} (retry)",
        projection_key=source.projection_key,
        status=run_state.PENDING,
        run_set_id=source.run_set_id,
        projection_set_id=source.projection_set_id,
        model_version_id=source.model_version_id,
        scenario_id=source.scenario_id,
        valuation_date=source.valuation_date,
        horizon_months=source.horizon_months,
        illustrative=source.illustrative,
        triggered_by=user.id,
        run_package_fingerprint=source.run_package_fingerprint,
        retried_from_run_id=source.id,
    )
    db.add(retried)
    db.flush()
    db.add(RunPackage(
        id=new_package_id,
        run_id=retried.id,
        project_id=package.project_id,
        schema_version=package.schema_version,
        fingerprint_algorithm=package.fingerprint_algorithm,
        fingerprint=package.fingerprint,
        package=document,
        created_by=user.id,
    ))
    add_event(
        db, retried.id, "queued",
        f"Retry queued from run {source.id} with the same frozen configuration.",
        data={
            "retried_from_run_id": source.id,
            "run_package_fingerprint": package.fingerprint,
        },
    )
    add_event(
        db, source.id, "retried", f"Retry created as run {retried.id}.",
        data={"retry_run_id": retried.id},
    )
    _refresh_run_set(db, retried)
    record_audit(
        db, actor_user_id=user.id, action="run.retried", entity_type="run",
        entity_id=retried.id,
        after_state={
            "status": retried.status, "retried_from_run_id": source.id,
            "run_package_fingerprint": retried.run_package_fingerprint,
        },
        context={"source_run_id": source.id},
    )
    db.commit()
    db.refresh(retried)
    return run_query_service.run_view(db, retried)
