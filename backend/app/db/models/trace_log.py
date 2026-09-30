"""Trace logs — records every variable resolution for debugging and traceability."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.types import BigIntegerPK


class TraceLog(Base):
    """A single variable resolution event — what was fetched, from where, with what keys."""

    __tablename__ = "trace_logs"

    id: Mapped[int] = mapped_column(BigIntegerPK, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    scenario_id: Mapped[str] = mapped_column(String(100), nullable=False)
    projection_month: Mapped[int] = mapped_column(Integer, nullable=False)
    # The run attempt that wrote this row; only runs.accepted_attempt_number is canonical.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # The run package's formula ID, kept as plain data (no foreign key): trace rows are
    # historical evidence and must not change when a formula is later edited or deleted.
    formula_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
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
