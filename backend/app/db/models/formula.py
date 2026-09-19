"""Formula registry — catalog of all calculation formulas in the engine."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


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
    product_applicability: Mapped[list] = mapped_column(ARRAY(String), default=list)
    basis_applicability: Mapped[list] = mapped_column(ARRAY(String), default=list)
    version: Mapped[str] = mapped_column(String(20), default="v1")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationship to dependencies
    dependencies = relationship(
        "FormulaDependency",
        backref="formula",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
