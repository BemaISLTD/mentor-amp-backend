"""Administrator-only audit-log viewer."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.database import get_db
from app.db.models.audit_log import AuditLog
from app.db.models.user import User
from app.models.audit import AuditLogListResponse, AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("/", response_model=AuditLogListResponse)
def list_audit_logs(
    current_admin: Annotated[User, Depends(require_roles("admin"))],
    entity_type: str | None = Query(None, max_length=100),
    entity_id: str | None = Query(None, max_length=100),
    actor_user_id: str | None = Query(None, max_length=36),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    del current_admin
    query = db.query(AuditLog)
    if entity_type is not None:
        query = query.filter(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(AuditLog.entity_id == entity_id)
    if actor_user_id is not None:
        query = query.filter(AuditLog.actor_user_id == actor_user_id)

    total = query.count()
    entries = (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AuditLogListResponse(
        entries=[AuditLogResponse.model_validate(entry) for entry in entries],
        total=total,
        limit=limit,
        offset=offset,
    )
