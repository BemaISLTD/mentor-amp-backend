"""Models, model versions, formula groups and published outputs (M1 modelling layer)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Model(Base):
    """A named actuarial model for one product inside a project."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_models_project_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_code: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class ModelVersion(Base):
    """One version of a model: block, profile, basis, methodology and its formulas."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version_label", name="uq_model_versions_label"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_label: Mapped[str] = mapped_column(String(30), nullable=False)
    block_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    basis: Mapped[str] = mapped_column(String(100), nullable=False)
    methodology: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    illustrative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class FormulaGroup(Base):
    """A named bundle of formulas inside a model version, with a lineage state."""

    __tablename__ = "formula_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lineage_state: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    version_label: Mapped[str] = mapped_column(String(30), default="v1", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelPublishedOutput(Base):
    """A variable a model version publishes for results, reports and downstream models."""

    __tablename__ = "model_published_outputs"
    __table_args__ = (
        UniqueConstraint("model_version_id", "variable_name", name="uq_published_outputs_var"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variable_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dimension: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "sum" for flows, "end_of_period" for balances (drives aggregation).
    aggregation: Mapped[str] = mapped_column(String(20), default="sum", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
