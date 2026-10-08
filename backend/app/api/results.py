"""Parquet-backed projection result and summary endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.runs import _get_run_or_404
from app.core.output.aggregate import compute_summary
from app.core.output.storage import get_run_results
from app.db.database import get_db
from app.models.runs import RunResultsResponse, RunSummaryResponse

router = APIRouter(prefix="/legacy-runs", tags=["legacy-results"], deprecated=True)


@router.get("/{run_id}/results", response_model=RunResultsResponse)
def list_run_results(
    run_id: str,
    policy_id: str | None = Query(None),
    scenario_id: str | None = Query(None),
    variable: str | None = Query(None),
    month_from: int | None = Query(None, ge=1),
    month_to: int | None = Query(None, ge=1),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_run_or_404(run_id, db)
    rows = get_run_results(db, run_id)
    if policy_id is not None:
        rows = [row for row in rows if row["policy_id"] == policy_id]
    if scenario_id is not None:
        rows = [row for row in rows if row["scenario_id"] == scenario_id]
    if variable is not None:
        rows = [row for row in rows if row["variable"] == variable]
    if month_from is not None:
        rows = [row for row in rows if row["month"] >= month_from]
    if month_to is not None:
        rows = [row for row in rows if row["month"] <= month_to]
    total = len(rows)
    return RunResultsResponse(
        run_id=run_id,
        results=rows[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}/summary", response_model=RunSummaryResponse)
def get_run_summary(run_id: str, db: Session = Depends(get_db)):
    run = _get_run_or_404(run_id, db)
    return RunSummaryResponse(
        run_id=run_id,
        status=run.status,
        summary=compute_summary(db, run_id),
    )
