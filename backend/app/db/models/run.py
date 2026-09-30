"""Projection run tracking — status, timing, and configuration."""

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Run(Base):
    """A single projection run execution record."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    projection_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # identifier for the run configuration
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | running | success | partial_success | failed | cancelled
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # --- M1: link to configuration, progress, summary and manifest ---
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("run_sets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    projection_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projection_sets.id", ondelete="SET NULL"), nullable=True
    )
    model_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True
    )
    scenario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    valuation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    horizon_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    manifest_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    illustrative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
