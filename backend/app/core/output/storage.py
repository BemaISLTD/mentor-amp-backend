"""Output storage — persists and retrieves projection results."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.run import Run
from app.db.models.run_output import RunOutput


def save_output(
    db: Session,
    run_id: str,
    policy_id: str,
    scenario_id: str,
    month: int,
    variable_name: str,
    value: Any,
    product: str = "",
) -> RunOutput:
    """Store a single calculated value."""
    output = RunOutput(
        run_id=run_id,
        policy_id=policy_id,
        scenario_id=scenario_id,
        projection_month=month,
        variable_name=variable_name,
        value={"value": value} if not isinstance(value, dict) else value,
        product=product,
    )
    db.add(output)
    db.flush()
    return output


def get_output(
    db: Session,
    run_id: str,
    policy_id: str,
    scenario_id: str,
    month: int,
    variable_name: str,
) -> Any:
    """Retrieve a single stored value."""
    output = (
        db.query(RunOutput)
        .filter(
            RunOutput.run_id == run_id,
            RunOutput.policy_id == policy_id,
            RunOutput.scenario_id == scenario_id,
            RunOutput.projection_month == month,
            RunOutput.variable_name == variable_name,
        )
        .first()
    )
    if output is None or output.value is None:
        return None
    return output.value.get("value") if isinstance(output.value, dict) else output.value


def get_policy_outputs(
    db: Session,
    run_id: str,
    policy_id: str,
) -> list[dict]:
    """Get all outputs for a specific policy in a run."""
    outputs = (
        db.query(RunOutput)
        .filter(
            RunOutput.run_id == run_id,
            RunOutput.policy_id == policy_id,
        )
        .order_by(RunOutput.projection_month, RunOutput.variable_name)
        .all()
    )
    return [
        {
            "scenario_id": o.scenario_id,
            "month": o.projection_month,
            "variable": o.variable_name,
            "value": o.value.get("value") if isinstance(o.value, dict) else o.value,
        }
        for o in outputs
    ]


def get_run_results(db: Session, run_id: str) -> list[dict]:
    """Get all outputs for an entire run."""
    outputs = (
        db.query(RunOutput)
        .filter(RunOutput.run_id == run_id)
        .order_by(RunOutput.policy_id, RunOutput.scenario_id, RunOutput.projection_month)
        .all()
    )
    return [
        {
            "policy_id": o.policy_id,
            "scenario_id": o.scenario_id,
            "month": o.projection_month,
            "variable": o.variable_name,
            "value": o.value.get("value") if isinstance(o.value, dict) else o.value,
        }
        for o in outputs
    ]


def update_run_status(db: Session, run_id: str, status: str) -> None:
    """Update the status of a run."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if run:
        run.status = status
        if status in ("success", "partial_success", "failed"):
            run.completed_at = datetime.now(timezone.utc)
        db.flush()
