"""Trace logs — records every variable resolution for debugging and traceability."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TraceLog(Base):
    """A single variable resolution event — what was fetched, from where, with what keys."""

    __tablename__ = "trace_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(100), nullable=False)
    projection_month: Mapped[int] = mapped_column(Integer, nullable=False)
    formula_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("formula_registry.id", ondelete="SET NULL"), nullable=True
    )
    variable_name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_table: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lookup_keys: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index(
            "idx_trace_logs_lookup",
            "run_id", "policy_id", "projection_month", "variable_name",
        ),
    )
