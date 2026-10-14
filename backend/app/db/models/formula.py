"""Formula registry — catalog of all calculation formulas in the engine."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.types import StringArray


class FormulaRegistry(Base):
    """Every formula MentorAmp knows how to execute."""

    __tablename__ = "formula_registry"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    output_variable: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("variable_registry.name", ondelete="RESTRICT"),
        nullable=False,
    )
    function_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_applicability: Mapped[list] = mapped_column(StringArray, default=list)
    basis_applicability: Mapped[list] = mapped_column(StringArray, default=list)
    version: Mapped[str] = mapped_column(String(20), default="v1")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # --- M1: ownership and display metadata ---
    model_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("formula_groups.id", ondelete="SET NULL"), nullable=True
    )
    expression_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    illustrative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationship to dependencies
    dependencies = relationship(
        "FormulaDependency",
        backref="formula",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
