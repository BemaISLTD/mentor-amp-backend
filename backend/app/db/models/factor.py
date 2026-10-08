"""Tables for factor sets and their data tables."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core import lifecycle
from app.db.database import Base


class FactorSet(Base):
    """A named collection of factor tables (e.g., 'Option Budget 2024', 'Cap Table v2')."""

    __tablename__ = "factor_sets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FactorTable(Base):
    """A single factor table (rows of lookup data for caps, participation rates, etc.)."""

    __tablename__ = "factor_tables"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("factor_sets.id", ondelete="CASCADE"), nullable=False
    )
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_type: Mapped[str] = mapped_column(String(100), nullable=False)  # option_budget, cap, participation, spread
    lookup_keys: Mapped[list] = mapped_column(JSON, default=list)  # e.g., ["strategy", "duration", "valuation_date"]
    data: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # --- Work Package 1: lifecycle, provenance and the explicit value column (as assumptions) ---
    status: Mapped[str] = mapped_column(String(30), default=lifecycle.UPLOADED, nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_column: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    version_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_table_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("factor_tables.id", ondelete="RESTRICT"), nullable=True
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
