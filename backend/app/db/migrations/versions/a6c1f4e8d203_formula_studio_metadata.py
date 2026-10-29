"""Add governed Formula Studio metadata and immutable reconciliation evidence.

Revision ID: a6c1f4e8d203
Revises: e5b2c9a1d408
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6c1f4e8d203"
down_revision: Union[str, Sequence[str], None] = "e5b2c9a1d408"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("formula_registry") as batch_op:
        batch_op.add_column(sa.Column("effective_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("owner_user_id", sa.String(36), nullable=True))
        batch_op.add_column(
            sa.Column("test_cases", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.create_foreign_key(
            "fk_formula_registry_owner_user_id_users",
            "users", ["owner_user_id"], ["id"], ondelete="SET NULL",
        )

    op.create_table(
        "expected_result_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("version_label", sa.String(30), nullable=False, server_default="v1"),
        sa.Column("source_reference", sa.String(500), nullable=False),
        sa.Column("timing_convention", sa.String(100), nullable=True),
        sa.Column("granularity", sa.String(30), nullable=False, server_default="policy_month"),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("columns_detected", sa.JSON(), nullable=False),
        sa.Column("rows_data", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="validated"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.String(36), sa.ForeignKey("expected_result_sets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_expected_result_sets_project_id", "expected_result_sets", ["project_id"])
    op.create_index("ix_expected_result_sets_fingerprint", "expected_result_sets", ["fingerprint"])

    op.create_table(
        "reconciliation_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expected_result_set_id", sa.String(36), sa.ForeignKey("expected_result_sets.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("level", sa.String(30), nullable=False, server_default="policy_month"),
        sa.Column("variables_requested", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("expected_result_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("expected_result_version", sa.String(30), nullable=False, server_default="v1"),
        sa.Column("timing_convention_snapshot", sa.String(100), nullable=True),
        sa.Column("granularity_snapshot", sa.String(30), nullable=False, server_default="policy_month"),
        sa.Column("policy_id_filter", sa.String(255), nullable=True),
        sa.Column("abs_tol", sa.Float(), nullable=False, server_default="0.01"),
        sa.Column("rel_tol", sa.Float(), nullable=False, server_default="0.0001"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("disposition_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("disposition_notes", sa.Text(), nullable=True),
        sa.Column("disposition_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("disposition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("failures", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reconciliation_reports_project_id", "reconciliation_reports", ["project_id"])
    op.create_index("ix_reconciliation_reports_run_id", "reconciliation_reports", ["run_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION mentoramp_protect_expected_result_set() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'expected_result_sets benchmark content is immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF (to_jsonb(NEW) - ARRAY['status', 'approved_by', 'approved_at', 'superseded_by_id', 'updated_at'])
                   IS DISTINCT FROM
                   (to_jsonb(OLD) - ARRAY['status', 'approved_by', 'approved_at', 'superseded_by_id', 'updated_at'])
                THEN
                    RAISE EXCEPTION 'expected_result_sets benchmark content is immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER expected_result_sets_protect_benchmark
            BEFORE UPDATE OR DELETE ON expected_result_sets
            FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_expected_result_set();
        """)

        op.execute("""
            CREATE FUNCTION mentoramp_protect_reconciliation_report() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'reconciliation_reports calculation evidence is immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF (to_jsonb(NEW) - ARRAY['disposition_status', 'disposition_notes', 'disposition_by', 'disposition_at', 'disposition_history'])
                   IS DISTINCT FROM
                   (to_jsonb(OLD) - ARRAY['disposition_status', 'disposition_notes', 'disposition_by', 'disposition_at', 'disposition_history'])
                THEN
                    RAISE EXCEPTION 'reconciliation_reports calculation evidence is immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER reconciliation_reports_protect_calculation
            BEFORE UPDATE OR DELETE ON reconciliation_reports
            FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_reconciliation_report();
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS reconciliation_reports_protect_calculation ON reconciliation_reports;")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_protect_reconciliation_report();")
        op.execute("DROP TRIGGER IF EXISTS expected_result_sets_protect_benchmark ON expected_result_sets;")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_protect_expected_result_set();")

    op.drop_index("ix_reconciliation_reports_run_id", table_name="reconciliation_reports")
    op.drop_index("ix_reconciliation_reports_project_id", table_name="reconciliation_reports")
    op.drop_table("reconciliation_reports")
    op.drop_index("ix_expected_result_sets_fingerprint", table_name="expected_result_sets")
    op.drop_index("ix_expected_result_sets_project_id", table_name="expected_result_sets")
    op.drop_table("expected_result_sets")
    with op.batch_alter_table("formula_registry") as batch_op:
        batch_op.drop_constraint("fk_formula_registry_owner_user_id_users", type_="foreignkey")
        batch_op.drop_column("test_cases")
        batch_op.drop_column("owner_user_id")
        batch_op.drop_column("effective_date")
