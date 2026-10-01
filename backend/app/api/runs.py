"""Projection run queue, status, and manifest endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.dependencies import require_permissions
from app.core.audit import record_audit
from app.core.project_lifecycle import require_active_project
from app.core.projection_engine.runner import run_projection
from app.core.run_manifest import create_run_manifest
from app.db.database import SessionLocal, get_db
from app.db.models.run import Run
from app.db.models.run_artifact import RunManifest
from app.db.models.user import User
from app.models.runs import RunCreate, RunListResponse, RunManifestResponse, RunResponse
from app.models.schemas import ProjectionRunDefinition

router = APIRouter(prefix="/runs", tags=["runs"])


def _get_run_or_404(run_id: str, db: Session) -> Run:
    run = db.query(Run).filter(Run.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return run


def execute_queued_run(run_definition: ProjectionRunDefinition) -> None:
    """Execute a queued run using a session independent of the HTTP request."""
    db = SessionLocal()
    try:
        run_projection(run_definition, db)
    except Exception:
        db.rollback()
        run = db.query(Run).filter(Run.id == run_definition.id).first()
        if run is not None:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post(
    "/",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permissions("runs:execute"))],
)
def queue_run(
    payload: RunCreate,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    require_active_project(db, payload.project_id)

    run_id = str(uuid.uuid4())
    run_definition = ProjectionRunDefinition(
        id=run_id,
        name=payload.name,
        project_id=payload.project_id,
        formula_database_id=payload.formula_database_id,
        dataset_ids=payload.dataset_ids,
        scenario_ids=payload.scenario_ids,
        projection_length_months=payload.projection_length_months,
        selected_output_variables=payload.selected_output_variables,
        debug_mode=payload.debug_mode,
    )
    run = Run(
        id=run_id,
        project_id=payload.project_id,
        projection_key=payload.formula_database_id,
        status="pending",
    )
    db.add(run)
    db.flush()
    create_run_manifest(db, run_definition)
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="run.queued",
        entity_type="run",
        entity_id=run.id,
        after_state={
            "project_id": run.project_id,
            "status": run.status,
            "projection_key": run.projection_key,
        },
    )
    db.commit()
    db.refresh(run)
    background_tasks.add_task(execute_queued_run, run_definition)
    return run


@router.get("/", response_model=RunListResponse)
def list_runs(
    project_id: str | None = Query(None),
    run_status: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Run)
    if project_id is not None:
        query = query.filter(Run.project_id == project_id)
    if run_status is not None:
        query = query.filter(Run.status == run_status)
    total = query.count()
    runs = query.order_by(Run.created_at.desc()).offset(offset).limit(limit).all()
    return RunListResponse(runs=runs, total=total, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: str, db: Session = Depends(get_db)):
    return _get_run_or_404(run_id, db)


@router.get("/{run_id}/manifest", response_model=RunManifestResponse)
def get_run_manifest(run_id: str, db: Session = Depends(get_db)):
    _get_run_or_404(run_id, db)
    manifest = db.query(RunManifest).filter(RunManifest.run_id == run_id).first()
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Manifest for run '{run_id}' not found.")
    return manifest
