"""Projection output artifact persistence and retrieval."""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.artifacts import RunArtifactBuffer, get_artifact_store
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact


def save_output(
    buffer: RunArtifactBuffer,
    run_id: str,
    policy_id: str,
    scenario_id: str,
    month: int,
    variable_name: str,
    value: Any,
    product: str = "",
) -> None:
    if buffer.run_id != run_id:
        raise ValueError("Output buffer does not belong to this run.")
    buffer.add_output(
        policy_id, scenario_id, month, variable_name, value, product=product
    )


def _output_rows(db: Session, run_id: str) -> list[dict[str, Any]]:
    artifacts = (
        db.query(RunArtifact)
        .filter(
            RunArtifact.run_id == run_id,
            RunArtifact.artifact_type == "outputs",
        )
        .order_by(RunArtifact.created_at, RunArtifact.id)
        .all()
    )
    store = get_artifact_store()
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.storage_backend != store.backend_name:
            raise ValueError(
                f"Artifact backend '{artifact.storage_backend}' is not configured."
            )
        rows.extend(store.read_rows(artifact.storage_uri))
    return rows


def _decoded_value(row: dict[str, Any]) -> Any:
    return json.loads(row["value_json"])


def get_output(
    db: Session,
    run_id: str,
    policy_id: str,
    scenario_id: str,
    month: int,
    variable_name: str,
) -> Any:
    for row in _output_rows(db, run_id):
        if (
            row["policy_id"] == policy_id
            and row["scenario_id"] == scenario_id
            and row["projection_month"] == month
            and row["variable_name"] == variable_name
        ):
            return _decoded_value(row)
    return None


def get_policy_outputs(
    db: Session,
    run_id: str,
    policy_id: str,
) -> list[dict[str, Any]]:
    rows = [row for row in _output_rows(db, run_id) if row["policy_id"] == policy_id]
    rows.sort(key=lambda row: (row["projection_month"], row["variable_name"]))
    return [
        {
            "scenario_id": row["scenario_id"],
            "month": row["projection_month"],
            "variable": row["variable_name"],
            "value": _decoded_value(row),
        }
        for row in rows
    ]


def get_run_results(db: Session, run_id: str) -> list[dict[str, Any]]:
    rows = _output_rows(db, run_id)
    rows.sort(
        key=lambda row: (
            row["policy_id"],
            row["scenario_id"],
            row["projection_month"],
            row["variable_name"],
        )
    )
    return [
        {
            "policy_id": row["policy_id"],
            "scenario_id": row["scenario_id"],
            "month": row["projection_month"],
            "variable": row["variable_name"],
            "value": _decoded_value(row),
        }
        for row in rows
    ]


def update_run_status(db: Session, run_id: str, status: str) -> None:
    run = db.query(Run).filter(Run.id == run_id).first()
    if run:
        run.status = status
        if status in ("success", "partial_success", "failed"):
            run.completed_at = datetime.now(timezone.utc)
        db.flush()
