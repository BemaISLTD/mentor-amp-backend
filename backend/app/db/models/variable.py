"""Variable registry — the central catalog of all named data in the engine."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class VariableRegistry(Base):
    """Every variable the MentorAmp engine knows about and how to resolve it."""

    __tablename__ = "variable_registry"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # number | string | boolean | date | vector | table
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # input | assumption | factor | scenario | formula | prior_output | manual
    source_table: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lookup_keys: Mapped[list] = mapped_column(JSON, default=list)
    default_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    product_applicability: Mapped[list] = mapped_column(ARRAY(String), default=list)
    basis_applicability: Mapped[list] = mapped_column(ARRAY(String), default=list)
    version: Mapped[str] = mapped_column(String(20), default="v1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
