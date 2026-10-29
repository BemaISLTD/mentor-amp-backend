"""Expected-result benchmarks and immutable reconciliation evidence."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExpectedResultSet(Base):
    __tablename__ = "expected_result_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_code: Mapped[str] = mapped_column(String(50), nullable=False)
    version_label: Mapped[str] = mapped_column(String(30), default="v1", nullable=False)
    source_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    timing_convention: Mapped[str | None] = mapped_column(String(100), nullable=True)
    granularity: Mapped[str] = mapped_column(String(30), default="policy_month", nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    columns_detected: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rows_data: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="validated", nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("expected_result_sets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class ReconciliationReport(Base):
    __tablename__ = "reconciliation_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expected_result_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("expected_result_sets.id", ondelete="RESTRICT"), nullable=True
    )
    level: Mapped[str] = mapped_column(String(30), default="policy_month", nullable=False)
    variables_requested: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    expected_result_fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    expected_result_version: Mapped[str] = mapped_column(String(30), default="v1", nullable=False)
    timing_convention_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True)
    granularity_snapshot: Mapped[str] = mapped_column(String(30), default="policy_month", nullable=False)

    policy_id_filter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    abs_tol: Mapped[float] = mapped_column(Float, default=1e-2, nullable=False)
    rel_tol: Mapped[float] = mapped_column(Float, default=1e-4, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    disposition_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    disposition_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disposition_history: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    failures: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
