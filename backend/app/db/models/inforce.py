"""Tables for uploaded inforce/policy data."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core import lifecycle
from app.db.database import Base
from app.db.immutability import ImmutableRecordError, protect_after_commit
from app.db.types import BigIntegerPK


class InforceFile(Base):
    """Metadata for each uploaded inforce file."""

    __tablename__ = "inforce_files"
    __table_args__ = (
        UniqueConstraint(
            "version_group_id", "version_number",
            name="uq_inforce_files_version_group_number",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # tsv, csv, xlsx
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    columns_detected: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # --- M1: lifecycle and provenance ---
    # app.core.lifecycle: "validated" only after a validation actually ran and passed.
    status: Mapped[str] = mapped_column(String(30), default=lifecycle.UPLOADED, nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # How ``fingerprint`` was computed (app.core.execution.fingerprints); runs require inforce-v2.
    fingerprint_scheme: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    version_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inforce_files.id", ondelete="RESTRICT"), nullable=True
    )
    import_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("import_sessions.id", ondelete="RESTRICT"), nullable=True
    )
    raw_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mapping_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("validation_runs.id", ondelete="RESTRICT"), nullable=True
    )
    committed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class InforceRecord(Base):
    """Individual policy record from an inforce file."""

    __tablename__ = "inforce_records"

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inforce_files.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


GOVERNANCE_COLUMNS = frozenset({
    "status", "approved_by", "approved_at", "rejected_by", "rejected_at",
    "review_reason",
})
protect_after_commit(InforceFile, mutable_columns=GOVERNANCE_COLUMNS)


def _protect_committed_record(connection, target: InforceRecord) -> None:
    committed_at = connection.execute(
        select(InforceFile.committed_at).where(InforceFile.id == target.file_id)
    ).scalar_one_or_none()
    if committed_at is not None:
        raise ImmutableRecordError(
            "InforceRecord committed content is immutable."
        )


@event.listens_for(InforceRecord, "before_update")
def _protect_record_update(mapper, connection, target) -> None:  # noqa: ARG001
    _protect_committed_record(connection, target)


@event.listens_for(InforceRecord, "before_delete")
def _protect_record_delete(mapper, connection, target) -> None:  # noqa: ARG001
    _protect_committed_record(connection, target)
