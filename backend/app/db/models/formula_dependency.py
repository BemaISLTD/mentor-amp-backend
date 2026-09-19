"""Links formulas to their dependent variables — powers the dependency engine."""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class FormulaDependency(Base):
    """Maps a formula to one variable it depends on."""

    __tablename__ = "formula_dependencies"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    formula_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("formula_registry.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_variable: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("variable_registry.name", ondelete="CASCADE"),
        nullable=False,
    )
