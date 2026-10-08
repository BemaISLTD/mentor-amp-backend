"""Prior-versus-current projection reconciliation endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.runs import _get_run_or_404
from app.core.output.storage import get_run_results
from app.core.reconciliation import reconcile_results
from app.db.database import get_db
from app.models.reconciliation import ReconciliationResponse

router = APIRouter(prefix="/legacy-runs", tags=["legacy-reconciliation"], deprecated=True)
TERMINAL_STATUSES = {"success", "partial_success", "failed"}


@router.get(
    "/{run_id}/reports/reconciliation",
    response_model=ReconciliationResponse,
)
def get_reconciliation(
    run_id: str,
    baseline_run_id: str = Query(...),
    policy_id: str | None = Query(None),
    scenario_id: str | None = Query(None),
    variable: str | None = Query(None),
    month_from: int | None = Query(None, ge=1),
    month_to: int | None = Query(None, ge=1),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    current_run = _get_run_or_404(run_id, db)
    baseline_run = _get_run_or_404(baseline_run_id, db)
    if current_run.project_id != baseline_run.project_id:
        raise HTTPException(
            status_code=400,
            detail="Runs from different projects cannot be reconciled.",
        )
    for label, run in (("Current", current_run), ("Baseline", baseline_run)):
        if run.status not in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"{label} run '{run.id}' is not complete.",
            )

    def apply_filters(rows):
        if policy_id is not None:
            rows = [item for item in rows if item["policy_id"] == policy_id]
        if scenario_id is not None:
            rows = [item for item in rows if item["scenario_id"] == scenario_id]
        if variable is not None:
            rows = [item for item in rows if item["variable"] == variable]
        if month_from is not None:
            rows = [item for item in rows if item["month"] >= month_from]
        if month_to is not None:
            rows = [item for item in rows if item["month"] <= month_to]
        return rows

    comparison = reconcile_results(
        apply_filters(get_run_results(db, baseline_run_id)),
        apply_filters(get_run_results(db, run_id)),
    )
    details = comparison["details"]
    total = len(details)
    return ReconciliationResponse(
        baseline_run_id=baseline_run_id,
        current_run_id=run_id,
        summary=comparison["summary"],
        bridges=comparison["bridges"],
        details=details[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )
