"""add run manifests and artifact metadata

Revision ID: 91b4e26d7fa0
Revises: 7c3f19ad0e82
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "91b4e26d7fa0"
down_revision: Union[str, None] = "7c3f19ad0e82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_manifests",
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("artifact_type", sa.String(30), nullable=False),
        sa.Column("storage_uri", sa.String(1000), nullable=False),
        sa.Column("storage_backend", sa.String(30), nullable=False),
        sa.Column("file_format", sa.String(20), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("partition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_uri", name="uq_run_artifacts_storage_uri"),
    )
    op.create_index(
        "idx_run_artifacts_run_type",
        "run_artifacts",
        ["run_id", "artifact_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_run_artifacts_run_type", table_name="run_artifacts")
    op.drop_table("run_artifacts")
    op.drop_table("run_manifests")
