"""Projection run tracking — status, timing, and configuration."""

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.execution.run_state import PENDING, RUN_STATUSES, sql_in_list
from app.db.database import Base


class Run(Base):
    """A single projection run execution record."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({sql_in_list(RUN_STATUSES)})", name="ck_runs_status"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    projection_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # identifier for the run configuration
    # See app.core.execution.run_state for the legal statuses and transitions.
    status: Mapped[str] = mapped_column(String(20), default=PENDING)
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
    illustrative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # --- Work Package 1: immutable inputs, attempts and the final manifest ---
    # Fingerprint of the frozen run package (run_packages.fingerprint). NULL only for legacy M1
    # runs submitted before run packages existed.
    run_package_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The attempt whose result rows are canonical; NULL until an attempt finishes with results.
    accepted_attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # run_manifests.fingerprint, set once when the run reaches a terminal status.
    final_manifest_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- M2: cooperative cancellation and immutable retry lineage ---
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    retried_from_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
