from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.core.audit import record_audit
from app.core.project_lifecycle import get_project_or_404, require_active_project
from app.db.database import get_db
from app.db.models.project import Project
from app.db.models.user import User
from app.models.schemas import (
    ProjectCreate,
    ProjectArchive,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_state(project: Project) -> dict[str, str | None]:
    return {
        "name": project.name,
        "description": project.description,
        "created_by": project.created_by,
        "updated_by": project.updated_by,
        "archived_by": project.archived_by,
        "archive_reason": project.archive_reason,
        "archived_at": project.archived_at.isoformat() if project.archived_at else None,
    }


@router.post(
    "/",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("projects:write"))],
)
def create_project(
    payload: ProjectCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Create a new project."""
    project = Project(
        name=payload.name,
        description=payload.description,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(project)
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="project.created",
        entity_type="project",
        entity_id=project.id,
        after_state=_project_state(project),
    )
    db.commit()
    db.refresh(project)
    return project


@router.get(
    "/",
    response_model=ProjectListResponse,
    dependencies=[Depends(require_permissions("projects:read"))],
)
def list_projects(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    """List all projects."""
    query = db.query(Project)
    if not include_archived:
        query = query.filter(Project.archived_at.is_(None))
    projects = query.order_by(Project.created_at.desc()).all()
    return ProjectListResponse(
        projects=[ProjectResponse.model_validate(p) for p in projects],
        total=len(projects),
    )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(require_permissions("projects:read"))],
)
def get_project(
    project_id: str,
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Get a single project by ID."""
    return get_project_or_404(db, project_id, include_archived=include_archived)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(require_permissions("projects:write"))],
)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Update one or more editable project fields."""
    project = require_active_project(db, project_id)
    before_state = _project_state(project)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_by = current_user.id
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="project.updated",
        entity_type="project",
        entity_id=project.id,
        before_state=before_state,
        after_state=_project_state(project),
    )
    db.commit()
    db.refresh(project)
    return project


@router.post(
    "/{project_id}/archive",
    response_model=ProjectResponse,
    dependencies=[Depends(require_permissions("projects:write"))],
)
def archive_project(
    project_id: str,
    payload: ProjectArchive,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Archive a project without deleting its governed records."""
    project = require_active_project(db, project_id)
    before_state = _project_state(project)
    project.archived_at = datetime.now(timezone.utc)
    project.archived_by = current_user.id
    project.archive_reason = payload.reason
    project.updated_by = current_user.id
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="project.archived",
        entity_type="project",
        entity_id=project.id,
        before_state=before_state,
        after_state=_project_state(project),
    )
    db.commit()
    db.refresh(project)
    return project
