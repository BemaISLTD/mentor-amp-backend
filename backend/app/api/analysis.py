"""Results, aggregates, export, trace, comparisons and dashboard (contract §E.1, §E.8, §E.10)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import require_permissions
from app.db.database import get_db
from app.db.models.user import User
from app.services import results_service, trace_service

router = APIRouter(tags=["results"])
Reader = Annotated[User, Depends(require_permissions("projects:read"))]
GroupBy = Literal["projection_year", "projection_month"]


@router.get("/projects/{project_id}/dashboard")
def dashboard(project_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return results_service.dashboard(db, project_id)


@router.get("/runs/{run_id}/summary")
def run_summary(run_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return results_service.summary(db, run_id)


@router.get("/runs/{run_id}/results")
def run_results(
    run_id: str,
    user: Reader,
    policy_id: str | None = Query(None),
    variable: str | None = Query(None),
    month: int | None = Query(None, ge=0),
    month_from: int | None = Query(None, ge=0),
    month_to: int | None = Query(None, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    del user
    return results_service.result_rows(
        db, run_id, policy_id=policy_id, variable=variable, month=month,
        month_from=month_from, month_to=month_to, limit=limit, offset=offset,
    )


@router.get("/runs/{run_id}/aggregates")
def run_aggregates(
    run_id: str,
    user: Reader,
    group_by: GroupBy = Query("projection_year"),
    variables: str | None = Query(None, description="Comma-separated variable names"),
    policy_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    del user
    return results_service.aggregates(db, run_id, group_by=group_by, variables=variables, policy_id=policy_id)


@router.get("/runs/{run_id}/export.csv")
def run_export(
    run_id: str,
    user: Reader,
    group_by: GroupBy = Query("projection_year"),
    variables: str | None = Query(None),
    db: Session = Depends(get_db),
):
    del user
    body, filename = results_service.export_csv(db, run_id, group_by=group_by, variables=variables)
    run_summary = results_service.summary(db, run_id)
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-MentorAmp-Run-Id": run_id,
            "X-MentorAmp-Illustrative": "true" if run_summary.get("illustrative") else "false",
        },
    )


@router.get("/runs/{run_id}/trace/policies")
def trace_policies(run_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return trace_service.traced_policies(db, run_id)


@router.get("/runs/{run_id}/trace/dependents")
def trace_dependents(
    run_id: str,
    user: Reader,
    policy_id: str = Query(...),
    month: int = Query(..., ge=0),
    variable: str = Query(...),
    db: Session = Depends(get_db),
):
    del user
    return trace_service.dependents(db, run_id, policy_id, month, variable)


@router.get("/runs/{run_id}/trace")
def trace(
    run_id: str,
    user: Reader,
    policy_id: str = Query(...),
    month: int = Query(..., ge=0),
    variable: str = Query(...),
    depth: int = Query(4, ge=0, le=10),
    db: Session = Depends(get_db),
):
    del user
    return trace_service.trace_tree(db, run_id, policy_id, month, variable, depth)


@router.get("/comparisons")
def compare_runs(
    user: Reader,
    baseline_run_id: str = Query(...),
    current_run_id: str = Query(...),
    variables: str | None = Query(None),
    group_by: GroupBy = Query("projection_year"),
    db: Session = Depends(get_db),
):
    del user
    return results_service.compare_runs(db, baseline_run_id, current_run_id, variables, group_by)
