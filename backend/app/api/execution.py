"""Run Sets and runs: preflight, submit, monitor, events, attempts, package, manifest (contract §E.7)."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path, require_permissions
from app.db.database import get_db
from app.db.models.user import User
from app.services import run_execution_service, run_query_service, run_submission_service

router = APIRouter(tags=["runs"])
Reader = Annotated[User, Depends(require_permissions("projects:read"))]
Executor = Annotated[User, Depends(require_permissions("runs:execute"))]
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
RunSetReader = Annotated[User, Depends(authorize_path("run_set", "run_set_id", "projects:read"))]
RunReader = Annotated[User, Depends(authorize_path("run", "run_id", "projects:read"))]


class RunSetRequest(BaseModel):
    project_id: str
    name: str = Field("Run Set", min_length=1, max_length=255)
    projection_set_ids: list[str] = Field(min_length=1)
    scenario_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


@router.get("/projects/{project_id}/run-sets")
def list_run_sets(
    project_id: str,
    user: ProjectReader,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    del user
    return run_query_service.list_run_sets(db, project_id, limit, offset)


@router.post("/run-sets/preflight")
def preflight(payload: RunSetRequest, user: Executor, db: Session = Depends(get_db)):
    return run_submission_service.preflight(db, payload.model_dump(), user)


@router.post("/run-sets", status_code=status.HTTP_202_ACCEPTED)
def submit_run_set(
    payload: RunSetRequest,
    background_tasks: BackgroundTasks,
    user: Executor,
    db: Session = Depends(get_db),
):
    result = run_submission_service.submit_run_set(db, payload.model_dump(), user)
    background_tasks.add_task(run_execution_service.execute_run_set, result["run_set"]["id"])
    return result


@router.get("/run-sets/{run_set_id}")
def get_run_set(run_set_id: str, user: RunSetReader, db: Session = Depends(get_db)):
    del user
    return run_query_service.get_run_set(db, run_set_id)


@router.get("/runs")
def list_runs(
    user: Reader,
    project_id: str | None = Query(None),
    run_set_id: str | None = Query(None),
    model_version_id: str | None = Query(None),
    projection_set_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return run_query_service.list_runs(
        db, user, project_id=project_id, run_set_id=run_set_id, model_version_id=model_version_id,
        projection_set_id=projection_set_id, status=status_filter, limit=limit, offset=offset,
    )


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: RunReader, db: Session = Depends(get_db)):
    del user
    return run_query_service.run_view(db, run_query_service.get_run(db, run_id))


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: str,
    user: RunReader,
    after_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    del user
    return run_query_service.list_events(db, run_id, after_id, limit)


@router.get("/runs/{run_id}/attempts")
def run_attempts(run_id: str, user: RunReader, db: Session = Depends(get_db)):
    del user
    return run_query_service.list_attempts(db, run_id)


@router.get("/runs/{run_id}/package")
def run_package(run_id: str, user: RunReader, db: Session = Depends(get_db)):
    del user
    return run_query_service.get_package(db, run_id)


@router.get("/runs/{run_id}/manifest")
def run_manifest(run_id: str, user: RunReader, db: Session = Depends(get_db)):
    del user
    return run_query_service.get_manifest(db, run_id)
