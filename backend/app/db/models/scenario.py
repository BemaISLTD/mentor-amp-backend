"""Tables for scenario sets and their override data."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ScenarioSet(Base):
    """A named collection of scenarios (e.g., 'Interest Rate Shocks', 'Market Crash Scenarios')."""

    __tablename__ = "scenario_sets"

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


class ScenarioTable(Base):
    """Override data for a single scenario (e.g., 'High Interest', 'Expense Shock')."""

    __tablename__ = "scenario_tables"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario_sets.id", ondelete="CASCADE"), nullable=False
    )
    scenario_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    overrides: Mapped[list] = mapped_column(JSON, default=list)
    # Each override: {"target_variable": "...", "operation": "set|add|multiply|...", "value": ..., "applies_from_period": ..., "applies_to_period": ...}
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
