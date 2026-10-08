"""Tables for scenario sets and their override data."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core import lifecycle
from app.db.database import Base


class ScenarioSet(Base):
    """A named collection of scenarios (e.g., 'Interest Rate Shocks', 'Market Crash Scenarios')."""

    __tablename__ = "scenario_sets"

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


class ScenarioTable(Base):
    """Override data for a single scenario (e.g., 'High Interest', 'Expense Shock')."""

    __tablename__ = "scenario_tables"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_sets.id", ondelete="CASCADE"), nullable=False
    )
    scenario_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    overrides: Mapped[list] = mapped_column(JSON, default=list)
    # Each override: {"target_variable": "...", "operation": "set|add|multiply|...", "value": ..., "applies_from_period": ..., "applies_to_period": ...}
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # --- M1: lifecycle, provenance and scenario description ---
    # app.core.lifecycle: "validated" only after a validation actually ran and passed.
    status: Mapped[str] = mapped_column(String(30), default=lifecycle.DRAFT, nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenario_type: Mapped[str] = mapped_column(String(30), default="deterministic", nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    path_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    version_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_table_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scenario_tables.id", ondelete="RESTRICT"), nullable=True
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
