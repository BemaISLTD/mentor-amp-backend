"""Project archive lifecycle shared by project-owned mutation paths."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models.project import Project


def get_project_or_404(
    db: Session,
    project_id: str,
    *,
    include_archived: bool = False,
) -> Project:
    query = db.query(Project).filter(Project.id == project_id)
    if not include_archived:
        query = query.filter(Project.archived_at.is_(None))
    project = query.first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with id '{project_id}' not found.",
        )
    return project


def require_active_project(db: Session, project_id: str) -> Project:
    project = get_project_or_404(db, project_id, include_archived=True)
    if project.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archived projects are retained as read-only records.",
        )
    return project
