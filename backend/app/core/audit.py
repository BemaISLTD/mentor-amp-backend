"""Transactional audit-log writer."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.audit_log import AuditLog


def record_audit(
    db: Session,
    *,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> AuditLog:
    """Stage an audit record in the caller's current transaction."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        context=context,
    )
    db.add(entry)
    db.flush()
    return entry
