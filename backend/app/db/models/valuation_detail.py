"""Valuation run subordinate metadata and execution parameters.

Subordinate to canonical `runs.id` (M5A invariant: zero parallel run architectures).
Canonical execution identity (project_id, basis, methodology, methodology_version,
source_projection_run_id, derived_dataset_id) is owned strictly by `Run`.
`ValuationRunDetail` contains only subordinate calculation parameters and summary results.
"""

from datetime import date, datetime, timezone
from typing import Any, TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.db.models.run import Run


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ValuationRunDetail(Base):
    """Subordinate extension metadata for a canonical Run executing a valuation methodology."""

    __tablename__ = "valuation_run_details"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True
    )
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    discount_curve_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    summary_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Subordinate relationship to canonical parent Run
    run: Mapped["Run"] = relationship("Run", backref="valuation_detail", lazy="joined")

    # Property proxies ensuring zero drift from canonical Run (Directive 4)
    @property
    def project_id(self) -> str | None:
        return self.run.project_id if self.run else None

    @property
    def basis(self) -> str | None:
        return self.run.basis if self.run else None

    @property
    def methodology(self) -> str | None:
        return self.run.methodology if self.run else None

    @property
    def methodology_version(self) -> str | None:
        return self.run.methodology_version if self.run else None

    @property
    def source_projection_run_id(self) -> str | None:
        return self.run.source_projection_run_id if self.run else None

    @property
    def derived_dataset_id(self) -> str | None:
        return self.run.derived_dataset_id if self.run else None
