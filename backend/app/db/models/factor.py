"""Tables for factor sets and their data tables."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class FactorSet(Base):
    """A named collection of factor tables (e.g., 'Option Budget 2024', 'Cap Table v2')."""

    __tablename__ = "factor_sets"

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


class FactorTable(Base):
    """A single factor table (rows of lookup data for caps, participation rates, etc.)."""

    __tablename__ = "factor_tables"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("factor_sets.id", ondelete="CASCADE"), nullable=False
    )
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_type: Mapped[str] = mapped_column(String(100), nullable=False)  # option_budget, cap, participation, spread
    lookup_keys: Mapped[list] = mapped_column(JSON, default=list)  # e.g., ["strategy", "duration", "valuation_date"]
    data: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
