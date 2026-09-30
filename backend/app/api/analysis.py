"""Results, aggregates, export, trace, comparisons and dashboard (contract §E.1, §E.8, §E.10).

Results exist only for ``success`` runs; ``partial_success`` needs ``include_partial=true``
(see ``app.services.results_service``).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path, require_permissions
from app.db.database import get_db
from app.db.models.user import User
from app.services import results_service, trace_service

router = APIRouter(tags=["results"])
Reader = Annotated[User, Depends(require_permissions("projects:read"))]
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
RunReader = Annotated[User, Depends(authorize_path("run", "run_id", "projects:read"))]
GroupBy = Literal["projection_year", "projection_month"]
IncludePartial = Query(
    False, description="Read results of a partial_success run (incomplete: some policies failed)."
)


@router.get("/projects/{project_id}/dashboard")
def dashboard(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    return results_service.dashboard(db, project_id)


@router.get("/runs/{run_id}/summary")
def run_summary(run_id: str, user: RunReader, db: Session = Depends(get_db)):
    del user
    return results_service.summary(db, run_id)


@router.get("/runs/{run_id}/results")
def run_results(
    run_id: str,
    user: RunReader,
    policy_id: str | None = Query(None),
    variable: str | None = Query(None),
    month: int | None = Query(None, ge=0),
    month_from: int | None = Query(None, ge=0),
    month_to: int | None = Query(None, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    include_partial: bool = IncludePartial,
    db: Session = Depends(get_db),
):
    del user
    return results_service.result_rows(
        db, run_id, policy_id=policy_id, variable=variable, month=month,
        month_from=month_from, month_to=month_to, limit=limit, offset=offset,
        include_partial=include_partial,
    )


@router.get("/runs/{run_id}/aggregates")
def run_aggregates(
    run_id: str,
    user: RunReader,
    group_by: GroupBy = Query("projection_year"),
    variables: str | None = Query(None, description="Comma-separated variable names"),
    policy_id: str | None = Query(None),
    include_partial: bool = IncludePartial,
    db: Session = Depends(get_db),
):
    del user
    return results_service.aggregates(
        db, run_id, group_by=group_by, variables=variables, policy_id=policy_id,
        include_partial=include_partial,
    )


@router.get("/runs/{run_id}/export.csv")
def run_export(
    run_id: str,
    user: RunReader,
    group_by: GroupBy = Query("projection_year"),
    variables: str | None = Query(None),
    include_partial: bool = IncludePartial,
    db: Session = Depends(get_db),
):
    del user
    body, filename, data = results_service.export_csv(
        db, run_id, group_by=group_by, variables=variables, include_partial=include_partial,
    )
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-MentorAmp-Run-Id": run_id,
            "X-MentorAmp-Illustrative": "true" if data.get("illustrative") else "false",
            "X-MentorAmp-Complete": "true" if data.get("complete") else "false",
        },
    )


@router.get("/runs/{run_id}/trace/policies")
def trace_policies(
    run_id: str, user: RunReader, include_partial: bool = IncludePartial, db: Session = Depends(get_db),
):
    del user
    return trace_service.traced_policies(db, run_id, include_partial)


@router.get("/runs/{run_id}/trace/dependents")
def trace_dependents(
    run_id: str,
    user: RunReader,
    policy_id: str = Query(...),
    month: int = Query(..., ge=0),
    variable: str = Query(...),
    include_partial: bool = IncludePartial,
    db: Session = Depends(get_db),
):
    del user
    return trace_service.dependents(db, run_id, policy_id, month, variable, include_partial)


@router.get("/runs/{run_id}/trace")
def trace(
    run_id: str,
    user: RunReader,
    policy_id: str = Query(...),
    month: int = Query(..., ge=0),
    variable: str = Query(...),
    depth: int = Query(4, ge=0, le=10),
    include_partial: bool = IncludePartial,
    db: Session = Depends(get_db),
):
    del user
    return trace_service.trace_tree(db, run_id, policy_id, month, variable, depth, include_partial)


@router.get("/comparisons")
def compare_runs(
    user: Reader,
    baseline_run_id: str = Query(...),
    current_run_id: str = Query(...),
    variables: str | None = Query(None),
    group_by: GroupBy = Query("projection_year"),
    include_partial: bool = IncludePartial,
    db: Session = Depends(get_db),
):
    return results_service.compare_runs(
        db, user, baseline_run_id, current_run_id, variables, group_by, include_partial,
    )
