"""Trace logger — records every variable resolution for later debugging."""

from app.db.models.trace_log import TraceLog
from app.models.schemas import ProjectionContext, VariableResolutionResult


def log_resolution(context: ProjectionContext, result: VariableResolutionResult, db) -> None:
    """Insert a trace log record for a variable resolution.
    
    Only logs if context has a real run_id (not a placeholder).
    """
    # Skip logging for test/placeholder run_ids
    if not context.run_id or context.run_id in ("r1", "test", "placeholder"):
        return

    try:
        trace = TraceLog(
            run_id=context.run_id,
            policy_id=context.policy_id,
            scenario_id=context.scenario_id,
            projection_month=context.projection_month,
            variable_name=result.variable_name,
            output_value={"value": result.value} if result.value is not None else None,
            source_type=result.source_type,
            source_table=result.source_table,
            lookup_keys=result.lookup_keys,
            error_message=result.error_message,
        )
        db.add(trace)
        db.commit()
    except Exception:
        db.rollback()
