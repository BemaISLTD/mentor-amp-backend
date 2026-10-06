"""Projection Sets: create, edit, validate, duplicate, attach scenarios (contract §E.6).

Every object a Projection Set references (model version, inforce files, assumption and factor
tables, scenarios) must belong to the Projection Set's project; this is enforced when the set is
created or edited, again by validation, and once more when a run is submitted (the run package
freeze). Validation uses the same checks as the freeze (``run_package_service``).
"""

import re
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import lifecycle
from app.core.audit import record_audit
from app.db.models.assumption import AssumptionTable
from app.db.models.factor import FactorTable
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.modeling import Model, ModelVersion
from app.db.models.projection import ProjectionSet
from app.db.models.scenario import ScenarioTable
from app.services import access, catalog_service
from app.services import run_package_service as checks
from app.services.common import ServiceError, conflict, iso, not_found, now_utc, user_ref

EDITABLE_FIELDS = (
    "name", "description", "model_version_id", "inforce_file_ids", "assumption_table_ids",
    "factor_table_ids", "scenario_ids", "valuation_date", "horizon_months", "time_step",
    "output_variables", "trace_scope", "parameters",
)
REFERENCE_FIELDS = (
    ("inforce_file_ids", "inforce_file"),
    ("assumption_table_ids", "assumption_table"),
    ("factor_table_ids", "factor_table"),
    ("scenario_ids", "scenario"),
)


def _audit_state(row: ProjectionSet) -> dict[str, Any]:
    return {
        column.name: iso(getattr(row, column.name))
        if column.name.endswith("_at") or column.name.endswith("_date")
        else getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"created_at", "updated_at"}
    }


def _audit(db: Session, user: Any | None, action: str, row: ProjectionSet,
           before: dict[str, Any] | None = None) -> None:
    if user is not None:
        record_audit(
            db, actor_user_id=user.id, action=action, entity_type="projection_set",
            entity_id=row.id, before_state=before, after_state=_audit_state(row),
        )


def get(db: Session, projection_set_id: str) -> ProjectionSet:
    projection_set = db.get(ProjectionSet, projection_set_id)
    if projection_set is None:
        raise not_found(f"Projection Set '{projection_set_id}' not found.")
    return projection_set


def _tables(db: Session, model: type, ids: list[str]) -> list[Any]:
    return db.query(model).filter(model.id.in_(ids)).all() if ids else []


def serialize(db: Session, projection_set: ProjectionSet) -> dict[str, Any]:
    version = db.get(ModelVersion, projection_set.model_version_id) if projection_set.model_version_id else None
    model = db.get(Model, version.model_id) if version else None
    files = _tables(db, InforceFile, list(projection_set.inforce_file_ids or []))
    assumption_tables = _tables(db, AssumptionTable, list(projection_set.assumption_table_ids or []))
    factor_tables = _tables(db, FactorTable, list(projection_set.factor_table_ids or []))
    scenarios = []
    for scenario_id in projection_set.scenario_ids or []:
        scenario = db.get(ScenarioTable, scenario_id)
        scenarios.append({"id": scenario_id, "name": scenario.scenario_name if scenario else None})
    formula_count = (
        db.query(FormulaRegistry).filter(
            FormulaRegistry.model_version_id == version.id,
            FormulaRegistry.deleted_at.is_(None),
        ).count()
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
                {"id": file.id, "name": file.filename, "record_count": file.row_count or 0,
                 "status": file.status, "fingerprint": file.fingerprint}
                for file in files
            ],
            "assumption_tables": [
                {"id": table.id, "name": table.table_name, "status": table.status,
                 "version_label": table.version_label, "fingerprint": table.fingerprint}
                for table in assumption_tables
            ],
            "factor_tables": [
                {"id": table.id, "name": table.table_name, "status": table.status,
                 "version_label": table.version_label, "fingerprint": table.fingerprint}
                for table in factor_tables
            ],
        },
        "scenarios": scenarios,
        "valuation_date": iso(projection_set.valuation_date),
        "horizon_months": projection_set.horizon_months,
        "time_step": projection_set.time_step,
        "output_variables": list(projection_set.output_variables or []),
        "trace_scope": dict(projection_set.trace_scope or {"mode": "none", "policy_ids": []}),
        "parameters": dict(projection_set.parameters or {}),
        "counts": {
            "input_count": len(files) + len(assumption_tables) + len(factor_tables),
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


def _check_references(db: Session, project_id: str, values: dict[str, Any]) -> None:
    """Refuse any referenced object that is missing or belongs to another project (422)."""
    foreign: list[dict[str, Any]] = []
    model_version_id = values.get("model_version_id")
    if model_version_id and access.project_id_of(db, "model_version", model_version_id) != project_id:
        foreign.append({"field": "model_version_id", "id": model_version_id})
    for field, kind in REFERENCE_FIELDS:
        for object_id in values.get(field) or []:
            if access.project_id_of(db, kind, object_id) != project_id:
                foreign.append({"field": field, "id": object_id})
    if foreign:
        raise ServiceError(
            422, "CROSS_PROJECT_REFERENCE",
            "Referenced objects were not found in this project: "
            + ", ".join(f"{item['field']} {item['id']}" for item in foreign),
            {"references": foreign},
        )


def create(db: Session, project_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    catalog_service.get_project(db, project_id)
    _check_references(db, project_id, payload)
    projection_set = ProjectionSet(
        project_id=project_id,
        name=payload["name"],
        description=payload.get("description"),
        version_label=payload.get("version_label") or "v1",
        status=lifecycle.DRAFT,
        model_version_id=payload.get("model_version_id"),
        inforce_file_ids=list(payload.get("inforce_file_ids") or []),
        assumption_table_ids=list(payload.get("assumption_table_ids") or []),
        factor_table_ids=list(payload.get("factor_table_ids") or []),
        scenario_ids=list(payload.get("scenario_ids") or []),
        valuation_date=payload["valuation_date"],
        horizon_months=payload["horizon_months"],
        time_step=payload.get("time_step") or "monthly",
        output_variables=list(payload.get("output_variables") or []),
        trace_scope=checks.normalise_trace_scope(payload.get("trace_scope")),
        parameters=dict(payload.get("parameters") or {}),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(projection_set)
    _commit_unique(db, projection_set, user=user, action="projection_set.created")
    return serialize(db, projection_set)


def _commit_unique(db: Session, projection_set: ProjectionSet, *, user: Any | None = None,
                   action: str | None = None, before: dict[str, Any] | None = None) -> None:
    try:
        db.flush()
        if action:
            _audit(db, user, action, projection_set, before)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict(
            f"A Projection Set named '{projection_set.name}' version "
            f"'{projection_set.version_label}' already exists in this project."
        ) from exc
    db.refresh(projection_set)


def update(db: Session, projection_set_id: str, payload: dict[str, Any],
           user: Any | None = None) -> dict[str, Any]:
    projection_set = get(db, projection_set_id)
    before = _audit_state(projection_set)
    _check_references(db, projection_set.project_id, payload)
    changed = False
    for field in EDITABLE_FIELDS:
        if field not in payload or payload[field] is None:
            continue
        value = payload[field]
        if field == "trace_scope":
            value = checks.normalise_trace_scope(value)
        if getattr(projection_set, field) != value:
            setattr(projection_set, field, value)
            changed = True
    if changed:
        # Any edit invalidates the previous validation; submitted runs are unaffected because
        # each one executes its own frozen run package.
        projection_set.status = lifecycle.DRAFT
        projection_set.validation = None
        projection_set.updated_by = user.id if user else projection_set.updated_by
    _commit_unique(
        db, projection_set, user=user,
        action="projection_set.updated" if changed else None, before=before,
    )
    return serialize(db, projection_set)


def duplicate(db: Session, projection_set_id: str, version_label: str | None, user: Any) -> dict[str, Any]:
    source = get(db, projection_set_id)
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
        status=lifecycle.DRAFT,
        model_version_id=source.model_version_id,
        inforce_file_ids=list(source.inforce_file_ids or []),
        assumption_table_ids=list(source.assumption_table_ids or []),
        factor_table_ids=list(source.factor_table_ids or []),
        scenario_ids=list(source.scenario_ids or []),
        valuation_date=source.valuation_date,
        horizon_months=source.horizon_months,
        time_step=source.time_step,
        output_variables=list(source.output_variables or []),
        trace_scope=dict(source.trace_scope or {}),
        parameters=dict(source.parameters or {}),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(copy)
    _commit_unique(db, copy, user=user, action="projection_set.version_created")
    return serialize(db, copy)


def attach_scenario(db: Session, projection_set_id: str, scenario_id: str,
                    user: Any | None = None) -> dict[str, Any]:
    projection_set = get(db, projection_set_id)
    before = _audit_state(projection_set)
    _check_references(db, projection_set.project_id, {"scenario_ids": [scenario_id]})
    if scenario_id not in (projection_set.scenario_ids or []):
        projection_set.scenario_ids = list(projection_set.scenario_ids or []) + [scenario_id]
        projection_set.status = lifecycle.DRAFT
        projection_set.validation = None
        projection_set.updated_by = user.id if user else projection_set.updated_by
        db.flush()
        _audit(db, user, "projection_set.scenario_attached", projection_set, before)
        db.commit()
        db.refresh(projection_set)
    return serialize(db, projection_set)


# =============================================================================
# Validation (contract §E.6.2)
# =============================================================================

MODEL_CODES = {
    "MODEL_VERSION_NOT_SELECTED", "MODEL_VERSION_NOT_FOUND", "MODEL_VERSION_NOT_RUNNABLE",
    "NO_FORMULAS", "FORMULA_NOT_RUNNABLE", "VARIABLE_NOT_DEFINED",
}
ENGINE_CODES = {"FUNCTION_NOT_REGISTERED", "FORMULA_GRAPH_INVALID"}


def _check(code: str, label: str, status: str, message: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "message": message}


def _messages(problems: list) -> str:
    return "; ".join(problem.message for problem in problems[:4])


def validate(db: Session, projection_set_id: str, user: Any | None = None) -> dict[str, Any]:
    projection_set = get(db, projection_set_id)
    before = _audit_state(projection_set)
    project_id = projection_set.project_id
    result: list[dict[str, str]] = []

    model = checks.check_model(db, project_id, projection_set)
    model_problems = [p for p in model.problems if p.code in MODEL_CODES]
    label = f"{model.model.name} {model.version.version_label}" if model.version and model.model else "—"
    result.append(_check(
        "model_version_selected", "Model version selected and validated",
        "fail" if model_problems else "pass", _messages(model_problems) or label,
    ))

    files, inforce_problems = checks.check_inforce(db, project_id, list(projection_set.inforce_file_ids or []))
    mappings = [
        item
        for file in files
        for item in catalog_service.input_mappings(db, project_id, file.id)["mappings"]
    ]
    present = [item for item in mappings if item["status"] == "validated"]
    if len(present) != len(mappings):
        inforce_problems = inforce_problems + [checks.Problem(
            "INPUT_COLUMNS_MISSING", f"{len(mappings) - len(present)} mapped inforce column(s) missing",
        )]
    result.append(_check(
        "inputs_mapped", "All required inputs mapped, validated and fingerprinted",
        "fail" if inforce_problems else "pass",
        _messages(inforce_problems) or f"{len(present)} of {len(mappings)} inforce columns present",
    ))

    pinned: dict[str, list] = {}
    table_problems: list = []
    for kind, ids in (("assumption", projection_set.assumption_table_ids),
                      ("factor", projection_set.factor_table_ids)):
        pinned[kind], found = checks.check_tables(db, project_id, kind, list(ids or []))
        table_problems += found
    bindings, _columns, binding_problems = checks.bind_tables(model.variables, pinned)
    table_problems += binding_problems
    result.append(_check(
        "assumption_tables_pinned", "Assumption and factor tables pinned by ID",
        "fail" if table_problems else "pass",
        _messages(table_problems) or (", ".join(sorted(bindings)) or "None needed"),
    ))

    scenario_problems: list = []
    for scenario_id in projection_set.scenario_ids or []:
        _scenario, _set, found = checks.check_scenario(db, project_id, scenario_id, model.variables)
        scenario_problems += found
    if not projection_set.scenario_ids:
        result.append(_check("scenarios_compatible", "Scenario subset compatible", "fail", "No scenario selected"))
    else:
        result.append(_check(
            "scenarios_compatible", "Scenario subset compatible",
            "fail" if scenario_problems else "pass",
            _messages(scenario_problems)
            or f"{len(projection_set.scenario_ids)} scenario(s); validated, unchanged, valid overrides",
        ))

    horizon_ok = 1 <= (projection_set.horizon_months or 0) <= 1200 and projection_set.time_step == "monthly"
    result.append(_check(
        "horizon_valid", "Projection characteristics valid",
        "pass" if horizon_ok else "fail",
        f"{projection_set.horizon_months} {projection_set.time_step} steps from {iso(projection_set.valuation_date)}",
    ))

    outputs = list(projection_set.output_variables or [])
    unknown_outputs = [name for name in outputs if name not in model.published]
    result.append(_check(
        "outputs_configured", "Output capture configured",
        "fail" if not outputs or unknown_outputs else "pass",
        f"Not published: {', '.join(unknown_outputs)}" if unknown_outputs
        else (f"{len(outputs)} published outputs" if outputs else "No outputs selected"),
    ))

    scope = checks.normalise_trace_scope(projection_set.trace_scope)
    policy_ids = {
        row[0] for row in db.query(InforceRecord.policy_id)
        .filter(InforceRecord.file_id.in_([file.id for file in files])).all()
    } if files else set()
    if scope["mode"] == "none":
        result.append(_check("trace_scope_selected", "Trace capture scope selected", "warning",
                             "Trace capture scope is not explicitly selected; values cannot be traced."))
    elif scope["mode"] == "all":
        too_many = len(policy_ids) > settings.max_traced_policies
        result.append(_check(
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
        result.append(_check(
            "trace_scope_selected", "Trace capture scope selected", status,
            f"Unknown policies: {', '.join(unknown)}" if unknown
            else f"{len(scope['policy_ids'])} policies",
        ))

    engine_problems = [p for p in model.problems if p.code in ENGINE_CODES]
    if not model.formulas and model.version is not None:
        engine_problems.append(checks.Problem("NO_FORMULAS", "The model version has no formulas."))
    result.append(_check(
        "engine_ready", "Formulas registered and graph valid",
        "fail" if engine_problems else "pass",
        _messages(engine_problems) or f"{len(model.formulas)} formulas, no cycles",
    ))

    if any(check["status"] == "fail" for check in result):
        overall, status = "invalid", lifecycle.DRAFT
    elif any(check["status"] == "warning" for check in result):
        overall, status = lifecycle.NEEDS_REVIEW, lifecycle.NEEDS_REVIEW
    else:
        overall, status = lifecycle.VALIDATED, lifecycle.VALIDATED
    projection_set.validation = {"status": overall, "validated_at": iso(now_utc()), "checks": result}
    projection_set.status = status
    projection_set.updated_by = user.id if user else projection_set.updated_by
    db.flush()
    _audit(db, user, "projection_set.validated", projection_set, before)
    db.commit()
    db.refresh(projection_set)
    return serialize(db, projection_set)
