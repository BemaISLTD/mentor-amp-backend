"""Model-version-scoped variable definitions.

Three layers, kept separate:

- ``variable_registry`` — the *semantic* variable catalog (identity: the name, what it means).
  Global and administrator-managed; never used to resolve values.
- ``model_variable_definitions`` (this table) — how one model version resolves one semantic
  variable: source rule, type, unit, default, whether scenarios may override it. Owned by the
  model version (so by its project); UNIQUE (model_version_id, variable_name).
- the run package's variable snapshot — the immutable copy a submitted run executes.

Resolution for a model version reads only its own definitions, so a change in one project can
never alter what another project's future runs freeze.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModelVariableDefinition(Base):
    __tablename__ = "model_variable_definitions"
    __table_args__ = (
        UniqueConstraint("model_version_id", "variable_name", name="uq_model_variable_definitions_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The semantic identity (catalog entry) this definition resolves.
    variable_name: Mapped[str] = mapped_column(
        String(255), ForeignKey("variable_registry.name", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # input | context | assumption | factor | manual | prior_output | formula | output
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    # The full resolution rule (contract §F.3), e.g. {"type": "assumption", "table": ..., ...}.
    source: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Scenario governance: a scenario may override this variable only if explicitly allowed.
    allow_scenario_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
