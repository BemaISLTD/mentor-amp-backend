"""Reads the *live, editable* model definition: formulas and the variables they need.

Used by the catalog screens, Projection Set validation and — once, at submission — by the run
package freeze. Execution never calls this module: a submitted run reads only its package.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.projection_engine.run_data import FormulaSpec, VariableSpec
from app.db.models.formula import FormulaRegistry
from app.db.models.variable import VariableRegistry


def variable_source(row: VariableRegistry) -> dict[str, Any]:
    """The full resolution rule; falls back to the pre-M1 columns for older rows."""
    if row.source:
        return dict(row.source)
    source: dict[str, Any] = {"type": row.source_type}
    if row.source_table:
        source["table"] = row.source_table
    if row.lookup_keys:
        source["key_map"] = {key: key for key in row.lookup_keys}
    if row.source_type == "input":
        source["column"] = row.name
    return source


def variable_default(row: VariableRegistry) -> Any:
    if isinstance(row.default_value, dict):
        return row.default_value.get("value")
    return row.default_value


def to_variable_spec(row: VariableRegistry) -> VariableSpec:
    return VariableSpec(
        name=row.name,
        kind=row.source_type,
        data_type=row.data_type,
        unit=row.unit,
        source=variable_source(row),
        default_value=variable_default(row),
        required=bool(row.required),
        display_name=row.display_name,
        version=row.version or "v1",
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
        .filter(FormulaRegistry.model_version_id == model_version_id)
        .order_by(FormulaRegistry.output_variable)
        .all()
    )


def variable_closure_rows(
    db: Session, formulas: list[FormulaRegistry], extra: list[str]
) -> tuple[dict[str, VariableRegistry], set[str]]:
    """Registry rows of every variable the formulas and outputs need, and any names missing."""
    rows = {row.name: row for row in db.query(VariableRegistry).all()}
    wanted: set[str] = set(extra)
    for formula in formulas:
        wanted.add(formula.output_variable)
        wanted.update(dep.depends_on_variable for dep in formula.dependencies)
    found: dict[str, VariableRegistry] = {}
    missing: set[str] = set()
    pending = list(wanted)
    while pending:
        name = pending.pop()
        if name in found or name in missing:
            continue
        row = rows.get(name)
        if row is None:
            missing.add(name)
            continue
        found[name] = row
        pending.extend(referenced_names(to_variable_spec(row)) - found.keys())
    return found, missing


def load_variable_closure(
    db: Session, formulas: list[FormulaRegistry], extra: list[str]
) -> dict[str, VariableSpec]:
    """All variables the formulas and outputs need, following keys, prior outputs and valuation."""
    rows, _missing = variable_closure_rows(db, formulas, extra)
    return {name: to_variable_spec(row) for name, row in rows.items()}
