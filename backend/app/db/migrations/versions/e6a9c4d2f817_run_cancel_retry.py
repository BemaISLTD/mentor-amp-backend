"""Add cooperative run cancellation and retry lineage.

Revision ID: e6a9c4d2f817
Revises: f3b8d6a1e240
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6a9c4d2f817"
down_revision: Union[str, Sequence[str], None] = "f3b8d6a1e240"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("cancel_requested_by", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_runs_cancel_requested_by", "runs", "users",
        ["cancel_requested_by"], ["id"], ondelete="SET NULL",
    )
    op.add_column("runs", sa.Column("retried_from_run_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_runs_retried_from", "runs", "runs",
        ["retried_from_run_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_runs_retried_from_run_id", "runs", ["retried_from_run_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_retried_from_run_id", table_name="runs")
    op.drop_constraint("fk_runs_retried_from", "runs", type_="foreignkey")
    op.drop_column("runs", "retried_from_run_id")
    op.drop_constraint("fk_runs_cancel_requested_by", "runs", type_="foreignkey")
    op.drop_column("runs", "cancel_requested_by")
    op.drop_column("runs", "cancel_requested_at")
