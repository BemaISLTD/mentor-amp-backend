"""Widen run-attempt cleanup status for retained evidence.

Revision ID: d4f7a9c2e681
Revises: ab6d9e2f4013
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f7a9c2e681"
down_revision: Union[str, Sequence[str], None] = "ab6d9e2f4013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "run_attempts",
        "cleanup_status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "run_attempts",
        "cleanup_status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
