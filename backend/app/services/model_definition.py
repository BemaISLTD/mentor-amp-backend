"""Reads the *live, editable* model definition of one model version: formulas and variables.

Used by the catalog screens, Projection Set validation and — once, at submission — by the run
package freeze. Execution never calls this module: a submitted run reads only its package.

Variables resolve ONLY through the model version's own ``model_variable_definitions`` rows
(UNIQUE per model version and name). There is no global fallback: a variable a formula needs but
the model version does not define is reported as missing.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.projection_engine.run_data import FormulaSpec, VariableSpec
from app.db.models.formula import FormulaRegistry
from app.db.models.model_variable import ModelVariableDefinition


def to_variable_spec(row: ModelVariableDefinition) -> VariableSpec:
    return VariableSpec(
        name=row.variable_name,
        kind=row.kind,
        data_type=row.data_type,
        unit=row.unit,
        source=dict(row.source or {}),
        default_value=row.default_value,
        required=bool(row.required),
        display_name=row.display_name,
        version=row.version or "v1",
        allow_scenario_override=bool(row.allow_scenario_override),
    )


def to_formula_spec(row: FormulaRegistry) -> FormulaSpec:
    return FormulaSpec(
        id=row.id,
        name=row.name,
        output_variable=row.output_variable,
        function_ref=row.function_ref,
        dependencies=tuple(sorted(dep.depends_on_variable for dep in row.dependencies)),
        expression_text=row.expression_text,
        version=row.version or "v1",
        illustrative=bool(row.illustrative),
    )


def referenced_names(spec: VariableSpec) -> set[str]:
    """Other variables a variable's resolution rule depends on (lookup keys, prior outputs...)."""
    source = spec.source
    names: set[str] = set()
    names.update((source.get("key_map") or {}).values())
    for key in ("variable", "cash_flow", "rate", "consistency_sum_of"):
        if source.get(key):
            names.add(str(source[key]))
    return names


def load_model_formulas(db: Session, model_version_id: str) -> list[FormulaRegistry]:
    return (
        db.query(FormulaRegistry)
        .filter(
            FormulaRegistry.model_version_id == model_version_id,
            FormulaRegistry.deleted_at.is_(None),
        )
        .order_by(FormulaRegistry.output_variable)
        .all()
    )


def definitions_for(db: Session, model_version_id: str) -> dict[str, ModelVariableDefinition]:
    """The model version's variable definitions by name (unique by database constraint)."""
    rows = (
        db.query(ModelVariableDefinition)
        .filter(ModelVariableDefinition.model_version_id == model_version_id)
        .order_by(ModelVariableDefinition.variable_name)
        .all()
    )
    return {row.variable_name: row for row in rows}


def variable_closure_rows(
    db: Session, model_version_id: str, formulas: list[FormulaRegistry], extra: list[str]
) -> tuple[dict[str, ModelVariableDefinition], set[str]]:
    """Definitions of every variable the formulas and outputs need, and any names missing."""
    rows = definitions_for(db, model_version_id)
    wanted: set[str] = set(extra)
    for formula in formulas:
        wanted.add(formula.output_variable)
        wanted.update(dep.depends_on_variable for dep in formula.dependencies)
    found: dict[str, ModelVariableDefinition] = {}
    missing: set[str] = set()
    pending = sorted(wanted)
    while pending:
        name = pending.pop()
        if name in found or name in missing:
            continue
        row = rows.get(name)
        if row is None:
            missing.add(name)
            continue
        found[name] = row
        pending.extend(sorted(referenced_names(to_variable_spec(row)) - found.keys()))
    return found, missing


def load_variable_closure(
    db: Session, model_version_id: str, formulas: list[FormulaRegistry], extra: list[str]
) -> dict[str, VariableSpec]:
    """All variables the formulas and outputs need, following keys, prior outputs and valuation."""
    rows, _missing = variable_closure_rows(db, model_version_id, formulas, extra)
    return {name: to_variable_spec(row) for name, row in rows.items()}


def variable_specs(db: Session, model_version_id: str) -> dict[str, VariableSpec]:
    """Every variable the model version defines (whether or not a formula uses it)."""
    return {name: to_variable_spec(row) for name, row in definitions_for(db, model_version_id).items()}


def definition_view(row: ModelVariableDefinition) -> dict[str, Any]:
    return {
        "id": row.id,
        "model_version_id": row.model_version_id,
        "variable_name": row.variable_name,
        "display_name": row.display_name,
        "description": row.description,
        "kind": row.kind,
        "data_type": row.data_type,
        "unit": row.unit,
        "required": bool(row.required),
        "default_value": row.default_value,
        "source": row.source,
        "allow_scenario_override": bool(row.allow_scenario_override),
        "version": row.version,
    }
