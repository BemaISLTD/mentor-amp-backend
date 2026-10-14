"""Add audited project archival metadata.

Revision ID: e2b5d8f0c316
Revises: d1a4c7e9b205
Create Date: 2026-09-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2b5d8f0c316"
down_revision: Union[str, None] = "d1a4c7e9b205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("archived_by", sa.String(36), nullable=True))
    op.add_column("projects", sa.Column("archive_reason", sa.Text(), nullable=True))
    op.add_column(
        "projects", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_projects_archived_by_users",
        "projects",
        "users",
        ["archived_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_projects_archived_at", "projects", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_projects_archived_at", table_name="projects")
    op.drop_constraint("fk_projects_archived_by_users", "projects", type_="foreignkey")
    op.drop_column("projects", "archived_at")
    op.drop_column("projects", "archive_reason")
    op.drop_column("projects", "archived_by")
