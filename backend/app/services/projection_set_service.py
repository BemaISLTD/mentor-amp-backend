"""Projection Sets: create, edit, validate, duplicate, attach scenarios (contract §E.6)."""

import re
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.db.models.assumption import AssumptionTable
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.modeling import Model, ModelPublishedOutput, ModelVersion
from app.db.models.projection import ProjectionSet
from app.db.models.scenario import ScenarioTable
from app.db.models.variable import VariableRegistry
from app.products.registry import register_all_products
from app.services import catalog_service
from app.services.common import conflict, iso, not_found, now_utc, user_ref
from app.services.run_loader import load_variable_closure, to_variable_spec

RUNNABLE_STATUSES = {"validated", "needs_review"}
EDITABLE_FIELDS = (
    "name", "description", "model_version_id", "inforce_file_ids", "assumption_table_ids",
    "scenario_ids", "valuation_date", "horizon_months", "time_step", "output_variables",
    "trace_scope", "parameters",
)


def _get(db: Session, projection_set_id: str) -> ProjectionSet:
    projection_set = db.get(ProjectionSet, projection_set_id)
    if projection_set is None:
        raise not_found(f"Projection Set '{projection_set_id}' not found.")
    return projection_set


def serialize(db: Session, projection_set: ProjectionSet) -> dict[str, Any]:
    version = db.get(ModelVersion, projection_set.model_version_id) if projection_set.model_version_id else None
    model = db.get(Model, version.model_id) if version else None
    files = (
        db.query(InforceFile).filter(InforceFile.id.in_(projection_set.inforce_file_ids)).all()
        if projection_set.inforce_file_ids else []
    )
    tables = (
        db.query(AssumptionTable).filter(AssumptionTable.id.in_(projection_set.assumption_table_ids)).all()
        if projection_set.assumption_table_ids else []
    )
    scenarios = []
    for scenario_id in projection_set.scenario_ids or []:
        scenario = db.get(ScenarioTable, scenario_id)
        scenarios.append({"id": scenario_id, "name": scenario.scenario_name if scenario else None})
    formula_count = (
        db.query(FormulaRegistry).filter(FormulaRegistry.model_version_id == version.id).count()
        if version else 0
    )
    return {
        "id": projection_set.id,
        "project_id": projection_set.project_id,
        "name": projection_set.name,
        "description": projection_set.description,
        "version_label": projection_set.version_label,
        "status": projection_set.status,
        "illustrative": bool(version.illustrative) if version else False,
        "model_version": (
            {
                "id": version.id,
                "model_id": model.id if model else None,
                "model_name": model.name if model else None,
                "version_label": version.version_label,
                "product_code": model.product_code if model else None,
                "basis": version.basis,
                "methodology": version.methodology,
            }
            if version else None
        ),
        "inputs": {
            "inforce_files": [
                {"id": file.id, "name": file.filename, "record_count": file.row_count or 0}
                for file in files
            ],
            "assumption_tables": [{"id": table.id, "name": table.table_name} for table in tables],
        },
        "scenarios": scenarios,
        "valuation_date": iso(projection_set.valuation_date),
        "horizon_months": projection_set.horizon_months,
        "time_step": projection_set.time_step,
        "output_variables": list(projection_set.output_variables or []),
        "trace_scope": dict(projection_set.trace_scope or {"mode": "none", "policy_ids": []}),
        "parameters": dict(projection_set.parameters or {}),
        "counts": {
            "input_count": len(files) + len(tables),
            "formula_count": formula_count,
            "scenario_count": len(scenarios),
            "policy_count": sum(file.row_count or 0 for file in files),
        },
        "validation": projection_set.validation,
        "created_by": user_ref(db, projection_set.created_by),
        "created_at": iso(projection_set.created_at),
        "updated_at": iso(projection_set.updated_at),
    }


def list_projection_sets(
    db: Session, project_id: str, search: str | None = None, status: str | None = None
) -> dict[str, Any]:
    catalog_service.get_project(db, project_id)
    query = db.query(ProjectionSet).filter(ProjectionSet.project_id == project_id)
    if status:
        query = query.filter(ProjectionSet.status == status)
    rows = query.order_by(ProjectionSet.updated_at.desc(), ProjectionSet.name).all()
    if search:
        needle = search.lower()
        rows = [row for row in rows if needle in row.name.lower() or needle in (row.description or "").lower()]
    return {"projection_sets": [serialize(db, row) for row in rows], "total": len(rows)}


def _normalise_trace_scope(scope: dict | None) -> dict[str, Any]:
    scope = dict(scope or {})
    return {"mode": scope.get("mode", "none"), "policy_ids": list(scope.get("policy_ids") or [])}


def create(db: Session, project_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    catalog_service.get_project(db, project_id)
    projection_set = ProjectionSet(
        project_id=project_id,
        name=payload["name"],
        description=payload.get("description"),
        version_label=payload.get("version_label") or "v1",
        status="draft",
        model_version_id=payload.get("model_version_id"),
        inforce_file_ids=list(payload.get("inforce_file_ids") or []),
        assumption_table_ids=list(payload.get("assumption_table_ids") or []),
        scenario_ids=list(payload.get("scenario_ids") or []),
        valuation_date=payload["valuation_date"],
        horizon_months=payload["horizon_months"],
        time_step=payload.get("time_step") or "monthly",
        output_variables=list(payload.get("output_variables") or []),
        trace_scope=_normalise_trace_scope(payload.get("trace_scope")),
        parameters=dict(payload.get("parameters") or {}),
        created_by=user.id,
    )
    db.add(projection_set)
    _commit_unique(db, projection_set)
    return serialize(db, projection_set)


def _commit_unique(db: Session, projection_set: ProjectionSet) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict(
            f"A Projection Set named '{projection_set.name}' version "
            f"'{projection_set.version_label}' already exists in this project."
        ) from exc
    db.refresh(projection_set)


def update(db: Session, projection_set_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    projection_set = _get(db, projection_set_id)
    changed = False
    for field in EDITABLE_FIELDS:
        if field not in payload or payload[field] is None:
            continue
        value = payload[field]
        if field == "trace_scope":
            value = _normalise_trace_scope(value)
        if getattr(projection_set, field) != value:
            setattr(projection_set, field, value)
            changed = True
    if changed:
        projection_set.status = "draft"
        projection_set.validation = None
    _commit_unique(db, projection_set)
    return serialize(db, projection_set)


def duplicate(db: Session, projection_set_id: str, version_label: str | None, user: Any) -> dict[str, Any]:
    source = _get(db, projection_set_id)
    if not version_label:
        labels = [
            row[0] for row in db.query(ProjectionSet.version_label)
            .filter(ProjectionSet.project_id == source.project_id, ProjectionSet.name == source.name)
            .all()
        ]
        numbers = [int(match.group(1)) for label in labels if (match := re.fullmatch(r"v(\d+)", label or ""))]
        version_label = f"v{(max(numbers) if numbers else 1) + 1}"
    copy = ProjectionSet(
        project_id=source.project_id,
        name=source.name,
        description=source.description,
        version_label=version_label,
        status="draft",
        model_version_id=source.model_version_id,
        inforce_file_ids=list(source.inforce_file_ids or []),
        assumption_table_ids=list(source.assumption_table_ids or []),
        scenario_ids=list(source.scenario_ids or []),
        valuation_date=source.valuation_date,
        horizon_months=source.horizon_months,
        time_step=source.time_step,
        output_variables=list(source.output_variables or []),
        trace_scope=dict(source.trace_scope or {}),
        parameters=dict(source.parameters or {}),
        created_by=user.id,
    )
    db.add(copy)
    _commit_unique(db, copy)
    return serialize(db, copy)


def attach_scenario(db: Session, projection_set_id: str, scenario_id: str) -> dict[str, Any]:
    projection_set = _get(db, projection_set_id)
    if db.get(ScenarioTable, scenario_id) is None:
        raise not_found(f"Scenario '{scenario_id}' not found.")
    if scenario_id not in (projection_set.scenario_ids or []):
        projection_set.scenario_ids = list(projection_set.scenario_ids or []) + [scenario_id]
        projection_set.status = "draft"
        projection_set.validation = None
        db.commit()
        db.refresh(projection_set)
    return serialize(db, projection_set)


# =============================================================================
# Validation (contract §E.6.2)
# =============================================================================

def _check(code: str, label: str, status: str, message: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "message": message}


def validate(db: Session, projection_set_id: str) -> dict[str, Any]:
    register_all_products()
    projection_set = _get(db, projection_set_id)
    checks: list[dict[str, str]] = []

    version = db.get(ModelVersion, projection_set.model_version_id) if projection_set.model_version_id else None
    model = db.get(Model, version.model_id) if version else None
    formulas = (
        db.query(FormulaRegistry).filter(FormulaRegistry.model_version_id == version.id).all()
        if version else []
    )
    published = (
        {row.variable_name for row in db.query(ModelPublishedOutput)
         .filter(ModelPublishedOutput.model_version_id == version.id).all()}
        if version else set()
    )
    if version is None:
        checks.append(_check("model_version_selected", "Model version selected and validated", "fail",
                             "No model version selected"))
    else:
        model_checks = catalog_service.model_version_checks(
            db, formulas,
            db.query(ModelPublishedOutput).filter(ModelPublishedOutput.model_version_id == version.id).all(),
        )
        checks.append(_check(
            "model_version_selected", "Model version selected and validated",
            "pass" if model_checks["status"] == "validated" else "fail",
            f"{model.name if model else '?'} {version.version_label}",
        ))

    variables = load_variable_closure(db, formulas, list(projection_set.output_variables or []))

    # inputs mapped
    files = (
        db.query(InforceFile).filter(InforceFile.id.in_(projection_set.inforce_file_ids)).all()
        if projection_set.inforce_file_ids else []
    )
    if not files:
        checks.append(_check("inputs_mapped", "All required inputs mapped", "fail", "No inforce file selected"))
    else:
        mappings = [
            item
            for file in files
            for item in catalog_service.input_mappings(db, projection_set.project_id, file.id)["mappings"]
        ]
        present = [item for item in mappings if item["status"] == "validated"]
        checks.append(_check(
            "inputs_mapped", "All required inputs mapped",
            "pass" if len(present) == len(mappings) else "fail",
            f"{len(present)} of {len(mappings)} inforce columns present",
        ))

    # assumption tables pinned
    needed = sorted({
        spec.source.get("table") for spec in variables.values()
        if spec.source_type == "assumption" and spec.source.get("table")
    })
    pinned_tables = (
        db.query(AssumptionTable).filter(AssumptionTable.id.in_(projection_set.assumption_table_ids)).all()
        if projection_set.assumption_table_ids else []
    )
    pinned_names = {table.table_name for table in pinned_tables}
    missing_tables = [name for name in needed if name not in pinned_names]
    checks.append(_check(
        "assumption_tables_pinned", "Assumption tables pinned",
        "fail" if missing_tables else "pass",
        f"Missing: {', '.join(missing_tables)}" if missing_tables else (", ".join(needed) or "None needed"),
    ))

    # scenarios compatible
    all_variables = {row.name: to_variable_spec(row) for row in db.query(VariableRegistry).all()}
    scenario_problems: list[str] = []
    scenario_rows = []
    for scenario_id in projection_set.scenario_ids or []:
        scenario = db.get(ScenarioTable, scenario_id)
        if scenario is None:
            scenario_problems.append(f"scenario {scenario_id} not found")
            continue
        scenario_rows.append(scenario)
        for override in scenario.overrides or []:
            target = override.get("target_variable")
            spec = all_variables.get(target)
            if spec is None:
                scenario_problems.append(f"{scenario.scenario_name}: '{target}' is not a variable")
            elif spec.source_type in ("formula", "valuation"):
                scenario_problems.append(f"{scenario.scenario_name}: '{target}' is a calculated output")
    if not projection_set.scenario_ids:
        checks.append(_check("scenarios_compatible", "Scenario subset compatible", "fail", "No scenario selected"))
    else:
        checks.append(_check(
            "scenarios_compatible", "Scenario subset compatible",
            "fail" if scenario_problems else "pass",
            "; ".join(scenario_problems) if scenario_problems
            else f"{len(scenario_rows)} scenario(s); overridden variables exist",
        ))

    # horizon
    horizon_ok = 1 <= (projection_set.horizon_months or 0) <= 1200 and projection_set.time_step == "monthly"
    checks.append(_check(
        "horizon_valid", "Projection characteristics valid",
        "pass" if horizon_ok else "fail",
        f"{projection_set.horizon_months} {projection_set.time_step} steps from {iso(projection_set.valuation_date)}",
    ))

    # outputs
    outputs = list(projection_set.output_variables or [])
    unknown_outputs = [name for name in outputs if name not in published]
    checks.append(_check(
        "outputs_configured", "Output capture configured",
        "fail" if not outputs or unknown_outputs else "pass",
        f"Not published: {', '.join(unknown_outputs)}" if unknown_outputs
        else (f"{len(outputs)} published outputs" if outputs else "No outputs selected"),
    ))

    # trace scope
    scope = _normalise_trace_scope(projection_set.trace_scope)
    policy_ids = {
        row[0] for row in db.query(InforceRecord.policy_id)
        .filter(InforceRecord.file_id.in_(projection_set.inforce_file_ids or [])).all()
    } if projection_set.inforce_file_ids else set()
    if scope["mode"] == "none":
        checks.append(_check("trace_scope_selected", "Trace capture scope selected", "warning",
                             "Trace capture scope is not explicitly selected; values cannot be traced."))
    elif scope["mode"] == "all":
        too_many = len(policy_ids) > settings.max_traced_policies
        checks.append(_check(
            "trace_scope_selected", "Trace capture scope selected",
            "fail" if too_many else "pass",
            f"'all' is limited to {settings.max_traced_policies} policies; this set has {len(policy_ids)}."
            if too_many else f"All {len(policy_ids)} policies",
        ))
    else:
        unknown = [pid for pid in scope["policy_ids"] if pid not in policy_ids]
        status = "warning" if unknown or not scope["policy_ids"] else "pass"
        if len(scope["policy_ids"]) > settings.max_traced_policies:
            status = "fail"
        checks.append(_check(
            "trace_scope_selected", "Trace capture scope selected", status,
            f"Unknown policies: {', '.join(unknown)}" if unknown
            else f"{len(scope['policy_ids'])} policies",
        ))

    # engine ready
    unregistered = [row.function_ref for row in formulas if row.function_ref not in FORMULA_FUNCTIONS]
    checks.append(_check(
        "engine_ready", "Formulas registered and graph valid",
        "fail" if unregistered or not formulas else "pass",
        f"Unregistered: {', '.join(unregistered)}" if unregistered
        else f"{len(formulas)} formulas, no cycles",
    ))

    if any(check["status"] == "fail" for check in checks):
        overall, status = "invalid", "draft"
    elif any(check["status"] == "warning" for check in checks):
        overall, status = "needs_review", "needs_review"
    else:
        overall, status = "validated", "validated"
    projection_set.validation = {"status": overall, "validated_at": iso(now_utc()), "checks": checks}
    projection_set.status = status
    db.commit()
    db.refresh(projection_set)
    return serialize(db, projection_set)
