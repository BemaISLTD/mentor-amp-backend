from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.audit import record_audit
from app.db.database import get_db
from app.db.models.project import Project
from app.db.models.user import User
from app.models.schemas import (
    ProjectCreate,
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
    }


def _get_project_or_404(project_id: str, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with id '{project_id}' not found.",
        )
    return project


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
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


@router.get("/", response_model=ProjectListResponse)
def list_projects(db: Session = Depends(get_db)):
    """List all projects."""
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return ProjectListResponse(
        projects=[ProjectResponse.model_validate(p) for p in projects],
        total=len(projects),
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, db: Session = Depends(get_db)):
    """Get a single project by ID."""
    return _get_project_or_404(project_id, db)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Update one or more editable project fields."""
    project = _get_project_or_404(project_id, db)
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


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> Response:
    """Delete a project and its database-cascaded child records."""
    project = _get_project_or_404(project_id, db)
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="project.deleted",
        entity_type="project",
        entity_id=project.id,
        before_state=_project_state(project),
    )
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
