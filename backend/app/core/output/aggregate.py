"""Aggregations over Parquet-backed projection outputs."""

from sqlalchemy.orm import Session

from app.core.output.storage import get_run_results
from app.models.schemas import ProjectionSummary


def compute_summary(
    db: Session,
    run_id: str,
    error_count: int = 0,
) -> ProjectionSummary:
    outputs = get_run_results(db, run_id)
    return ProjectionSummary(
        policy_count=len({row["policy_id"] for row in outputs}),
        period_count=max((row["month"] for row in outputs), default=0),
        scenario_count=len({row["scenario_id"] for row in outputs}),
        calculated_variable_count=len(outputs),
        error_count=error_count,
        warning_count=0,
    )


def get_aggregates(
    db: Session,
    run_id: str,
    variable_name: str | None = None,
    period: int | None = None,
) -> dict[str, float | int]:
    outputs = get_run_results(db, run_id)
    values: list[float] = []
    for output in outputs:
        if variable_name and output["variable"] != variable_name:
            continue
        if period is not None and output["month"] != period:
            continue
        try:
            values.append(float(output["value"]))
        except (ValueError, TypeError):
            continue
    total = sum(values)
    return {
        "count": len(values),
        "sum": total,
        "avg": total / len(values) if values else 0,
    }
