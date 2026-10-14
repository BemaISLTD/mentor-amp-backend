"""Public audit-log response contracts."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditLogResponse(BaseModel):
    id: int
    actor_user_id: str | None = None
    action: str
    entity_type: str
    entity_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    entries: list[AuditLogResponse] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int
