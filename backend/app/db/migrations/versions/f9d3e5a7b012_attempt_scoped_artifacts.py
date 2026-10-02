"""Tag analytical artifacts with their execution attempt.

Revision ID: f9d3e5a7b012
Revises: f8c2d4e6a901
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9d3e5a7b012"
down_revision: Union[str, Sequence[str], None] = "f8c2d4e6a901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("run_artifacts", sa.Column("attempt_number", sa.Integer(), nullable=True))
    op.create_index(
        "idx_run_artifacts_accepted_attempt",
        "run_artifacts",
        ["run_id", "artifact_type", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_index("idx_run_artifacts_accepted_attempt", table_name="run_artifacts")
    op.drop_column("run_artifacts", "attempt_number")
