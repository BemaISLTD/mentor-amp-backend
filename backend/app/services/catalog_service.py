"""Read side of Models, Formulas, Inputs and Scenarios (contract §E.2–§E.5)."""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.engine import EngineError, order_formulas
from app.core.projection_engine.run_data import VariableSpec
from app.db.models.assumption import AssumptionSet, AssumptionTable
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.modeling import FormulaGroup, Model, ModelPublishedOutput, ModelVersion
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet
from app.db.models.run import Run
from app.db.models.scenario import ScenarioSet, ScenarioTable
from app.services.common import iso, not_found, product_name, user_ref
from app.services.model_definition import (
    definitions_for,
    load_variable_closure,
    to_formula_spec,
    variable_specs,
)

CATEGORY_LABELS = {
    "liability_inforce": "Liability Inforce",
    "asset_inforce": "Asset Inforce",
    "assumption_table": "Assumption Tables",
    "factor_table": "Factor Tables",
    "projection_inputs": "Projection Inputs",
    "expected_results": "Expected Results",
}
CATEGORY_MILESTONES = {
    "liability_inforce": "M1",
    "asset_inforce": "M2",
    "assumption_table": "M1",
    "factor_table": "M1",
    "projection_inputs": "M2",
    "expected_results": "M3",
}
DATA_KINDS = {"input", "context", "assumption", "factor", "manual", "prior_output", "scenario"}


# =============================================================================
# Shared lookups
# =============================================================================

def get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise not_found(f"Project '{project_id}' not found.")
    return project


def get_model_version(db: Session, model_version_id: str) -> tuple[ModelVersion, Model]:
    version = db.query(ModelVersion).filter(
        ModelVersion.id == model_version_id, ModelVersion.deleted_at.is_(None)
    ).first()
    if version is None:
        raise not_found(f"Model version '{model_version_id}' not found.")
    model = db.get(Model, version.model_id)
    return version, model


def version_ref(version: ModelVersion | None) -> dict | None:
    if version is None:
        return None
    return {
        "id": version.id,
        "version_label": version.version_label,
        "basis": version.basis,
        "methodology": version.methodology,
        "status": version.status,
        "illustrative": bool(version.illustrative),
        "created_at": iso(version.created_at),
        "is_current": bool(version.is_current),
        "notes": version.notes,
    }


def _versions_by_model(db: Session, model_ids: list[str]) -> dict[str, list[ModelVersion]]:
    grouped: dict[str, list[ModelVersion]] = {model_id: [] for model_id in model_ids}
    if not model_ids:
        return grouped
    for version in (
        db.query(ModelVersion)
        .filter(ModelVersion.model_id.in_(model_ids), ModelVersion.deleted_at.is_(None))
        .order_by(ModelVersion.created_at)
        .all()
    ):
        grouped.setdefault(version.model_id, []).append(version)
    return grouped


def _current_version(versions: list[ModelVersion]) -> ModelVersion | None:
    current = [version for version in versions if version.is_current]
    if current:
        return current[-1]
    return versions[-1] if versions else None


def _run_counts(db: Session, version_ids: list[str]) -> dict[str, int]:
    if not version_ids:
        return {}
    rows = (
        db.query(Run.model_version_id, func.count(Run.id))
        .filter(Run.model_version_id.in_(version_ids))
        .group_by(Run.model_version_id)
        .all()
    )
    return {version_id: count for version_id, count in rows}


def _model_summary(
    db: Session,
    model: Model,
    versions: list[ModelVersion],
    run_counts: dict[str, int],
    current_user_id: str | None,
) -> dict[str, Any]:
    current = _current_version(versions)
    return {
        "id": model.id,
        "project_id": model.project_id,
        "name": model.name,
        "product_code": model.product_code,
        "product_name": product_name(model.product_code),
        "description": model.description,
        "status": model.status,
        "owner": user_ref(db, model.owner_user_id, current_user_id),
        "current_version": version_ref(current),
        "version_count": len(versions),
        "run_count": sum(run_counts.get(version.id, 0) for version in versions),
        "updated_at": iso(model.updated_at),
    }


# =============================================================================
# Models (§E.2)
# =============================================================================

def list_models(
    db: Session,
    project_id: str,
    current_user_id: str | None,
    product_code: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    search: str | None = None,
    sort: str = "updated_desc",
) -> dict[str, Any]:
    get_project(db, project_id)
    models = db.query(Model).filter(
        Model.project_id == project_id, Model.deleted_at.is_(None)
    ).all()
    versions = _versions_by_model(db, [model.id for model in models])
    run_counts = _run_counts(db, [v.id for vs in versions.values() for v in vs])

    owned = [model for model in models if current_user_id and model.owner_user_id == current_user_id]
    counts = {"all": len(models), "owned": len(owned), "shared": len(models) - len(owned)}
    products: dict[str, int] = {}
    for model in models:
        products[model.product_code] = products.get(model.product_code, 0) + 1

    selected = models
    if product_code:
        selected = [model for model in selected if model.product_code.upper() == product_code.upper()]
    if status:
        selected = [model for model in selected if model.status == status]
    if owner == "me":
        selected = [model for model in selected if model.owner_user_id == current_user_id]
    elif owner == "others":
        selected = [model for model in selected if model.owner_user_id != current_user_id]
    if search:
        needle = search.lower()
        selected = [
            model for model in selected
            if needle in model.name.lower() or needle in (model.description or "").lower()
        ]
    if sort == "name_asc":
        selected.sort(key=lambda model: model.name.lower())
    else:
        selected.sort(key=lambda model: model.updated_at or model.created_at, reverse=True)

    return {
        "models": [
            _model_summary(db, model, versions[model.id], run_counts, current_user_id)
            for model in selected
        ],
        "total": len(selected),
        "counts": counts,
        "products": [
            {"code": code, "name": product_name(code), "count": count}
            for code, count in sorted(products.items())
        ],
    }


def get_model(db: Session, model_id: str, current_user_id: str | None) -> dict[str, Any]:
    model = db.query(Model).filter(Model.id == model_id, Model.deleted_at.is_(None)).first()
    if model is None:
        raise not_found(f"Model '{model_id}' not found.")
    versions = _versions_by_model(db, [model.id])[model.id]
    run_counts = _run_counts(db, [version.id for version in versions])
    summary = _model_summary(db, model, versions, run_counts, current_user_id)
    owner = user_ref(db, model.owner_user_id)
    summary["versions"] = [version_ref(version) for version in reversed(versions)]
    summary["access"] = [{"user": owner, "role": "owner"}] if owner else []
    return summary


def _model_formulas(db: Session, model_version_id: str) -> list[FormulaRegistry]:
    return (
        db.query(FormulaRegistry)
        .filter(
            FormulaRegistry.model_version_id == model_version_id,
            FormulaRegistry.deleted_at.is_(None),
        )
        .order_by(FormulaRegistry.output_variable)
        .all()
    )


def _published(db: Session, model_version_id: str) -> list[ModelPublishedOutput]:
    return (
        db.query(ModelPublishedOutput)
        .filter(ModelPublishedOutput.model_version_id == model_version_id)
        .order_by(ModelPublishedOutput.sort_order, ModelPublishedOutput.variable_name)
        .all()
    )


def _projection_sets_for_version(db: Session, model_version_id: str) -> list[ProjectionSet]:
    return db.query(ProjectionSet).filter(ProjectionSet.model_version_id == model_version_id).all()


def model_version_checks(
    db: Session,
    formulas: list[FormulaRegistry],
    published: list[ModelPublishedOutput],
    model_version_id: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    registered = [row for row in formulas if row.function_ref in FORMULA_FUNCTIONS]
    checks.append({
        "code": "functions_registered",
        "label": "Every formula has a registered implementation",
        "status": "pass" if formulas and len(registered) == len(formulas) else "fail",
        "message": f"{len(registered)} of {len(formulas)} registered",
    })
    try:
        order_formulas([to_formula_spec(row) for row in formulas])
        checks.append({"code": "graph_acyclic", "label": "Dependency graph has no cycles",
                       "status": "pass", "message": "No cycles"})
    except EngineError as error:
        checks.append({"code": "graph_acyclic", "label": "Dependency graph has no cycles",
                       "status": "fail", "message": error.message})
    version_id = model_version_id or next((row.model_version_id for row in formulas if row.model_version_id), None)
    names = set(definitions_for(db, version_id)) if version_id else set()
    dependencies = [dep.depends_on_variable for row in formulas for dep in row.dependencies]
    missing = sorted({name for name in dependencies if name not in names})
    checks.append({
        "code": "dependencies_registered",
        "label": "Every dependency is defined for this model version",
        "status": "fail" if missing else "pass",
        "message": f"Missing: {', '.join(missing)}" if missing
        else f"All {len(dependencies)} dependencies registered",
    })
    published_missing = [row.variable_name for row in published if row.variable_name not in names]
    checks.append({
        "code": "outputs_published",
        "label": "Published outputs are defined for this model version",
        "status": "fail" if published_missing else "pass",
        "message": f"{len(published) - len(published_missing)} of {len(published)}",
    })
    status = "validated" if all(check["status"] == "pass" for check in checks) else "invalid"
    return {"status": status, "checks": checks}


def model_version_structure(db: Session, model_version_id: str) -> dict[str, Any]:
    version, model = get_model_version(db, model_version_id)
    formulas = _model_formulas(db, version.id)
    published = _published(db, version.id)
    groups = (
        db.query(FormulaGroup)
        .filter(FormulaGroup.model_version_id == version.id)
        .order_by(FormulaGroup.sort_order, FormulaGroup.name)
        .all()
    )
    group_counts: dict[str, int] = {}
    for row in formulas:
        if row.group_id:
            group_counts[row.group_id] = group_counts.get(row.group_id, 0) + 1
    variables = load_variable_closure(db, version.id, formulas, [row.variable_name for row in published])
    input_variables = [spec for spec in variables.values() if spec.source_type in DATA_KINDS]
    projection_sets = _projection_sets_for_version(db, version.id)
    linked: set[str] = set()
    for projection_set in projection_sets:
        linked.update(projection_set.inforce_file_ids or [])
        linked.update(projection_set.assumption_table_ids or [])
    run_count = db.query(func.count(Run.id)).filter(Run.model_version_id == version.id).scalar() or 0

    hierarchy: list[dict[str, Any]] = [
        {"level": "model", "depth": 0, "id": model.id, "label": model.name,
         "meta": f"Model · {version.version_label}"},
        {"level": "product", "depth": 1, "id": None, "label": model.product_code,
         "meta": "Domain / product"},
        {"level": "basis", "depth": 2, "id": version.id,
         "label": " · ".join(part for part in (version.block_name, version.basis) if part),
         "meta": "Block · profile · basis"},
    ]
    for group in groups:
        hierarchy.append({
            "level": "formula_group", "depth": 3, "id": group.id, "label": group.name,
            "meta": f"Formula Group · {group.lineage_state.capitalize()} · {group.version_label}",
            "lineage_state": group.lineage_state,
        })
    hierarchy.append({
        "level": "inputs", "depth": 2, "id": None, "label": "Inputs and assumptions",
        "meta": f"{len(input_variables)} variables · {len(linked)} linked datasets",
    })
    hierarchy.append({
        "level": "published_outputs", "depth": 2, "id": None, "label": "Published outputs",
        "meta": f"{len(published)} interface variables",
    })

    return {
        "model_version": {
            **version_ref(version),
            "model_id": model.id,
            "model_name": model.name,
            "product_code": model.product_code,
            "block_name": version.block_name,
            "profile_name": version.profile_name,
        },
        "hierarchy": hierarchy,
        "facts": {
            "formula_group_count": len(groups),
            "formula_count": len(formulas),
            "input_variable_count": len(input_variables),
            "linked_dataset_count": len(linked),
            "published_output_count": len(published),
            "projection_set_count": len(projection_sets),
            "run_count": run_count,
        },
        "formula_groups": [
            {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "lineage_state": group.lineage_state,
                "version_label": group.version_label,
                "formula_count": group_counts.get(group.id, 0),
            }
            for group in groups
        ],
        "validation": model_version_checks(db, formulas, published, version.id),
    }


def published_outputs(db: Session, model_version_id: str) -> dict[str, Any]:
    version, _ = get_model_version(db, model_version_id)
    rows = _published(db, version.id)
    projection_sets = _projection_sets_for_version(db, version.id)
    formulas = _model_formulas(db, version.id)
    checks = model_version_checks(db, formulas, rows)
    return {
        "model_version_id": version.id,
        "contract_status": checks["status"],
        "outputs": [
            {
                "variable_name": row.variable_name,
                "display_name": row.display_name,
                "unit": row.unit,
                "dimension": row.dimension,
                "aggregation": row.aggregation,
                "description": row.description,
                "consumer_count": sum(
                    1 for ps in projection_sets if row.variable_name in (ps.output_variables or [])
                ),
            }
            for row in rows
        ],
    }


# =============================================================================
# Formulas (§E.5)
# =============================================================================

def formula_groups(db: Session, model_version_id: str) -> dict[str, Any]:
    version, _ = get_model_version(db, model_version_id)
    formulas = _model_formulas(db, version.id)
    consumer_count = len(_projection_sets_for_version(db, version.id))
    groups = (
        db.query(FormulaGroup)
        .filter(FormulaGroup.model_version_id == version.id)
        .order_by(FormulaGroup.sort_order, FormulaGroup.name)
        .all()
    )
    result = []
    for group in groups:
        members = [row for row in formulas if row.group_id == group.id]
        result.append({
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "lineage_state": group.lineage_state,
            "version_label": group.version_label,
            "formula_count": len(members),
            "consumer_count": consumer_count,
            "formulas": [
                {
                    "id": row.id,
                    "name": row.name,
                    "output_variable": row.output_variable,
                    "expression_text": row.expression_text,
                    "version": row.version,
                    "status": row.status,
                    "illustrative": bool(row.illustrative),
                }
                for row in _dependency_order(members)
            ],
        })
    return {"model_version_id": version.id, "groups": result}


def _dependency_order(rows: list[FormulaRegistry]) -> list[FormulaRegistry]:
    by_output = {row.output_variable: row for row in rows}
    try:
        ordered = order_formulas([to_formula_spec(row) for row in rows])
        return [by_output[spec.output_variable] for spec in ordered]
    except EngineError:
        return rows


def _source_summary(spec: VariableSpec) -> str:
    source = spec.source
    kind = spec.source_type
    if kind == "input":
        return f"Inforce column '{source.get('column') or spec.name}'"
    if kind in ("assumption", "factor"):
        keys = ", ".join((source.get("key_map") or {}).keys())
        return f"{source.get('table')} by {keys} → {source.get('value_column')}"
    if kind == "manual":
        return f"Constant {source.get('value', spec.default_value)} (scenario-overridable)"
    if kind == "context":
        return f"Run context field '{source.get('field') or spec.name}'"
    if kind == "prior_output":
        return (
            f"{source.get('variable')} at t − {source.get('offset', 1)} "
            f"(initial {source.get('initial_value')})"
        )
    return kind


def formula_detail(db: Session, formula_id: str) -> dict[str, Any]:
    row = db.get(FormulaRegistry, formula_id)
    if row is None:
        raise not_found(f"Formula '{formula_id}' not found.")
    group = db.get(FormulaGroup, row.group_id) if row.group_id else None
    version = db.get(ModelVersion, row.model_version_id) if row.model_version_id else None
    model = db.get(Model, version.model_id) if version else None
    siblings = _model_formulas(db, version.id) if version else [row]
    producers = {item.output_variable: item.id for item in siblings}
    variables = variable_specs(db, version.id) if version else {}

    inputs: list[dict[str, Any]] = []
    formula_dependencies: list[dict[str, Any]] = []
    for dependency in sorted(dep.depends_on_variable for dep in row.dependencies):
        spec = variables.get(dependency)
        kind = spec.source_type if spec else "missing"
        if kind == "formula":
            formula_dependencies.append(
                {"variable": dependency, "kind": "formula", "formula_id": producers.get(dependency)}
            )
        elif kind == "prior_output":
            source_variable = spec.source.get("variable")
            formula_dependencies.append({
                "variable": dependency, "kind": "prior_output",
                "formula_id": producers.get(source_variable),
            })
        else:
            inputs.append({
                "variable": dependency,
                "kind": kind,
                "unit": spec.unit if spec else None,
                "source_summary": _source_summary(spec) if spec else "Not defined for this model version",
            })

    dependents = [
        {"variable": item.output_variable, "formula_id": item.id}
        for item in siblings
        if row.output_variable in {dep.depends_on_variable for dep in item.dependencies}
    ]
    for spec in variables.values():
        if spec.source_type == "valuation" and spec.source.get("cash_flow") == row.output_variable:
            dependents.append({"variable": spec.name, "formula_id": None})
        if spec.source_type == "prior_output" and spec.source.get("variable") == row.output_variable:
            dependents.append({"variable": spec.name, "formula_id": None})

    latest_run = None
    if version is not None:
        latest_run = (
            db.query(Run)
            .filter(Run.model_version_id == version.id, Run.status.in_(["success", "partial_success"]))
            .order_by(Run.completed_at.desc())
            .first()
        )
    traced = bool(latest_run and (latest_run.summary or {}).get("trace", {}).get("captured"))
    return {
        "id": row.id,
        "name": row.name,
        "output_variable": row.output_variable,
        "function_ref": row.function_ref,
        "expression_text": row.expression_text,
        "explanation": row.explanation,
        "unit": row.unit,
        "version": row.version,
        "status": row.status,
        "illustrative": bool(row.illustrative),
        "group": (
            {"id": group.id, "name": group.name, "lineage_state": group.lineage_state,
             "version_label": group.version_label}
            if group else None
        ),
        "model_version": (
            {"id": version.id, "model_name": model.name if model else None,
             "version_label": version.version_label}
            if version else None
        ),
        "inputs": inputs,
        "formula_dependencies": formula_dependencies,
        "dependents": dependents,
        "consumer_count": len(_projection_sets_for_version(db, version.id)) if version else 0,
        "lineage": {
            "source": f"{group.name} {group.version_label}" if group else "—",
            "inherited_by": [],
        },
        "tests": {
            "available": False,
            "passing": 0,
            "total": 0,
            "message": "Formula tests arrive in M3 with the actuarial reference cases.",
        },
        "trace": {"available": traced, "latest_run_id": latest_run.id if latest_run else None},
    }


# =============================================================================
# Inputs (§E.3)
# =============================================================================

def _project_product_scope(db: Session, project_id: str) -> str:
    codes = sorted({row[0] for row in db.query(Model.product_code).filter(Model.project_id == project_id).all()})
    return ", ".join(codes) if codes else "—"


def _assumption_tables(db: Session, project_id: str) -> list[AssumptionTable]:
    return (
        db.query(AssumptionTable)
        .join(AssumptionSet, AssumptionSet.id == AssumptionTable.set_id)
        .filter(AssumptionSet.project_id == project_id)
        .order_by(AssumptionTable.table_name)
        .all()
    )


def _factor_tables(db: Session, project_id: str) -> list[FactorTable]:
    return (
        db.query(FactorTable)
        .join(FactorSet, FactorSet.id == FactorTable.set_id)
        .filter(FactorSet.project_id == project_id)
        .order_by(FactorTable.table_name)
        .all()
    )


def _inforce_item(file: InforceFile, scope: str) -> dict[str, Any]:
    return {
        "id": file.id,
        "category": "liability_inforce",
        "category_label": CATEGORY_LABELS["liability_inforce"],
        "name": file.filename,
        "description": file.description,
        "version_label": file.version_label,
        "scope": scope,
        "record_count": file.row_count or 0,
        "record_unit": "records",
        "status": file.status,
        "fingerprint": file.fingerprint,
        "updated_at": iso(file.uploaded_at),
    }


def _table_item(table: Any, category: str, scope: str) -> dict[str, Any]:
    return {
        "id": table.id,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "name": table.table_name,
        "description": getattr(table, "description", None),
        "version_label": getattr(table, "version_label", None),
        "scope": scope,
        "record_count": len(table.data or []),
        "record_unit": "rows",
        "status": table.status,
        "fingerprint": getattr(table, "fingerprint", None),
        "updated_at": iso(table.created_at),
    }


def list_inputs(db: Session, project_id: str, category: str | None = None, search: str | None = None) -> dict[str, Any]:
    get_project(db, project_id)
    scope = _project_product_scope(db, project_id)
    files = db.query(InforceFile).filter(InforceFile.project_id == project_id).order_by(InforceFile.uploaded_at).all()
    items: list[dict[str, Any]] = [_inforce_item(file, scope) for file in files]
    items += [_table_item(table, "assumption_table", scope) for table in _assumption_tables(db, project_id)]
    items += [_table_item(table, "factor_table", scope) for table in _factor_tables(db, project_id)]

    counts: dict[str, int] = {key: 0 for key in CATEGORY_LABELS}
    for item in items:
        counts[item["category"]] += 1
    selected = items
    if category:
        selected = [item for item in selected if item["category"] == category]
    if search:
        needle = search.lower()
        selected = [
            item for item in selected
            if needle in item["name"].lower() or needle in (item["description"] or "").lower()
        ]
    mappings = input_mappings(db, project_id)
    return {
        "inputs": selected,
        "total": len(selected),
        "summary": {
            "total": len(items),
            "validated": sum(1 for item in items if item["status"] == "validated"),
            "needs_review": sum(1 for item in items if item["status"] == "needs_review"),
            "mapping_count": mappings["total"],
        },
        "categories": [
            {"key": key, "label": label, "count": counts[key], "milestone": CATEGORY_MILESTONES[key]}
            for key, label in CATEGORY_LABELS.items()
        ],
    }


def _input_variables(db: Session, project_id: str) -> list[VariableSpec]:
    versions = (
        db.query(ModelVersion.id)
        .join(Model, Model.id == ModelVersion.model_id)
        .filter(Model.project_id == project_id)
        .all()
    )
    variables: dict[str, VariableSpec] = {}
    for (version_id,) in sorted(versions):
        for name, spec in load_variable_closure(db, version_id, _model_formulas(db, version_id), []).items():
            variables.setdefault(name, spec)
    return sorted(
        (spec for spec in variables.values() if spec.source_type in ("input", "context")),
        key=lambda spec: spec.name,
    )


def _file_columns(file: InforceFile) -> list[str]:
    columns = file.columns_detected
    if isinstance(columns, dict):
        return list(columns.keys())
    return list(columns or [])


def input_mappings(db: Session, project_id: str, inforce_file_id: str | None = None) -> dict[str, Any]:
    get_project(db, project_id)
    query = db.query(InforceFile).filter(InforceFile.project_id == project_id)
    if inforce_file_id:
        query = query.filter(InforceFile.id == inforce_file_id)
    files = query.order_by(InforceFile.uploaded_at).all()
    variables = _input_variables(db, project_id)
    mappings: list[dict[str, Any]] = []
    for file in files:
        columns = set(_file_columns(file))
        for spec in variables:
            if spec.source_type == "input":
                column = spec.source.get("column") or spec.name
                if spec.data_type == "number":
                    transformation = f"Direct (number{', ' + spec.unit if spec.unit else ''})"
                else:
                    transformation = "Direct (text, upper-case)" if spec.source.get("transform") == "upper" else "Direct (text)"
                mappings.append({
                    "source_field": column,
                    "target_variable": spec.name,
                    "transformation": transformation,
                    "status": "validated" if column in columns else "needs_review",
                    "inforce_file_id": file.id,
                })
            elif spec.source.get("field") == "attained_age":
                mappings.append({
                    "source_field": "issue_age",
                    "target_variable": spec.name,
                    "transformation": "issue_age + completed policy years since issue_date (context)",
                    "status": "validated" if "issue_age" in columns else "needs_review",
                    "inforce_file_id": file.id,
                })
    return {"mappings": mappings, "total": len(mappings)}


def inforce_detail(db: Session, file_id: str) -> dict[str, Any]:
    file = db.get(InforceFile, file_id)
    if file is None:
        raise not_found(f"Inforce file '{file_id}' not found.")
    mappings = input_mappings(db, file.project_id, file.id)["mappings"]
    mapped = [item for item in mappings if item["status"] == "validated"]
    record_total = db.query(func.count(InforceRecord.id)).filter(InforceRecord.file_id == file.id).scalar() or 0
    distinct_ids = (
        db.query(func.count(func.distinct(InforceRecord.policy_id)))
        .filter(InforceRecord.file_id == file.id)
        .scalar()
        or 0
    )
    preview = (
        db.query(InforceRecord)
        .filter(InforceRecord.file_id == file.id)
        .order_by(InforceRecord.policy_id)
        .limit(20)
        .all()
    )
    checks = [
        {"code": "schema_compatible", "label": "Schema compatible",
         "status": "pass" if len(mapped) == len(mappings) else "fail",
         "message": "All mapped columns present" if len(mapped) == len(mappings)
         else f"{len(mappings) - len(mapped)} mapped column(s) missing"},
        {"code": "unique_policy_ids", "label": "Policy IDs unique",
         "status": "pass" if distinct_ids == record_total else "fail",
         "message": f"{distinct_ids} unique of {record_total}"},
        {"code": "blocking_issues", "label": "No blocking issues",
         "status": "pass" if file.status == "validated" else "warning",
         "message": "No blocking issues" if file.status == "validated" else "Review warnings"},
    ]
    return {
        "id": file.id,
        "project_id": file.project_id,
        "category": "liability_inforce",
        "name": file.filename,
        "description": file.description,
        "file_type": file.file_type,
        "version_label": file.version_label,
        "status": file.status,
        "record_count": record_total,
        "columns": _file_columns(file),
        "fingerprint": file.fingerprint,
        "uploaded_at": iso(file.uploaded_at),
        "mapping": {
            "required_count": len(mappings),
            "mapped_count": len(mapped),
            "status": "validated" if len(mapped) == len(mappings) else "needs_review",
        },
        "validation": {
            "status": "validated" if all(c["status"] == "pass" for c in checks) else "needs_review",
            "checks": checks,
        },
        "preview": [record.data for record in preview],
    }


def inforce_records(db: Session, file_id: str, limit: int, offset: int) -> dict[str, Any]:
    file = db.get(InforceFile, file_id)
    if file is None:
        raise not_found(f"Inforce file '{file_id}' not found.")
    query = db.query(InforceRecord).filter(InforceRecord.file_id == file.id)
    total = query.count()
    rows = query.order_by(InforceRecord.policy_id, InforceRecord.id).offset(offset).limit(limit).all()
    return {
        "records": [{"policy_id": row.policy_id, "data": row.data} for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def table_detail(db: Session, table_id: str, kind: str, limit: int, offset: int) -> dict[str, Any]:
    if kind == "assumption":
        table = db.get(AssumptionTable, table_id)
        set_row = db.get(AssumptionSet, table.set_id) if table else None
        category = "assumption_table"
    else:
        table = db.get(FactorTable, table_id)
        set_row = db.get(FactorSet, table.set_id) if table else None
        category = "factor_table"
    if table is None:
        raise not_found(f"Table '{table_id}' not found.")
    rows = list(table.data or [])
    columns: list[str] = []
    for row in rows[:50]:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    return {
        "id": table.id,
        "category": category,
        "set": {"id": set_row.id, "name": set_row.name} if set_row else None,
        "name": table.table_name,
        "table_type": table.table_type,
        "description": getattr(table, "description", None),
        "lookup_keys": list(table.lookup_keys or []),
        "value_column": getattr(table, "value_column", None),
        "columns": columns,
        "version_label": getattr(table, "version_label", None),
        "status": table.status,
        "fingerprint": getattr(table, "fingerprint", None),
        "rows": rows[offset: offset + limit],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


def validation_issues(db: Session, project_id: str) -> dict[str, Any]:
    get_project(db, project_id)
    issues: list[dict[str, Any]] = []
    for mapping in input_mappings(db, project_id)["mappings"]:
        if mapping["status"] != "validated":
            issues.append({
                "id": f"inforce_file:{mapping['inforce_file_id']}:{mapping['target_variable']}",
                "category": "liability_inforce",
                "category_label": CATEGORY_LABELS["liability_inforce"],
                "severity": "error",
                "status": "needs_review",
                "title": CATEGORY_LABELS["liability_inforce"],
                "message": f"Column '{mapping['source_field']}' (for {mapping['target_variable']}) "
                           "is missing from the inforce file.",
                "target": {"type": "inforce_file", "id": mapping["inforce_file_id"]},
            })
    for table in _assumption_tables(db, project_id):
        if table.status == "needs_review":
            issues.append({
                "id": f"assumption_table:{table.id}",
                "category": "assumption_table",
                "category_label": CATEGORY_LABELS["assumption_table"],
                "severity": "warning",
                "status": "needs_review",
                "title": CATEGORY_LABELS["assumption_table"],
                "message": f"Table '{table.table_name}' needs review.",
                "target": {"type": "assumption_table", "id": table.id},
            })
    for projection_set in db.query(ProjectionSet).filter(ProjectionSet.project_id == project_id).all():
        validation = projection_set.validation or {}
        for check in validation.get("checks", []):
            if check.get("status") in ("warning", "fail"):
                issues.append({
                    "id": f"projection_set:{projection_set.id}:{check.get('code')}",
                    "category": "projection_inputs",
                    "category_label": CATEGORY_LABELS["projection_inputs"],
                    "severity": "error" if check.get("status") == "fail" else "warning",
                    "status": "needs_review",
                    "title": f"{CATEGORY_LABELS['projection_inputs']} — {projection_set.name}",
                    "message": check.get("message") or check.get("label"),
                    "target": {"type": "projection_set", "id": projection_set.id},
                })
    return {"issues": issues, "total": len(issues)}


# =============================================================================
# Scenarios (§E.4)
# =============================================================================

def _scenario_consumers(db: Session, project_id: str, scenario_id: str) -> list[ProjectionSet]:
    return [
        ps for ps in db.query(ProjectionSet).filter(ProjectionSet.project_id == project_id).all()
        if scenario_id in (ps.scenario_ids or [])
    ]


def _scenario_summary(db: Session, project_id: str, scenario: ScenarioTable) -> dict[str, Any]:
    overrides = list(scenario.overrides or [])
    return {
        "id": scenario.id,
        "name": scenario.scenario_name,
        "description": scenario.description,
        "scenario_type": scenario.scenario_type,
        "as_of_date": iso(scenario.as_of_date),
        "path_count": scenario.path_count,
        "status": scenario.status,
        "version_label": scenario.version_label,
        "override_count": len(overrides),
        "variables": sorted({item.get("target_variable") for item in overrides if item.get("target_variable")}),
        "consumer_count": len(_scenario_consumers(db, project_id, scenario.id)),
    }


def list_scenario_sets(db: Session, project_id: str) -> dict[str, Any]:
    get_project(db, project_id)
    sets = db.query(ScenarioSet).filter(ScenarioSet.project_id == project_id).order_by(ScenarioSet.created_at).all()
    result = []
    for scenario_set in sets:
        scenarios = (
            db.query(ScenarioTable)
            .filter(ScenarioTable.set_id == scenario_set.id)
            .order_by(ScenarioTable.created_at, ScenarioTable.scenario_name)
            .all()
        )
        result.append({
            "id": scenario_set.id,
            "name": scenario_set.name,
            "description": scenario_set.description,
            "scenario_count": len(scenarios),
            "scenarios": [_scenario_summary(db, project_id, scenario) for scenario in scenarios],
        })
    return {"scenario_sets": result, "total": len(result)}


def get_scenario(db: Session, scenario_id: str) -> dict[str, Any]:
    scenario = db.get(ScenarioTable, scenario_id)
    if scenario is None:
        raise not_found(f"Scenario '{scenario_id}' not found.")
    scenario_set = db.get(ScenarioSet, scenario.set_id)
    consumers = _scenario_consumers(db, scenario_set.project_id, scenario.id)
    units = {name: spec.unit for name, spec in _scenario_variable_specs(db, consumers).items()}
    return {
        "id": scenario.id,
        "set": {"id": scenario_set.id, "name": scenario_set.name},
        "name": scenario.scenario_name,
        "description": scenario.description,
        "scenario_type": scenario.scenario_type,
        "as_of_date": iso(scenario.as_of_date),
        "path_count": scenario.path_count,
        "status": scenario.status,
        "version_label": scenario.version_label,
        "fingerprint": scenario.fingerprint,
        "source_label": "Defined manually",
        "overrides": [
            {
                "target_variable": item.get("target_variable"),
                "operation": item.get("operation", "set"),
                "value": _number(item.get("value")),
                "applies_from_period": item.get("applies_from_period"),
                "applies_to_period": item.get("applies_to_period"),
                "unit": units.get(item.get("target_variable")),
            }
            for item in scenario.overrides or []
        ],
        "consumers": {"projection_sets": [{"id": ps.id, "name": ps.name} for ps in consumers]},
        "created_at": iso(scenario.created_at),
    }


def _scenario_variable_specs(db: Session, consumers: list[ProjectionSet]) -> dict[str, VariableSpec]:
    """Variable definitions of the model version used by the scenario's first consumer (by name)."""
    for projection_set in sorted(consumers, key=lambda ps: (ps.name, ps.version_label)):
        if projection_set.model_version_id:
            return variable_specs(db, projection_set.model_version_id)
    return {}


def _number(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _effective_value(base: Any, override: dict | None) -> Any:
    if override is None:
        return base
    operand = _number(override.get("value"))
    operation = override.get("operation", "set")
    if operation == "set" or base is None:
        return operand
    return {
        "add": base + operand,
        "subtract": base - operand,
        "multiply": base * operand,
        "percent_change": base * (1 + operand),
    }.get(operation, operand)


def compare_scenarios(db: Session, baseline_id: str, compare_id: str) -> dict[str, Any]:
    baseline = db.get(ScenarioTable, baseline_id)
    compare = db.get(ScenarioTable, compare_id)
    if baseline is None or compare is None:
        raise not_found("One or both scenarios were not found.")
    scenario_set = db.get(ScenarioSet, baseline.set_id)
    variables = _scenario_variable_specs(db, _scenario_consumers(db, scenario_set.project_id, baseline.id))
    base_overrides = {item.get("target_variable"): item for item in baseline.overrides or []}
    comp_overrides = {item.get("target_variable"): item for item in compare.overrides or []}
    differences = []
    for name in sorted(set(base_overrides) | set(comp_overrides)):
        spec = variables.get(name)
        default = _number(spec.source.get("value", spec.default_value)) if spec else None
        base_value = _effective_value(default, base_overrides.get(name))
        comp_value = _effective_value(default, comp_overrides.get(name))
        differences.append({
            "variable": name,
            "baseline": {
                "operation": (base_overrides.get(name) or {}).get("operation"),
                "value": base_value,
                "source": "scenario override" if name in base_overrides else "variable default",
            },
            "compare": {
                "operation": (comp_overrides.get(name) or {}).get("operation"),
                "value": comp_value,
                "source": "scenario override" if name in comp_overrides else "variable default",
            },
            "difference": (
                comp_value - base_value
                if isinstance(comp_value, (int, float)) and isinstance(base_value, (int, float))
                else None
            ),
        })

    downstream: dict[str, Any] = {"available": False}
    scenario_set = db.get(ScenarioSet, baseline.set_id)
    for projection_set in _scenario_consumers(db, scenario_set.project_id, baseline.id):
        if compare.id not in (projection_set.scenario_ids or []):
            continue
        runs = {}
        for scenario_id in (baseline.id, compare.id):
            runs[scenario_id] = (
                db.query(Run)
                .filter(Run.projection_set_id == projection_set.id, Run.scenario_id == scenario_id,
                        Run.status == "success")
                .order_by(Run.completed_at.desc())
                .first()
            )
        if all(runs.values()):
            base_headline = (runs[baseline.id].summary or {}).get("headline") or {}
            comp_headline = (runs[compare.id].summary or {}).get("headline") or {}
            if base_headline and comp_headline:
                base_value = base_headline.get("value")
                comp_value = comp_headline.get("value")
                downstream = {
                    "available": True,
                    "projection_set": {"id": projection_set.id, "name": projection_set.name},
                    "metric": base_headline.get("metric"),
                    "label": base_headline.get("label"),
                    "baseline_value": base_value,
                    "compare_value": comp_value,
                    "difference_pct": (
                        (comp_value - base_value) / base_value * 100 if base_value else None
                    ),
                    "baseline_run_id": runs[baseline.id].id,
                    "compare_run_id": runs[compare.id].id,
                }
                break
    return {
        "baseline": {"id": baseline.id, "name": baseline.scenario_name},
        "compare": {"id": compare.id, "name": compare.scenario_name},
        "input_differences": differences,
        "downstream": downstream,
    }
