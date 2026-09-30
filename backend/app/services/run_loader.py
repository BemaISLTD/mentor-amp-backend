"""Loads everything one run needs from the database, once, into ``RunData`` (contract §F.1 step 6).

This is the only place a run reads the database. Keeping the reads here (a handful of queries)
makes runs fast against a remote database such as Neon and keeps the engine storage-free.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.core.projection_engine.engine import EngineError, order_formulas
from app.core.projection_engine.run_data import (
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    TableSpec,
    VariableSpec,
)
from app.db.models.assumption import AssumptionSet, AssumptionTable
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.projection import ProjectionSet
from app.db.models.run import Run
from app.db.models.scenario import ScenarioTable
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
    source = variable_source(row)
    return VariableSpec(
        name=row.name,
        kind=row.source_type,
        data_type=row.data_type,
        unit=row.unit,
        source=source,
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


def _referenced_names(spec: VariableSpec) -> set[str]:
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


def load_variable_closure(
    db: Session, formulas: list[FormulaRegistry], extra: list[str]
) -> dict[str, VariableSpec]:
    """All variables the formulas and outputs need, following keys, prior outputs and valuation."""
    rows = {row.name: row for row in db.query(VariableRegistry).all()}
    wanted: set[str] = set(extra)
    for formula in formulas:
        wanted.add(formula.output_variable)
        wanted.update(dep.depends_on_variable for dep in formula.dependencies)
    specs: dict[str, VariableSpec] = {}
    pending = list(wanted)
    while pending:
        name = pending.pop()
        if name in specs or name not in rows:
            continue
        spec = to_variable_spec(rows[name])
        specs[name] = spec
        pending.extend(_referenced_names(spec) - specs.keys())
    return specs


def load_tables(
    db: Session, project_id: str, variables: dict[str, VariableSpec], pinned_ids: list[str]
) -> dict[str, TableSpec]:
    tables: dict[str, TableSpec] = {}
    for spec in variables.values():
        kind = spec.source_type
        if kind not in ("assumption", "factor"):
            continue
        name = spec.source.get("table")
        if not name or name in tables:
            continue
        if kind == "assumption":
            query = (
                db.query(AssumptionTable)
                .join(AssumptionSet, AssumptionSet.id == AssumptionTable.set_id)
                .filter(AssumptionSet.project_id == project_id, AssumptionTable.table_name == name)
            )
            if pinned_ids:
                query = query.filter(AssumptionTable.id.in_(pinned_ids))
            row = query.order_by(AssumptionTable.created_at.desc()).first()
        else:
            row = (
                db.query(FactorTable)
                .join(FactorSet, FactorSet.id == FactorTable.set_id)
                .filter(FactorSet.project_id == project_id, FactorTable.table_name == name)
                .order_by(FactorTable.created_at.desc())
                .first()
            )
        if row is None:
            raise EngineError(
                "lookup_failed",
                f"Table '{name}' needed by '{spec.name}' is not pinned in the Projection Set.",
                spec.name,
            )
        value_column = getattr(row, "value_column", None) or spec.source.get("value_column")
        if not value_column:
            raise EngineError(
                "lookup_failed", f"Table '{name}' has no value column.", spec.name
            )
        table = TableSpec(
            id=row.id,
            name=row.table_name,
            key_columns=tuple(row.lookup_keys or []),
            value_column=value_column,
            rows=list(row.data or []),
            fingerprint=getattr(row, "fingerprint", None),
            kind=kind,
        )
        duplicates = table.build_index()
        if duplicates:
            raise EngineError(
                "lookup_failed",
                f"Table '{name}' is ambiguous: duplicate keys {duplicates[:3]}.",
                spec.name,
            )
        tables[name] = table
    return tables


def load_policies(db: Session, inforce_file_ids: list[str]) -> list[PolicyRecord]:
    if not inforce_file_ids:
        return []
    files = {
        file.id: file
        for file in db.query(InforceFile).filter(InforceFile.id.in_(inforce_file_ids)).all()
    }
    records = (
        db.query(InforceRecord)
        .filter(InforceRecord.file_id.in_(inforce_file_ids))
        .order_by(InforceRecord.policy_id, InforceRecord.id)
        .all()
    )
    return [
        PolicyRecord(
            policy_id=record.policy_id,
            data=dict(record.data or {}),
            dataset_id=record.file_id,
            dataset_name=files[record.file_id].filename if record.file_id in files else None,
        )
        for record in records
    ]


def load_scenario(db: Session, scenario_id: str | None) -> ScenarioSpec:
    if not scenario_id:
        return ScenarioSpec(id=None, name="No scenario", overrides=())
    row = db.get(ScenarioTable, scenario_id)
    if row is None:
        raise EngineError("scenario_override_failed", f"Scenario '{scenario_id}' not found.")
    return ScenarioSpec(id=row.id, name=row.scenario_name, overrides=tuple(row.overrides or []))


def traced_policy_ids(trace_scope: dict | None, policies: list[PolicyRecord]) -> frozenset[str]:
    scope = trace_scope or {}
    mode = scope.get("mode", "none")
    available = [policy.policy_id for policy in policies]
    if mode == "all":
        return frozenset(available[: settings.max_traced_policies])
    if mode == "selected_policies":
        wanted = set(scope.get("policy_ids") or [])
        chosen = [policy_id for policy_id in available if policy_id in wanted]
        return frozenset(chosen[: settings.max_traced_policies])
    return frozenset()


def load_run_data(db: Session, run: Run) -> RunData:
    projection_set = db.get(ProjectionSet, run.projection_set_id) if run.projection_set_id else None
    if projection_set is None:
        raise EngineError("invalid_formula", "The run has no Projection Set.")
    model_version_id = run.model_version_id or projection_set.model_version_id
    if not model_version_id:
        raise EngineError("invalid_formula", "The Projection Set has no model version.")

    formula_rows = load_model_formulas(db, model_version_id)
    if not formula_rows:
        raise EngineError("invalid_formula", "The model version has no formulas.")
    variables = load_variable_closure(db, formula_rows, list(projection_set.output_variables or []))
    formulas = order_formulas([to_formula_spec(row) for row in formula_rows])
    tables = load_tables(
        db, projection_set.project_id, variables, list(projection_set.assumption_table_ids or [])
    )
    policies = load_policies(db, list(projection_set.inforce_file_ids or []))
    return RunData(
        variables=variables,
        formulas=formulas,
        tables=tables,
        policies=policies,
        scenario=load_scenario(db, run.scenario_id),
        valuation_date=run.valuation_date or projection_set.valuation_date,
        horizon_months=run.horizon_months or projection_set.horizon_months,
        output_variables=list(projection_set.output_variables or []),
        traced_policy_ids=traced_policy_ids(projection_set.trace_scope, policies),
    )
