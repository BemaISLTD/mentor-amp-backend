"""Immutable records of security- and metadata-relevant mutations."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")
AUDIT_ID = BigInteger().with_variant(Integer, "sqlite")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_logs_entity", "entity_type", "entity_id"),
        Index("idx_audit_logs_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(AUDIT_ID, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_VALUE, nullable=True
    )
    after_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_VALUE, nullable=True
    )
    context: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON_VALUE, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
