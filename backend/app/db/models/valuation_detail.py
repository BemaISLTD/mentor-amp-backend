"""Valuation run subordinate metadata and execution parameters.

Subordinate to canonical `runs.id` (M5A invariant: zero parallel run architectures).
"""

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ValuationRunDetail(Base):
    """Subordinate extension metadata for a canonical Run executing a valuation methodology."""

    __tablename__ = "valuation_run_details"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_projection_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    derived_dataset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("derived_datasets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    basis: Mapped[str] = mapped_column(String(50), nullable=False)
    methodology: Mapped[str] = mapped_column(String(50), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(50), nullable=False)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    discount_curve_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    summary_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
