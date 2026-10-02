"""Projection Sets, Run Sets and run events (M1 execution layer)."""

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.types import BigIntegerPK

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectionSet(Base):
    """A saved, validated run configuration: model version, inputs, scenarios, horizon, outputs."""

    __tablename__ = "projection_sets"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "name", "version_label", name="uq_projection_sets_name_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_label: Mapped[str] = mapped_column(String(30), default="v1", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    model_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True
    )
    inforce_file_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    assumption_table_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Pinned factor tables (by ID); a run never selects a factor table by name alone.
    factor_table_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    scenario_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_months: Mapped[int] = mapped_column(Integer, nullable=False)
    time_step: Mapped[str] = mapped_column(String(20), default="monthly", nullable=False)
    output_variables: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    trace_scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class RunSet(Base):
    """One submission of Projection Sets x scenarios; creates one run per combination."""

    __tablename__ = "run_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    projection_set_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # The scenarios actually submitted (resolved per Projection Set, first-appearance order).
    scenario_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # How the submission resolved: requested scenario IDs, and per Projection Set the scenario
    # source ("request" or "projection_set"), the scenarios and the runs created.
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunEvent(Base):
    """One entry in a run's log (shown in the execution monitor)."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    level: Mapped[str] = mapped_column(String(10), default="info", nullable=False)
    step: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
