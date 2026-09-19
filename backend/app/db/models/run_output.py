"""Stores all projection output values — the results of every calculation."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class RunOutput(Base):
    """One calculated value for a specific policy/scenario/month/variable combination."""

    __tablename__ = "run_outputs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(100), nullable=False)
    projection_month: Mapped[int] = mapped_column(Integer, nullable=False)
    variable_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    product: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index(
            "idx_run_outputs_lookup",
            "run_id", "policy_id", "scenario_id", "projection_month",
        ),
    )
