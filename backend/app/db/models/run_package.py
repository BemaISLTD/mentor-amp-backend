"""Run packages (immutable execution inputs) and run attempts (individual executions)."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.execution.run_state import ATTEMPT_STATUSES, sql_in_list
from app.db.database import Base
from app.db.immutability import forbid_updates


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunPackage(Base):
    """The frozen, fingerprinted inputs of one run (see ``app.core.execution.run_package``).

    Written once at submission; never updated. ``package`` is stored as plain JSON (not JSONB) so
    the document is kept exactly as written.
    """

    __tablename__ = "run_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint_algorithm: Mapped[str] = mapped_column(String(40), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    package: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


forbid_updates(RunPackage)


class RunAttempt(Base):
    """One execution of a run by one worker.

    Result and trace rows carry the attempt number; only the attempt promoted in
    ``runs.accepted_attempt_number`` is ever read by the results APIs. ``lease_expires_at`` is
    reserved for worker leases (not enforced yet).
    """

    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_number", name="uq_run_attempts_run_number"),
        CheckConstraint(f"status IN ({sql_in_list(ATTEMPT_STATUSES)})", name="ck_run_attempts_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trace_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # not_needed | deleted | failed — what happened to the rows of a failed attempt.
    cleanup_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    executed_build: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
