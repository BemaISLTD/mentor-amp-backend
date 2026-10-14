"""Remove legacy PostgreSQL run output and trace storage.

Revision ID: a7f4c2d9e180
Revises: e2b5d8f0c316
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7f4c2d9e180"
down_revision: Union[str, None] = "e2b5d8f0c316"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _require_empty(table_name: str) -> None:
    row_count = op.get_bind().execute(
        sa.text(f'SELECT COUNT(*) FROM "{table_name}"')
    ).scalar_one()
    if row_count:
        raise RuntimeError(
            f"Cannot remove non-empty legacy table '{table_name}'. "
            "Export its records to analytical artifact storage first."
        )


def upgrade() -> None:
    _require_empty("run_outputs")
    _require_empty("trace_logs")
    op.drop_table("trace_logs")
    op.drop_table("run_outputs")


def downgrade() -> None:
    op.create_table(
        "run_outputs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("scenario_id", sa.String(length=100), nullable=False),
        sa.Column("projection_month", sa.Integer(), nullable=False),
        sa.Column("variable_name", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("product", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_outputs_run_id", "run_outputs", ["run_id"])
    op.create_index(
        "idx_run_outputs_lookup",
        "run_outputs",
        ["run_id", "policy_id", "scenario_id", "projection_month"],
    )

    op.create_table(
        "trace_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("scenario_id", sa.String(length=100), nullable=False),
        sa.Column("projection_month", sa.Integer(), nullable=False),
        sa.Column("formula_id", sa.String(length=36), nullable=True),
        sa.Column("variable_name", sa.String(length=255), nullable=False),
        sa.Column("input_values", sa.JSON(), nullable=True),
        sa.Column("output_value", sa.JSON(), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("source_table", sa.String(length=255), nullable=True),
        sa.Column("lookup_keys", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["formula_id"], ["formula_registry.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trace_logs_run_id", "trace_logs", ["run_id"])
    op.create_index(
        "idx_trace_logs_lookup",
        "trace_logs",
        ["run_id", "policy_id", "projection_month", "variable_name"],
    )
