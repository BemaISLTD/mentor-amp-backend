from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path, require_permissions
from app.db.database import get_db
from app.db.models.project import Project
from app.db.models.project_member import OWNER, ProjectMember
from app.db.models.user import User
from app.models.schemas import (
    ProjectCreate,
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


class MemberCreate(BaseModel):
    user_id: str
    role: Literal["owner", "editor", "viewer"] = "viewer"


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, user: Creator, db: Session = Depends(get_db)):
    """Create a new project; the creator becomes its owner."""
    project = Project(name=payload.name, description=payload.description)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=OWNER, created_by=user.id))
    db.commit()
    db.refresh(project)
    return project


@router.get("/", response_model=ProjectListResponse)
def list_projects(user: Lister, db: Session = Depends(get_db)):
    """List the projects the caller may access (administrators see all)."""
    query = db.query(Project)
    allowed = access.accessible_project_ids(db, user)
    if allowed is not None:
        query = query.filter(Project.id.in_(allowed or [""]))
    projects = query.order_by(Project.created_at.desc()).all()
    return ProjectListResponse(
        projects=[ProjectResponse.model_validate(p) for p in projects],
        total=len(projects),
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    """Get a single project by ID."""
    del user
    return db.get(Project, project_id)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    user: ProjectWriter,
    db: Session = Depends(get_db),
):
    """Update one or more editable project fields."""
    del user
    project = db.get(Project, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
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
def add_member(project_id: str, payload: MemberCreate, user: ProjectReader, db: Session = Depends(get_db)):
    """Grant a user access to the project (platform administrators and project owners only)."""
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
    if member is None:
        member = ProjectMember(
            project_id=project_id, user_id=payload.user_id, role=payload.role, created_by=user.id,
        )
        db.add(member)
    else:
        member.role = payload.role
    db.commit()
    db.refresh(member)
    return _member_view(member, member_user)
