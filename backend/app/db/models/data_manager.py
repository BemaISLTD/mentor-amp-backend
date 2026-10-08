"""Persistent Data Manager sessions, mappings, validation evidence, and import logs."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.types import BigIntegerPK


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ImportSession(Base):
    __tablename__ = "import_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    replaces_dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open", nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_storage_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    detected_types: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    mapping_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    mapping_profile_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("mapping_profiles.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    committed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dataset_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class MappingProfile(Base):
    __tablename__ = "mapping_profiles"
    __table_args__ = (
        UniqueConstraint("project_id", "category", "name", "version_number",
                         name="uq_mapping_profile_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_profile_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("mapping_profiles.id", ondelete="RESTRICT"), nullable=True
    )
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    import_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("import_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    canonical_storage_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    canonical_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint_scheme: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("validation_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class RejectedRecord(Base):
    __tablename__ = "rejected_records"
    __table_args__ = (
        UniqueConstraint("validation_run_id", "row_number", name="uq_rejected_record_row"),
    )

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("validation_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ImportSessionEvent(Base):
    __tablename__ = "import_session_events"

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)
    import_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("import_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
