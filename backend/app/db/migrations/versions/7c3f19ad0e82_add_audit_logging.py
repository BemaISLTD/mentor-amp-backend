"""add audit logging and project actor fields

Revision ID: 7c3f19ad0e82
Revises: 2f6d51e920a4
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "7c3f19ad0e82"
down_revision: Union[str, None] = "2f6d51e920a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("created_by", sa.String(36), nullable=True))
    op.add_column("projects", sa.Column("updated_by", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_projects_created_by_users",
        "projects",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_projects_updated_by_users",
        "projects",
        "users",
        ["updated_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"]
    )
    op.create_index(
        "idx_audit_logs_actor_created",
        "audit_logs",
        ["actor_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_audit_logs_actor_created", table_name="audit_logs")
    op.drop_index("idx_audit_logs_entity", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_constraint("fk_projects_updated_by_users", "projects", type_="foreignkey")
    op.drop_constraint("fk_projects_created_by_users", "projects", type_="foreignkey")
    op.drop_column("projects", "updated_by")
    op.drop_column("projects", "created_by")
