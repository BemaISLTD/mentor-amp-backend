"""Parquet-backed projection trace endpoints."""

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.runs import _get_run_or_404
from app.core.artifacts import read_artifact_rows
from app.db.database import get_db
from app.models.runs import RunTraceResponse

router = APIRouter(prefix="/runs", tags=["trace"])


@router.get("/{run_id}/events", response_model=RunTraceResponse)
def list_run_events(
    run_id: str,
    policy_id: str | None = Query(None),
    variable: str | None = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_run_or_404(run_id, db)
    rows = read_artifact_rows(db, run_id, "traces")
    events = [
        {
            "policy_id": row["policy_id"],
            "scenario_id": row["scenario_id"],
            "month": row["projection_month"],
            "variable": row["variable_name"],
            "value": json.loads(row["output_value_json"]),
            "source_type": row["source_type"],
            "source_table": row["source_table"],
            "lookup_keys": json.loads(row["lookup_keys_json"]),
            "error_message": row["error_message"],
        }
        for row in rows
    ]
    if policy_id is not None:
        events = [event for event in events if event["policy_id"] == policy_id]
    if variable is not None:
        events = [event for event in events if event["variable"] == variable]
    total = len(events)
    return RunTraceResponse(
        run_id=run_id,
        events=events[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )
