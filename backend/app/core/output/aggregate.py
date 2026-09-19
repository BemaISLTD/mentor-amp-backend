"""Aggregation — computes summary statistics for projection runs."""

from sqlalchemy.orm import Session

from app.db.models.run_output import RunOutput
from app.models.schemas import ProjectionSummary


def compute_summary(
    db: Session,
    run_id: str,
    error_count: int = 0,
) -> ProjectionSummary:
    """Compute a summary for a completed run."""
    outputs = db.query(RunOutput).filter(RunOutput.run_id == run_id).all()

    policy_ids: set[str] = set()
    scenario_ids: set[str] = set()
    variables: set[str] = set()
    max_month = 0

    for o in outputs:
        policy_ids.add(o.policy_id)
        scenario_ids.add(o.scenario_id)
        variables.add(o.variable_name)
        if o.projection_month > max_month:
            max_month = o.projection_month

    return ProjectionSummary(
        policy_count=len(policy_ids),
        period_count=max_month,
        scenario_count=len(scenario_ids),
        calculated_variable_count=len(outputs),
        error_count=error_count,
        warning_count=0,
    )

def get_aggregates(db, run_id, variable_name=None, period=None):
    from app.db.models.run_output import RunOutput
    query = db.query(RunOutput).filter(RunOutput.run_id == run_id)
    if variable_name:
        query = query.filter(RunOutput.variable_name == variable_name)
    if period:
        query = query.filter(RunOutput.projection_month == period)
    outputs = query.all()
    if not outputs:
        return {'count': 0, 'sum': 0, 'avg': 0}
    values = []
    for o in outputs:
        v = o.value
        if isinstance(v, dict):
            v = v.get('value')
        if v is not None:
            try:
                values.append(float(str(v)))
            except (ValueError, TypeError):
                pass
    return {'count': len(values), 'sum': sum(values) if values else 0, 'avg': (sum(values) / len(values)) if values else 0}
