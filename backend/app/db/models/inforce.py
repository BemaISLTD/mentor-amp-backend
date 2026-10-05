"""Tables for uploaded inforce/policy data."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core import lifecycle
from app.db.database import Base
from app.db.types import BigIntegerPK


class InforceFile(Base):
    """Metadata for each uploaded inforce file."""

    __tablename__ = "inforce_files"

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
