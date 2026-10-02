"""Record the actors of registry mutations.

Revision ID: c6e1a4b9d203
Revises: f9d3e5a7b012
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6e1a4b9d203"
down_revision: Union[str, Sequence[str], None] = "f9d3e5a7b012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("variable_registry", sa.Column("created_by", sa.String(36), nullable=True))
    op.add_column("variable_registry", sa.Column("updated_by", sa.String(36), nullable=True))
    op.add_column("formula_registry", sa.Column("updated_by", sa.String(36), nullable=True))
    for table, column in (
        ("variable_registry", "created_by"),
        ("variable_registry", "updated_by"),
        ("formula_registry", "updated_by"),
    ):
        op.create_foreign_key(
            f"fk_{table}_{column}_users", table, "users", [column], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    for table, column in (
        ("formula_registry", "updated_by"),
        ("variable_registry", "updated_by"),
        ("variable_registry", "created_by"),
    ):
        op.drop_constraint(f"fk_{table}_{column}_users", table, type_="foreignkey")
        op.drop_column(table, column)
