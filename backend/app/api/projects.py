from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.core.audit import record_audit
from app.core.project_lifecycle import get_project_or_404, require_active_project
from app.db.database import get_db
from app.db.models.project import Project
from app.api.dependencies import authorize_path
from app.db.models.project_member import OWNER, ProjectMember
from app.db.models.user import User
from app.models.schemas import (
    ProjectCreate,
    ProjectArchive,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.services import access
from app.services.common import ServiceError, iso, not_found

router = APIRouter(prefix="/projects", tags=["projects"])
Creator = Annotated[User, Depends(require_permissions("projects:write"))]
Lister = Annotated[User, Depends(require_permissions("projects:read"))]
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
ProjectWriter = Annotated[
    User, Depends(authorize_path("project", "project_id", "projects:write", write=True))
]


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
    db.add(ProjectMember(project_id=project.id, user_id=current_user.id, role=OWNER, created_by=current_user.id))
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


class MemberCreate(BaseModel):
    user_id: str
    role: Literal["owner", "editor", "viewer"] = "viewer"


@router.get(
    "/",
    response_model=ProjectListResponse,
    dependencies=[Depends(require_permissions("projects:read"))],
)
def list_projects(
    user: Lister,
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    """List all projects."""
    query = db.query(Project)
    if not include_archived:
        query = query.filter(Project.archived_at.is_(None))
    allowed = access.accessible_project_ids(db, user)
    if allowed is not None:
        query = query.filter(Project.id.in_(allowed or [""]))
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
    user: ProjectReader,
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Get a single project by ID."""
    return get_project_or_404(db, project_id, include_archived=include_archived)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    user: ProjectWriter,
    db: Session = Depends(get_db),
):
    """Update one or more editable project fields."""
    project = require_active_project(db, project_id)
    before_state = _project_state(project)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_by = user.id
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
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
    user: ProjectWriter,
    db: Session = Depends(get_db),
):
    """Archive a project without deleting its governed records."""
    project = require_active_project(db, project_id)
    before_state = _project_state(project)
    project.archived_at = datetime.now(timezone.utc)
    project.archived_by = user.id
    project.archive_reason = payload.reason
    project.updated_by = user.id
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="project.archived",
        entity_type="project",
        entity_id=project.id,
        before_state=before_state,
        after_state=_project_state(project),
    )
    db.commit()
    db.refresh(project)
    return project


def _member_view(member: ProjectMember, user: User | None) -> dict:
    return {
        "user_id": member.user_id,
        "email": user.email if user else None,
        "full_name": user.full_name if user else None,
        "role": member.role,
        "created_at": iso(member.created_at),
    }


@router.get("/{project_id}/members")
def list_members(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    members = db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()
    return {
        "members": [_member_view(member, db.get(User, member.user_id)) for member in members],
        "total": len(members),
    }


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(project_id: str, payload: MemberCreate, user: ProjectWriter, db: Session = Depends(get_db)):
    """Grant a user access to the project (platform administrators and project owners only)."""
    require_active_project(db, project_id)
    if not access.is_platform_admin(user):
        role = (
            db.query(ProjectMember.role)
            .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
            .scalar()
        )
        if role != OWNER:
            raise ServiceError(403, "FORBIDDEN", "Only project owners and administrators manage members.")
    member_user = db.get(User, payload.user_id)
    if member_user is None:
        raise not_found(f"User '{payload.user_id}' not found.")
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == payload.user_id)
        .first()
    )
    before_state = {"user_id": member.user_id, "role": member.role} if member else None
    if member is None:
        member = ProjectMember(
            project_id=project_id, user_id=payload.user_id, role=payload.role, created_by=user.id,
        )
        db.add(member)
    else:
        member.role = payload.role
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="project_member.granted" if before_state is None else "project_member.updated",
        entity_type="project_member",
        entity_id=member.id,
        before_state=before_state,
        after_state={"project_id": project_id, "user_id": member.user_id, "role": member.role},
    )
    db.commit()
    db.refresh(member)
    return _member_view(member, member_user)
