"""Rollforward summaries and mathematical attribution records.

Enforces unbroken accounting continuity:
    Beginning Balance + Sum(Attributed Movements) = Ending Balance
All foreign keys use ON DELETE RESTRICT to guarantee audit retention.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RollforwardSummary(Base):
    """A single balance or movement line item in a governed rollforward schedule."""

    __tablename__ = "rollforward_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cohort_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    methodology: Mapped[str] = mapped_column(String(50), nullable=False)
    basis: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)
    component_name: Mapped[str] = mapped_column(String(100), nullable=False)
    component_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_movement: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    opening_balance: Mapped[float] = mapped_column(Numeric(18, 4), default=0.0, nullable=False)
    movement_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0.0, nullable=False)
    closing_balance: Mapped[float] = mapped_column(Numeric(18, 4), default=0.0, nullable=False)
    is_continuity_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    continuity_difference: Mapped[float] = mapped_column(Numeric(18, 4), default=0.0, nullable=False)
    trace_identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
