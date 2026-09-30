"""Run Sets and runs: preflight, submission, background execution, views (contract §E.7, §F.1)."""

import logging
import platform
from typing import Any

from sqlalchemy import func, insert
from sqlalchemy.orm import Session

from app.config import settings
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import EngineError, run_policy
from app.db.database import SessionLocal
from app.db.models.inforce import InforceFile
from app.db.models.assumption import AssumptionTable
from app.db.models.modeling import Model, ModelPublishedOutput, ModelVersion
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet, RunEvent, RunSet
from app.db.models.run import Run
from app.db.models.run_output import RunOutput
from app.db.models.scenario import ScenarioTable
from app.db.models.trace_log import TraceLog
from app.products.registry import register_all_products
from app.services.common import (
    TERMINAL_RUN_STATUSES,
    ServiceError,
    bad_request,
    conflict,
    fingerprint,
    iso,
    not_found,
    now_utc,
    user_ref,
)
from app.services.run_loader import (
    load_model_formulas,
    load_run_data,
    load_variable_closure,
)

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"
ENGINE_VERSION = "m1-cpu-1"
FLUSH_EVERY_POLICIES = 5
# needs_review = validated with warnings only; warnings do not block a run.
RUNNABLE_STATUSES = {"validated", "needs_review"}
TOTAL_LABELS = {
    "expected_payment": "Total expected payments (undiscounted)",
    "pv_expected_payment": "PV of expected payments",
}


# =============================================================================
# Helpers
# =============================================================================

def add_event(
    db: Session, run_id: str, step: str, message: str, level: str = "info", data: Any = None
) -> None:
    db.add(RunEvent(run_id=run_id, step=step, message=message, level=level, data=data))


def _get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise not_found(f"Project '{project_id}' not found.")
    return project


def _published_outputs(db: Session, model_version_id: str | None) -> dict[str, ModelPublishedOutput]:
    if not model_version_id:
        return {}
    rows = (
        db.query(ModelPublishedOutput)
        .filter(ModelPublishedOutput.model_version_id == model_version_id)
        .all()
    )
    return {row.variable_name: row for row in rows}


def derive_run_set_status(statuses: list[str]) -> str:
    if not statuses:
        return "queued"
    if all(status == "pending" for status in statuses):
        return "queued"
    if any(status in ("pending", "running") for status in statuses):
        return "running"
    if all(status == "success" for status in statuses):
        return "success"
    if all(status == "failed" for status in statuses):
        return "failed"
    return "partial_success"


def refresh_run_set_status(db: Session, run_set: RunSet) -> None:
    statuses = [row[0] for row in db.query(Run.status).filter(Run.run_set_id == run_set.id).all()]
    run_set.status = derive_run_set_status(statuses)
    if run_set.status not in ("queued", "running") and run_set.completed_at is None:
        run_set.completed_at = now_utc()


# =============================================================================
# Manifest (contract §F.9)
# =============================================================================

def build_manifest(
    db: Session,
    project: Project,
    projection_set: ProjectionSet,
    model_version: ModelVersion,
    model: Model,
    scenario: ScenarioTable | None,
    user: Any,
) -> tuple[dict[str, Any], str]:
    formulas = load_model_formulas(db, model_version.id)
    variables = load_variable_closure(db, formulas, list(projection_set.output_variables or []))
    files = (
        db.query(InforceFile).filter(InforceFile.id.in_(projection_set.inforce_file_ids or [])).all()
        if projection_set.inforce_file_ids
        else []
    )
    tables = (
        db.query(AssumptionTable)
        .filter(AssumptionTable.id.in_(projection_set.assumption_table_ids or []))
        .all()
        if projection_set.assumption_table_ids
        else []
    )
    inputs: dict[str, Any] = {
        "schema_version": "m1",
        "app_version": APP_VERSION,
        "engine_version": ENGINE_VERSION,
        "code_version": settings.code_version,
        "project": {"id": project.id, "name": project.name},
        "model_version": {
            "id": model_version.id,
            "model_id": model.id,
            "model_name": model.name,
            "version_label": model_version.version_label,
            "product_code": model.product_code,
            "basis": model_version.basis,
            "methodology": model_version.methodology,
        },
        "projection_set": {
            "id": projection_set.id,
            "name": projection_set.name,
            "version_label": projection_set.version_label,
        },
        "scenario": (
            {
                "id": scenario.id,
                "name": scenario.scenario_name,
                "fingerprint": scenario.fingerprint,
                "overrides": list(scenario.overrides or []),
            }
            if scenario
            else None
        ),
        "valuation_date": iso(projection_set.valuation_date),
        "horizon_months": projection_set.horizon_months,
        "time_step": projection_set.time_step,
        "formulas": [
            {
                "id": row.id,
                "output_variable": row.output_variable,
                "function_ref": row.function_ref,
                "version": row.version,
                "expression_text": row.expression_text,
                "dependencies": sorted(dep.depends_on_variable for dep in row.dependencies),
            }
            for row in sorted(formulas, key=lambda item: item.output_variable)
        ],
        "variables": [
            {"name": spec.name, "kind": spec.source_type, "version": spec.version, "source": spec.source}
            for spec in sorted(variables.values(), key=lambda item: item.name)
        ],
        "datasets": [
            {
                "type": "liability_inforce",
                "id": file.id,
                "name": file.filename,
                "record_count": file.row_count,
                "fingerprint": file.fingerprint,
            }
            for file in sorted(files, key=lambda item: item.id)
        ],
        "assumption_tables": [
            {"id": table.id, "name": table.table_name, "fingerprint": table.fingerprint}
            for table in sorted(tables, key=lambda item: item.id)
        ],
        "output_variables": list(projection_set.output_variables or []),
        "trace_scope": dict(projection_set.trace_scope or {}),
        "execution_backend": "cpu",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
        },
        "illustrative": bool(model_version.illustrative),
        "not_yet_captured": ["approval_state", "factor_set", "methodology_version", "code_commit"],
    }
    run_fingerprint = fingerprint({k: v for k, v in inputs.items() if k != "environment"})
    manifest = {**inputs, "user": {"id": user.id, "full_name": user.full_name}, "outcome": None}
    return manifest, run_fingerprint


# =============================================================================
# Preflight and submission
# =============================================================================

def _resolve_request(db: Session, payload: dict) -> tuple[Project, list[tuple[ProjectionSet, list[ScenarioTable]]]]:
    project = _get_project(db, payload["project_id"])
    projection_set_ids: list[str] = list(payload.get("projection_set_ids") or [])
    if not projection_set_ids:
        raise ServiceError(422, "VALIDATION_ERROR", "Select at least one Projection Set.")
    requested_scenarios: list[str] = list(payload.get("scenario_ids") or [])
    plan: list[tuple[ProjectionSet, list[ScenarioTable]]] = []
    for projection_set_id in projection_set_ids:
        projection_set = db.get(ProjectionSet, projection_set_id)
        if projection_set is None or projection_set.project_id != project.id:
            raise not_found(f"Projection Set '{projection_set_id}' not found in this project.")
        scenario_ids = requested_scenarios or list(projection_set.scenario_ids or [])
        scenarios: list[ScenarioTable] = []
        for scenario_id in scenario_ids:
            scenario = db.get(ScenarioTable, scenario_id)
            if scenario is None:
                raise not_found(f"Scenario '{scenario_id}' not found.")
            scenarios.append(scenario)
        plan.append((projection_set, scenarios))
    return project, plan


def preflight(db: Session, payload: dict) -> dict[str, Any]:
    project, plan = _resolve_request(db, payload)
    del project
    register_all_products()
    checks: list[dict[str, Any]] = []
    validated = [ps for ps, _ in plan if ps.status in RUNNABLE_STATUSES]
    checks.append({
        "code": "projection_sets_validated",
        "label": "Projection compatibility",
        "status": "pass" if len(validated) == len(plan) else "fail",
        "message": f"{len(validated)} of {len(plan)} Projection Sets validated",
    })
    file_count = sum(len(ps.inforce_file_ids or []) for ps, _ in plan)
    table_count = sum(len(ps.assumption_table_ids or []) for ps, _ in plan)
    checks.append({
        "code": "inputs_pinned",
        "label": "Input versions pinned",
        "status": "pass" if file_count else "fail",
        "message": f"{file_count} inforce file(s), {table_count} assumption table(s) (fingerprinted)",
    })
    missing_functions: list[str] = []
    formula_count = 0
    for ps, _ in plan:
        if ps.model_version_id:
            for row in load_model_formulas(db, ps.model_version_id):
                formula_count += 1
                if row.function_ref not in FORMULA_FUNCTIONS:
                    missing_functions.append(row.function_ref)
    checks.append({
        "code": "formulas_ready",
        "label": "Formula lineage captured",
        "status": "fail" if missing_functions or not formula_count else "pass",
        "message": (
            f"Missing functions: {', '.join(sorted(set(missing_functions)))}"
            if missing_functions
            else f"{formula_count} formulas registered; graph valid"
        ),
    })
    scenario_count = sum(len(scenarios) for _, scenarios in plan)
    checks.append({
        "code": "scenarios_valid",
        "label": "Scenarios valid",
        "status": "pass" if scenario_count else "fail",
        "message": f"{scenario_count} scenario run(s)" if scenario_count else "No scenarios selected",
    })
    checks.append({
        "code": "reports_attached",
        "label": "Report attachment valid",
        "status": "pass",
        "message": "No reports attached (reports arrive later)",
    })
    planned = [
        {
            "projection_set_id": ps.id,
            "scenario_id": scenario.id,
            "name": f"{ps.name} · {scenario.scenario_name}",
        }
        for ps, scenarios in plan
        for scenario in scenarios
    ]
    return {
        "ok": all(check["status"] != "fail" for check in checks),
        "checks": checks,
        "planned_runs": planned,
    }


def submit_run_set(db: Session, payload: dict, user: Any) -> dict[str, Any]:
    project, plan = _resolve_request(db, payload)
    for projection_set, scenarios in plan:
        if projection_set.status not in RUNNABLE_STATUSES:
            raise conflict(
                f"Projection Set '{projection_set.name}' is not validated "
                f"(status: {projection_set.status}). Validate it first.",
                reason="PROJECTION_SET_NOT_VALIDATED",
                projection_set_id=projection_set.id,
            )
        if not scenarios:
            raise bad_request(f"Projection Set '{projection_set.name}' has no scenarios to run.")

    submitted_at = now_utc()
    run_set = RunSet(
        project_id=project.id,
        name=payload.get("name") or "Run Set",
        notes=payload.get("notes") or None,
        projection_set_ids=[ps.id for ps, _ in plan],
        scenario_ids=list(payload.get("scenario_ids") or []),
        status="queued",
        created_by=user.id,
        submitted_at=submitted_at,
    )
    db.add(run_set)
    db.flush()

    created: list[Run] = []
    for projection_set, scenarios in plan:
        model_version = db.get(ModelVersion, projection_set.model_version_id)
        model = db.get(Model, model_version.model_id) if model_version else None
        if model_version is None or model is None:
            raise conflict(f"Projection Set '{projection_set.name}' has no model version.")
        for scenario in scenarios:
            manifest, run_fingerprint = build_manifest(
                db, project, projection_set, model_version, model, scenario, user
            )
            run = Run(
                project_id=project.id,
                name=f"{projection_set.name} · {scenario.scenario_name}",
                projection_key=f"{projection_set.name}@{projection_set.version_label}",
                status="pending",
                run_set_id=run_set.id,
                projection_set_id=projection_set.id,
                model_version_id=model_version.id,
                scenario_id=scenario.id,
                valuation_date=projection_set.valuation_date,
                horizon_months=projection_set.horizon_months,
                manifest=manifest,
                manifest_fingerprint=run_fingerprint,
                illustrative=bool(model_version.illustrative),
                triggered_by=user.id,
            )
            db.add(run)
            db.flush()
            add_event(db, run.id, "queued", "Run queued.")
            created.append(run)
    db.commit()

    names = {ps.id: ps.name for ps, _ in plan}
    scenario_names = {scenario.id: scenario.scenario_name for _, s in plan for scenario in s}
    return {
        "run_set": {
            "id": run_set.id,
            "name": run_set.name,
            "status": run_set.status,
            "run_count": len(created),
            "submitted_at": iso(submitted_at),
        },
        "runs": [
            {
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "scenario": {"id": run.scenario_id, "name": scenario_names.get(run.scenario_id)},
                "projection_set": {"id": run.projection_set_id, "name": names.get(run.projection_set_id)},
            }
            for run in created
        ],
    }


# =============================================================================
# Execution (background)
# =============================================================================

def execute_run_set(run_set_id: str) -> None:
    """Execute every pending run of a Run Set, one after another, in its own database session."""
    register_all_products()
    db = SessionLocal()
    try:
        run_set = db.get(RunSet, run_set_id)
        if run_set is None:
            return
        run_ids = [
            row[0]
            for row in db.query(Run.id)
            .filter(Run.run_set_id == run_set_id)
            .order_by(Run.created_at, Run.name)
            .all()
        ]
        run_set.status = "running"
        db.commit()
        for run_id in run_ids:
            execute_run(db, run_id)
        run_set = db.get(RunSet, run_set_id)
        refresh_run_set_status(db, run_set)
        db.commit()
    except Exception:  # noqa: BLE001 - background task must never crash silently
        logger.exception("Run Set %s failed", run_set_id)
        db.rollback()
    finally:
        db.close()


def _fail_run(db: Session, run: Run, error_type: str, message: str) -> None:
    run.status = "failed"
    run.completed_at = now_utc()
    run.error_count = (run.error_count or 0) + 1
    add_event(db, run.id, "failed", message, level="error", data={"error_type": error_type})
    _finalize_manifest(run, [])
    db.commit()


def _finalize_manifest(run: Run, warnings: list[str]) -> None:
    manifest = dict(run.manifest or {})
    manifest["outcome"] = {
        "status": run.status,
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "warnings": run.warning_count,
        "errors": run.error_count,
        "warning_messages": warnings[:20],
    }
    run.manifest = manifest


def execute_run(db: Session, run_id: str) -> None:
    run = db.get(Run, run_id)
    if run is None or run.status != "pending":
        return
    run.status = "running"
    run.started_at = now_utc()
    db.commit()

    try:
        data = load_run_data(db, run)
    except EngineError as error:
        _fail_run(db, run, error.error_type, f"Could not load the run: {error.message}")
        return
    except Exception as error:  # noqa: BLE001
        logger.exception("Loading run %s failed", run_id)
        db.rollback()
        run = db.get(Run, run_id)
        _fail_run(db, run, "invalid_formula", f"Could not load the run: {error}")
        return

    if not data.policies:
        _fail_run(db, run, "missing_value", "The Projection Set's inforce files contain no policies.")
        return

    model_version = db.get(ModelVersion, run.model_version_id) if run.model_version_id else None
    product_code = None
    if model_version is not None:
        model = db.get(Model, model_version.model_id)
        product_code = model.product_code if model else None

    run.progress_total = len(data.policies)
    add_event(
        db, run.id, "load",
        f"Loaded {len(data.policies)} policies, {len(data.variables)} variables, "
        f"{len(data.formulas)} formulas, {len(data.tables)} table(s).",
        data={"policies": len(data.policies), "formulas": len(data.formulas)},
    )
    db.commit()

    output_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    output_row_count = 0
    failed_policies = 0
    all_warnings: list[str] = []

    def flush() -> None:
        # Table-level (Core) inserts send true multi-row batches. The ORM bulk-insert path
        # groups rows by which columns are None, which splits mixed trace rows into batches of
        # one or two rows — thousands of round trips against a remote database such as Neon.
        nonlocal output_rows, trace_rows
        if output_rows:
            db.execute(insert(RunOutput.__table__), output_rows)
        if trace_rows:
            db.execute(insert(TraceLog.__table__), trace_rows)
        output_rows, trace_rows = [], []
        db.commit()

    try:
        for index, policy in enumerate(data.policies, start=1):
            result = run_policy(data, policy, FORMULA_FUNCTIONS)
            if result.error is not None:
                failed_policies += 1
                run.error_count = (run.error_count or 0) + 1
                add_event(
                    db, run.id, "policy",
                    f"Policy {policy.policy_id} failed at month {result.error.month}: "
                    f"{result.error.message}",
                    level="error",
                    data={"policy_id": policy.policy_id, **result.error.as_dict()},
                )
            else:
                created_at = now_utc()
                for month, variable, value in result.outputs:
                    output_rows.append({
                        "run_id": run.id,
                        "policy_id": policy.policy_id,
                        "scenario_id": run.scenario_id or "",
                        "projection_month": month,
                        "variable_name": variable,
                        "value": {"value": value},
                        "product": product_code,
                        "created_at": created_at,
                    })
                output_row_count += len(result.outputs)
                for name, value in result.sums.items():
                    totals[name] = totals.get(name, 0.0) + value
                add_event(
                    db, run.id, "policy",
                    f"Policy {policy.policy_id} complete ({result.months_computed} months).",
                    data={"policy_id": policy.policy_id},
                )
            for warning in result.warnings:
                run.warning_count = (run.warning_count or 0) + 1
                all_warnings.append(f"{policy.policy_id}: {warning}")
                add_event(
                    db, run.id, "policy", f"{policy.policy_id}: {warning}", level="warning",
                    data={"policy_id": policy.policy_id},
                )
            for row in result.trace_rows:
                trace_rows.append({
                    **row,
                    "run_id": run.id,
                    "scenario_id": run.scenario_id or "",
                    "created_at": now_utc(),
                })
            run.progress_done = index
            if index % FLUSH_EVERY_POLICIES == 0 or index == len(data.policies):
                flush()
    except Exception as error:  # noqa: BLE001
        logger.exception("Run %s failed during execution", run_id)
        db.rollback()
        run = db.get(Run, run_id)
        _fail_run(db, run, "invalid_formula", f"Run failed during execution: {error}")
        return

    succeeded = len(data.policies) - failed_policies
    if failed_policies == 0:
        run.status = "success"
    elif succeeded == 0:
        run.status = "failed"
    else:
        run.status = "partial_success"
    run.completed_at = now_utc()
    run.summary = _build_summary(
        db, run, data, totals, output_row_count, failed_policies
    )
    _finalize_manifest(run, all_warnings)
    add_event(
        db, run.id, "complete",
        f"Run completed: {succeeded} of {len(data.policies)} policies.",
        level="info" if failed_policies == 0 else "warning",
        data={"status": run.status},
    )
    db.commit()


def _build_summary(
    db: Session,
    run: Run,
    data,
    totals: dict[str, float],
    output_row_count: int,
    failed_policies: int,
) -> dict[str, Any]:
    published = _published_outputs(db, run.model_version_id)
    valuation_names = [spec.name for spec in data.valuation_variables()]
    headline = None
    for name in valuation_names:
        if name in data.output_variables and f"{name}_0" in totals:
            unit = published[name].unit if name in published else "USD"
            headline = {
                "metric": name,
                "label": "Reserve at valuation (illustrative)" if run.illustrative
                else "Reserve at valuation",
                "value": totals[f"{name}_0"],
                "unit": unit,
                "as_of": iso(data.valuation_date),
            }
            break
    summary_totals: dict[str, Any] = {}
    for name in data.output_variables:
        output = published.get(name)
        if output is None or output.aggregation != "sum" or output.unit != "USD" or name not in totals:
            continue
        summary_totals[name] = {
            "label": TOTAL_LABELS.get(name, f"Total {output.display_name}"),
            "value": totals[name],
            "unit": output.unit,
        }
    return {
        "run_id": run.id,
        "status": run.status,
        "illustrative": bool(run.illustrative),
        "scenario": {"id": data.scenario.id, "name": data.scenario.name},
        "valuation_date": iso(data.valuation_date),
        "policy_count": len(data.policies),
        "failed_policy_count": failed_policies,
        "period_count": data.horizon_months,
        "output_variable_count": len(data.output_variables),
        "output_row_count": output_row_count,
        "error_count": run.error_count,
        "warning_count": run.warning_count,
        "headline": headline,
        "totals": summary_totals,
        "trace": {
            "captured": bool(data.traced_policy_ids),
            "mode": (run.manifest or {}).get("trace_scope", {}).get("mode", "none"),
            "captured_policy_count": len(data.traced_policy_ids),
            "policy_ids": sorted(data.traced_policy_ids),
        },
    }


# =============================================================================
# Views
# =============================================================================

class _Lookups:
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


def run_view(db: Session, run: Run, lookups: _Lookups | None = None, include_summary: bool = True) -> dict:
    lookups = lookups or _Lookups(db)
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
        if run.status == "running" and done and total > done:
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
        "manifest_fingerprint": run.manifest_fingerprint,
        "illustrative": bool(run.illustrative),
        "summary": run.summary if include_summary else None,
    }


def get_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise not_found(f"Run '{run_id}' not found.")
    return run


def list_runs(
    db: Session,
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
        query = query.filter(Run.project_id == project_id)
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
    lookups = _Lookups(db)
    return {
        "runs": [run_view(db, run, lookups, include_summary=False) for run in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _run_set_summary(db: Session, run_set: RunSet, lookups: _Lookups) -> dict[str, Any]:
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
        "run_count": len(runs),
        "runs_by_status": by_status,
        "report_count": 0,
        "submitted_at": iso(run_set.submitted_at),
        "completed_at": iso(run_set.completed_at),
        "illustrative": any(run.illustrative for run in runs),
        "_runs": runs,
    }


def list_run_sets(db: Session, project_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    _get_project(db, project_id)
    query = db.query(RunSet).filter(RunSet.project_id == project_id)
    total = query.count()
    rows = query.order_by(RunSet.created_at.desc()).offset(offset).limit(limit).all()
    lookups = _Lookups(db)
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
        .filter(Run.project_id == project_id, Run.status.in_(["pending", "running"]))
        .scalar(),
        "needs_recovery_run_count": db.query(func.count(Run.id))
        .filter(Run.project_id == project_id, Run.status.in_(["failed", "partial_success"]))
        .scalar(),
    }
    return {"run_sets": items, "total": total, "limit": limit, "offset": offset, "stats": stats}


def get_run_set(db: Session, run_set_id: str) -> dict[str, Any]:
    run_set = db.get(RunSet, run_set_id)
    if run_set is None:
        raise not_found(f"Run Set '{run_set_id}' not found.")
    lookups = _Lookups(db)
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


def get_manifest(db: Session, run_id: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    if not run.manifest:
        raise not_found(f"Manifest for run '{run_id}' not found.")
    return {"run_id": run.id, "fingerprint": run.manifest_fingerprint, "manifest": run.manifest}


def is_finished(run: Run) -> bool:
    return run.status in TERMINAL_RUN_STATUSES


__all__ = [
    "execute_run_set",
    "get_manifest",
    "get_run",
    "get_run_set",
    "list_events",
    "list_run_sets",
    "list_runs",
    "preflight",
    "run_view",
    "submit_run_set",
    "is_finished",
]
