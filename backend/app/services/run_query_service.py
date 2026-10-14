"""Read side of runs: views, lists, events, attempts, run packages and final manifests.

List endpoints only return runs of projects the caller may access; single-object endpoints are
authorized by the router (``authorize_path``) before these functions run.
"""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.execution import run_state
from app.db.models.modeling import Model, ModelVersion
from app.db.models.projection import ProjectionSet, RunEvent, RunSet
from app.db.models.run import Run
from app.db.models.run_artifact import RunManifest
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.scenario import ScenarioTable
from app.services import access
from app.services.common import iso, not_found, now_utc, user_ref


class Lookups:
    """Caches small related objects while serialising many runs."""

    def __init__(self, db: Session):
        self.db = db
        self._cache: dict[tuple[type, str], Any] = {}

    def get(self, model: type, key: str | None) -> Any:
        if not key:
            return None
        cache_key = (model, key)
        if cache_key not in self._cache:
            self._cache[cache_key] = self.db.get(model, key)
        return self._cache[cache_key]


def get_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise not_found(f"Run '{run_id}' not found.")
    return run


def results_available(db: Session, run: Run) -> bool:
    from app.services.run_finalization import evidence_consistent  # local: avoids an import cycle

    return evidence_consistent(db, run)


def run_view(db: Session, run: Run, lookups: Lookups | None = None, include_summary: bool = True) -> dict:
    lookups = lookups or Lookups(db)
    run_set = lookups.get(RunSet, run.run_set_id)
    projection_set = lookups.get(ProjectionSet, run.projection_set_id)
    model_version = lookups.get(ModelVersion, run.model_version_id)
    model = lookups.get(Model, model_version.model_id) if model_version else None
    scenario = lookups.get(ScenarioTable, run.scenario_id)

    total = run.progress_total or 0
    done = run.progress_done or 0
    percent = round(done / total * 100, 1) if total else 0.0
    duration = None
    eta = None
    if run.started_at:
        end = run.completed_at or now_utc()
        started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=end.tzinfo)
        duration = round((end - started).total_seconds(), 1)
        if run.status == run_state.RUNNING and done and total > done:
            eta = int(duration / done * (total - done))

    return {
        "id": run.id,
        "name": run.name,
        "project_id": run.project_id,
        "run_set": {"id": run_set.id, "name": run_set.name} if run_set else None,
        "projection_set": (
            {"id": projection_set.id, "name": projection_set.name, "version_label": projection_set.version_label}
            if projection_set else None
        ),
        "model_version": (
            {
                "id": model_version.id,
                "model_name": model.name if model else None,
                "version_label": model_version.version_label,
                "basis": model_version.basis,
                "methodology": model_version.methodology,
                "product_code": model.product_code if model else None,
            }
            if model_version else None
        ),
        "scenario": {"id": scenario.id, "name": scenario.scenario_name} if scenario else None,
        "valuation_date": iso(run.valuation_date),
        "horizon_months": run.horizon_months,
        "status": run.status,
        "progress": {
            "total": total,
            "done": done,
            "percent": percent,
            "unit": "policies",
            "eta_seconds": eta,
        },
        "error_count": run.error_count or 0,
        "warning_count": run.warning_count or 0,
        "compute": {"backend": "cpu", "description": "Local CPU reference path"},
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "duration_seconds": duration,
        "triggered_by": user_ref(db, run.triggered_by),
        "run_package_fingerprint": run.run_package_fingerprint,
        "final_manifest_fingerprint": run.final_manifest_fingerprint,
        # Deprecated alias (M1 name) of run_package_fingerprint; kept for existing clients.
        "manifest_fingerprint": run.run_package_fingerprint,
        "legacy_run": run.run_package_fingerprint is None,
        "attempt_count": run.attempt_count or 0,
        "accepted_attempt_number": run.accepted_attempt_number,
        "cancel_requested_at": iso(run.cancel_requested_at),
        "cancel_requested_by": user_ref(db, run.cancel_requested_by),
        "retried_from_run_id": run.retried_from_run_id,
        "can_cancel": run.status in run_state.ACTIVE_STATUSES,
        "can_retry": run.status in {
            run_state.FAILED, run_state.CANCELLED, run_state.PARTIAL_SUCCESS,
        },
        "results_available": (available := results_available(db, run)),
        "results_complete": available and run.status == run_state.SUCCESS,
        "illustrative": bool(run.illustrative),
        "summary": run.summary if include_summary else None,
    }


def list_runs(
    db: Session,
    user: Any,
    project_id: str | None = None,
    run_set_id: str | None = None,
    model_version_id: str | None = None,
    projection_set_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    query = db.query(Run)
    if project_id:
        access.require_project_access(db, user, project_id)
        query = query.filter(Run.project_id == project_id)
    else:
        allowed = access.accessible_project_ids(db, user)
        if allowed is not None:
            query = query.filter(Run.project_id.in_(allowed or [""]))
    if run_set_id:
        query = query.filter(Run.run_set_id == run_set_id)
    if model_version_id:
        query = query.filter(Run.model_version_id == model_version_id)
    if projection_set_id:
        query = query.filter(Run.projection_set_id == projection_set_id)
    if status:
        query = query.filter(Run.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    total = query.count()
    rows = query.order_by(Run.created_at.desc(), Run.name).offset(offset).limit(limit).all()
    lookups = Lookups(db)
    return {
        "runs": [run_view(db, run, lookups, include_summary=False) for run in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _run_set_summary(db: Session, run_set: RunSet, lookups: Lookups) -> dict[str, Any]:
    runs = db.query(Run).filter(Run.run_set_id == run_set.id).order_by(Run.created_at, Run.name).all()
    by_status: dict[str, int] = {}
    for run in runs:
        by_status[run.status] = by_status.get(run.status, 0) + 1
    scenario_names: list[str] = []
    for run in runs:
        scenario = lookups.get(ScenarioTable, run.scenario_id)
        if scenario and scenario.scenario_name not in scenario_names:
            scenario_names.append(scenario.scenario_name)
    projection_sets = []
    for ps_id in run_set.projection_set_ids or []:
        ps = lookups.get(ProjectionSet, ps_id)
        projection_sets.append({"id": ps_id, "name": ps.name if ps else None})
    return {
        "id": run_set.id,
        "name": run_set.name,
        "notes": run_set.notes,
        "status": run_set.status,
        "projection_sets": projection_sets,
        "scenario_names": scenario_names,
        # The scenarios actually submitted, and how they were resolved per Projection Set.
        "scenario_ids": list(run_set.scenario_ids or []),
        "resolution": run_set.resolution,
        "run_count": len(runs),
        "runs_by_status": by_status,
        "report_count": 0,
        "submitted_at": iso(run_set.submitted_at),
        "completed_at": iso(run_set.completed_at),
        "illustrative": any(run.illustrative for run in runs),
        "_runs": runs,
    }


def list_run_sets(db: Session, project_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    query = db.query(RunSet).filter(RunSet.project_id == project_id)
    total = query.count()
    rows = query.order_by(RunSet.created_at.desc()).offset(offset).limit(limit).all()
    lookups = Lookups(db)
    items = []
    for run_set in rows:
        item = _run_set_summary(db, run_set, lookups)
        item.pop("_runs")
        items.append(item)
    stats = {
        "run_set_count": total,
        "ready_projection_set_count": db.query(func.count(ProjectionSet.id))
        .filter(ProjectionSet.project_id == project_id, ProjectionSet.status == "validated")
        .scalar(),
        "active_run_count": db.query(func.count(Run.id))
        .filter(Run.project_id == project_id, Run.status.in_(sorted(run_state.ACTIVE_STATUSES)))
        .scalar(),
        "needs_recovery_run_count": db.query(func.count(Run.id))
        .filter(Run.project_id == project_id,
                Run.status.in_([run_state.FAILED, run_state.PARTIAL_SUCCESS]))
        .scalar(),
    }
    return {"run_sets": items, "total": total, "limit": limit, "offset": offset, "stats": stats}


def get_run_set(db: Session, run_set_id: str) -> dict[str, Any]:
    run_set = db.get(RunSet, run_set_id)
    if run_set is None:
        raise not_found(f"Run Set '{run_set_id}' not found.")
    lookups = Lookups(db)
    item = _run_set_summary(db, run_set, lookups)
    runs = item.pop("_runs")
    item["runs"] = [run_view(db, run, lookups, include_summary=False) for run in runs]
    return item


def list_events(db: Session, run_id: str, after_id: int = 0, limit: int = 200) -> dict[str, Any]:
    get_run(db, run_id)
    rows = (
        db.query(RunEvent)
        .filter(RunEvent.run_id == run_id, RunEvent.id > after_id)
        .order_by(RunEvent.id)
        .limit(limit)
        .all()
    )
    return {
        "events": [
            {
                "id": row.id,
                "created_at": iso(row.created_at),
                "level": row.level,
                "step": row.step,
                "message": row.message,
                "data": row.data,
            }
            for row in rows
        ],
        "last_id": rows[-1].id if rows else after_id,
    }


def list_attempts(db: Session, run_id: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    rows = (
        db.query(RunAttempt)
        .filter(RunAttempt.run_id == run_id)
        .order_by(RunAttempt.attempt_number)
        .all()
    )
    return {
        "run_id": run.id,
        "accepted_attempt_number": run.accepted_attempt_number,
        "can_cancel": run.status in run_state.ACTIVE_STATUSES,
        "can_retry": run.status in {
            run_state.FAILED, run_state.CANCELLED, run_state.PARTIAL_SUCCESS,
        },
        "attempts": [
            {
                "attempt_number": row.attempt_number,
                "status": row.status,
                "accepted": row.attempt_number == run.accepted_attempt_number,
                "worker_id": row.worker_id,
                "started_at": iso(row.started_at),
                "heartbeat_at": iso(row.heartbeat_at),
                "completed_at": iso(row.completed_at),
                "error_type": row.error_type,
                "error_message": row.error_message,
                "cleanup_status": row.cleanup_status,
                "output_row_count": row.output_row_count,
                "trace_row_count": row.trace_row_count,
                "executed_build": row.executed_build,
                "metrics": row.metrics,
            }
            for row in rows
        ],
    }


def get_package_row(db: Session, run_id: str) -> RunPackage | None:
    return db.query(RunPackage).filter(RunPackage.run_id == run_id).first()


def get_package(db: Session, run_id: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    package = get_package_row(db, run_id)
    if package is None:
        raise not_found(
            f"Run '{run_id}' has no run package (it was submitted before run packages existed).",
            reason="LEGACY_RUN_WITHOUT_PACKAGE",
        )
    return {
        "run_id": run.id,
        "run_package_id": package.id,
        "fingerprint": package.fingerprint,
        "schema_version": package.schema_version,
        "created_at": iso(package.created_at),
        "package": package.package,
    }


def get_manifest(db: Session, run_id: str) -> dict[str, Any]:
    """Package identity (always, if any) and the final manifest (once the run has finished)."""
    run = get_run(db, run_id)
    package = get_package_row(db, run_id)
    final = db.get(RunManifest, run_id)
    if package is None and final is None:
        raise not_found(f"Manifest for run '{run_id}' not found.")
    final_view = (
        {
            "fingerprint": final.fingerprint,
            "schema_version": final.schema_version,
            "attempt_number": final.attempt_number,
            "created_at": iso(final.created_at),
            "manifest": final.manifest,
        }
        if final else None
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "legacy_run": package is None,
        "run_package": (
            {"id": package.id, "fingerprint": package.fingerprint, "schema_version": package.schema_version}
            if package else None
        ),
        "final_manifest": final_view,
        # Deprecated M1 aliases: "fingerprint" is the run package fingerprint (the configuration
        # identity); "manifest" is the final manifest, or null until the run has finished.
        "fingerprint": package.fingerprint if package else None,
        "manifest": final.manifest if final else None,
    }


# =============================================================================
# The configuration a run executed with
# =============================================================================

def run_configuration(db: Session, run: Run) -> dict[str, Any]:
    """The frozen configuration of a run (its package), or a best-effort view for legacy runs.

    Legacy M1 runs (submitted before run packages) only have the manifest migrated into
    ``run_manifests``; their configuration view is marked ``"legacy": True``.
    """
    package = get_package_row(db, run.id)
    if package is not None:
        return package.package["configuration"]
    return _legacy_configuration(db, run)


def _legacy_configuration(db: Session, run: Run) -> dict[str, Any]:
    """Best-effort view for legacy runs: their migrated manifest plus the run's own model-version
    variable definitions (never the global catalog)."""
    from app.services.model_definition import variable_specs  # local: only legacy runs need it

    final = db.get(RunManifest, run.id)
    old = dict((final.manifest if final else None) or {})
    specs = variable_specs(db, run.model_version_id) if run.model_version_id else {}
    variables = []
    for item in old.get("variables") or []:
        spec = specs.get(item.get("name"))
        variables.append({
            "name": item.get("name"),
            "kind": item.get("kind"),
            "version": item.get("version"),
            "source": item.get("source") or (dict(spec.source) if spec else {}),
            "default_value": spec.default_value if spec else None,
            "unit": spec.unit if spec else None,
            "display_name": spec.display_name if spec else None,
            "content_fingerprint": None,
        })
    return {
        "legacy": True,
        "project": {"id": run.project_id},
        "projection_set": old.get("projection_set") or {},
        "model_version": old.get("model_version") or {},
        "model": {"product_code": (old.get("model_version") or {}).get("product_code")},
        "parameters": {},
        "formulas": [
            {**formula, "content_fingerprint": None} for formula in old.get("formulas") or []
        ],
        "variables": variables,
        "datasets": {
            "inforce": [d for d in old.get("datasets") or []],
            "assumption_tables": list(old.get("assumption_tables") or []),
            "factor_tables": [],
        },
        "scenario": old.get("scenario") or {},
        "outputs": {
            "output_variables": list(old.get("output_variables") or []),
            "trace_scope": old.get("trace_scope") or {},
            "published": {},
        },
        "build": {"code_version": old.get("code_version"), "engine_version": old.get("engine_version")},
    }
