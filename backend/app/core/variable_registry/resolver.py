"""Variable Resolver — fetches actual values given a variable name and context."""

from typing import Any

from sqlalchemy.orm import Session

from app.core.variable_registry.registry import get_by_name
from app.db.models.assumption import AssumptionTable
from app.db.models.factor import FactorTable
from app.db.models.inforce import InforceRecord
from app.db.models.run_output import RunOutput
from app.db.models.scenario import ScenarioTable
from app.models.schemas import ProjectionContext, VariableResolutionResult


def build_lookup_keys(definition, context: ProjectionContext) -> dict:
    keys: dict[str, Any] = {}
    if context.policy_id:
        keys["policy_id"] = context.policy_id
    if context.projection_month is not None:
        keys["projection_month"] = context.projection_month
    if context.attained_age is not None:
        keys["age"] = context.attained_age
    if context.duration is not None:
        keys["duration"] = context.duration
    if context.valuation_date:
        keys["valuation_date"] = context.valuation_date
    if context.scenario_id:
        keys["scenario_id"] = context.scenario_id
    if context.product:
        keys["product_type"] = context.product
    return keys


def _get_source_attr(source, attr: str, default=None):
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(attr, default)
    return getattr(source, attr, default)


def _inforce_lookup(db: Session, policy_id: str) -> dict | None:
    """Look up the full inforce record for a policy."""
    if not policy_id:
        return None
    record = db.query(InforceRecord).filter(
        InforceRecord.policy_id == policy_id
    ).order_by(InforceRecord.created_at.desc()).first()
    if record is None or not record.data:
        return None
    return record.data


def _assumption_lookup(db: Session, table_name: str, keys: dict) -> Any:
    table = db.query(AssumptionTable).filter(AssumptionTable.table_name == table_name).first()
    if table is None or not table.data:
        return None
    age = keys.get("age")
    duration = keys.get("duration")
    for row in table.data:
        row_age = row.get("age")
        row_duration = row.get("duration")
        if (age is None or row_age is None or str(row_age) == str(age)) and \
           (duration is None or row_duration is None or str(row_duration) == str(duration)):
            return row
    return table.data[0] if table.data else None


def _factor_lookup(db: Session, table_name: str, keys: dict) -> Any:
    table = db.query(FactorTable).filter(FactorTable.table_name == table_name).first()
    if table is None or not table.data:
        return None
    duration = keys.get("duration")
    for row in table.data:
        row_duration = row.get("duration")
        if duration is None or row_duration is None or str(row_duration) == str(duration):
            return row
    return table.data[0] if table.data else None


def _scenario_lookup(db: Session, scenario_id: str, variable_name: str, context: ProjectionContext) -> Any:
    table = db.query(ScenarioTable).filter(ScenarioTable.scenario_name == scenario_id).first()
    if table is None or not table.overrides:
        return None
    month = context.projection_month
    for override in table.overrides:
        if override.get("target_variable") != variable_name:
            continue
        applies_from = override.get("applies_from_period")
        applies_to = override.get("applies_to_period")
        if applies_from is not None and month < applies_from:
            continue
        if applies_to is not None and month > applies_to:
            continue
        return override
    return None


def _prior_period_lookup(db: Session, variable_name: str, context: ProjectionContext, offset: int = 1) -> Any:
    prev_month = context.projection_month - offset
    if prev_month < 0:
        return None
    output = db.query(RunOutput).filter(
        RunOutput.run_id == context.run_id,
        RunOutput.policy_id == context.policy_id,
        RunOutput.scenario_id == context.scenario_id,
        RunOutput.projection_month == prev_month,
        RunOutput.variable_name == variable_name,
    ).first()
    if output is None or output.value is None:
        return None
    return output.value.get("value") if isinstance(output.value, dict) else output.value


def resolve(
    variable_name: str,
    context: ProjectionContext,
    db: Session,
    trace_logger=None,
) -> VariableResolutionResult:
    definition = get_by_name(db, variable_name)

    if definition is None:
        result = VariableResolutionResult(
            variable_name=variable_name, value=None, source_type="unknown",
            error_message=f"Variable '{variable_name}' is not registered.",
        )
        if trace_logger:
            trace_logger(context, result)
        return result

    lookup_keys = build_lookup_keys(definition, context)
    source_type = definition.kind
    source = definition.source
    value: Any = None
    error: str | None = None
    source_table: str | None = None

    try:
        if source_type == "input":
            column_name = _get_source_attr(source, "column_name", variable_name)
            source_table = "inforce_records"
            record = _inforce_lookup(db, lookup_keys.get("policy_id", ""))
            if record is not None and isinstance(record, dict):
                value = record.get(column_name)

        elif source_type == "assumption":
            source_table = _get_source_attr(source, "table_id")
            if source_table:
                raw = _assumption_lookup(db, source_table, lookup_keys)
                if raw and isinstance(raw, dict):
                    value = raw.get("mortality_rate") or raw.get("lapse_rate") or raw
                else:
                    value = raw
            else:
                error = f"No source_table for assumption variable '{variable_name}'."

        elif source_type == "factor":
            source_table = _get_source_attr(source, "table_id")
            if source_table:
                raw = _factor_lookup(db, source_table, lookup_keys)
                value = raw
            else:
                error = f"No source_table for factor variable '{variable_name}'."

        elif source_type == "scenario":
            override = _scenario_lookup(db, context.scenario_id, variable_name, context)
            if override:
                value = override.get("value")
            source_table = context.scenario_id

        elif source_type == "prior_output":
            offset = _get_source_attr(source, "offset_periods", 1)
            value = _prior_period_lookup(db, variable_name, context, offset)
            source_table = "run_outputs"

        elif source_type == "manual":
            value = _get_source_attr(source, "value")
            source_table = "manual"

        elif source_type == "formula":
            value = _prior_period_lookup(db, variable_name, context, offset=0)
            source_table = "formula_output"

        else:
            value = definition.default_value
            source_table = "default"

    except Exception as e:
        error = f"Error resolving '{variable_name}': {str(e)}"

    was_defaulted = False
    if value is None and definition.default_value is not None:
        value = definition.default_value
        was_defaulted = True

    if value is None and definition.required and not was_defaulted:
        if not error:
            error = f"Required variable '{variable_name}' could not be resolved."

    result = VariableResolutionResult(
        variable_name=variable_name, value=value, source_type=source_type,
        source_table=source_table, lookup_keys=lookup_keys,
        was_defaulted=was_defaulted, error_message=error,
    )

    if trace_logger:
        trace_logger(context, result)

    return result
