"""Add Milestone 5 canonical valuation, derived datasets, and rollforward metadata.

Revision ID: b8f3d1e7c504
Revises: a6c1f4e8d203
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8f3d1e7c504"
down_revision: Union[str, Sequence[str], None] = "a6c1f4e8d203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Extend runs with canonical valuation identity and provenance
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column("run_type", sa.String(30), nullable=False, server_default="projection")
        )
        batch_op.add_column(sa.Column("basis", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("methodology", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("methodology_version", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("source_projection_run_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("derived_dataset_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_runs_source_projection_run_id",
            "runs", ["source_projection_run_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_runs_derived_dataset_id",
            "derived_datasets", ["derived_dataset_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_index("ix_runs_source_projection_run_id", ["source_projection_run_id"])
        batch_op.create_index("ix_runs_derived_dataset_id", ["derived_dataset_id"])

    # 2. Extend derived_datasets with output-as-input provenance & four-eyes approval
    with op.batch_alter_table("derived_datasets") as batch_op:
        batch_op.add_column(sa.Column("source_run_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("source_artifact_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("source_model_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("product_code", sa.String(50), nullable=True))
        batch_op.add_column(
            sa.Column("dimensions_available", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(sa.Column("approved_by", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_derived_datasets_source_run_id",
            "runs", ["source_run_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_derived_datasets_source_artifact_id",
            "run_artifacts", ["source_artifact_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_derived_datasets_source_model_id",
            "models", ["source_model_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_derived_datasets_approved_by",
            "users", ["approved_by"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_index("ix_derived_datasets_source_run_id", ["source_run_id"])
        batch_op.create_index("ix_derived_datasets_source_artifact_id", ["source_artifact_id"])
        batch_op.create_index("ix_derived_datasets_source_model_id", ["source_model_id"])

    # 3. Create valuation_run_details (subordinate extension to canonical runs)
    op.create_table(
        "valuation_run_details",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_projection_run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("derived_dataset_id", sa.String(36), sa.ForeignKey("derived_datasets.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("basis", sa.String(50), nullable=False),
        sa.Column("methodology", sa.String(50), nullable=False),
        sa.Column("methodology_version", sa.String(50), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("discount_curve_fingerprint", sa.String(64), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("summary_results", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("basis IN ('GAAP', 'STATUTORY', 'TAX')", name="ck_valuation_basis"),
        sa.CheckConstraint(
            "methodology IN ('VM21', 'VM22', 'LDTI', 'DAC', 'MRB', 'ASC815')",
            name="ck_valuation_methodology",
        ),
    )
    op.create_index("ix_valuation_run_details_project_id", "valuation_run_details", ["project_id"])
    op.create_index(
        "ix_valuation_run_details_source_projection_run_id",
        "valuation_run_details",
        ["source_projection_run_id"],
    )

    # 4. Create rollforward_summaries (generic attribution schedules)
    op.create_table(
        "rollforward_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cohort_id", sa.String(100), nullable=False),
        sa.Column("methodology", sa.String(50), nullable=False),
        sa.Column("basis", sa.String(50), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("component_name", sa.String(100), nullable=False),
        sa.Column("component_order", sa.Integer(), nullable=False),
        sa.Column("is_movement", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("opening_balance", sa.Numeric(18, 4), nullable=False, server_default="0.0"),
        sa.Column("movement_amount", sa.Numeric(18, 4), nullable=False, server_default="0.0"),
        sa.Column("closing_balance", sa.Numeric(18, 4), nullable=False, server_default="0.0"),
        sa.Column("is_continuity_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("continuity_difference", sa.Numeric(18, 4), nullable=False, server_default="0.0"),
        sa.Column("trace_identifier", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rollforward_summaries_run_id", "rollforward_summaries", ["run_id"])
    op.create_index("ix_rollforward_summaries_project_id", "rollforward_summaries", ["project_id"])
    op.create_index("ix_rollforward_summaries_cohort_id", "rollforward_summaries", ["cohort_id"])

    # 5. Immutability triggers for PostgreSQL
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Protect rollforward_summaries against mutation or deletion
        op.execute("""
            CREATE FUNCTION mentoramp_protect_rollforward_summaries() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'rollforward_summaries accounting evidence is immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'rollforward_summaries accounting evidence is immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER rollforward_summaries_protect_continuity
            BEFORE UPDATE OR DELETE ON rollforward_summaries
            FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_rollforward_summaries();
        """)

        # Protect valuation_run_details against deletion
        op.execute("""
            CREATE FUNCTION mentoramp_protect_valuation_run_details() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'valuation_run_details execution evidence cannot be deleted'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER valuation_run_details_protect_evidence
            BEFORE DELETE ON valuation_run_details
            FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_valuation_run_details();
        """)

        # Protect approved derived datasets against mutation or deletion
        op.execute("""
            CREATE FUNCTION mentoramp_protect_derived_datasets() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.status = 'approved' THEN
                        RAISE EXCEPTION 'Approved derived_datasets cannot be deleted'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF OLD.status = 'approved' AND NEW.status != 'superseded' THEN
                        RAISE EXCEPTION 'Approved derived_datasets cannot be modified except to mark superseded'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER derived_datasets_protect_approved
            BEFORE UPDATE OR DELETE ON derived_datasets
            FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_derived_datasets();
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS derived_datasets_protect_approved ON derived_datasets;")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_protect_derived_datasets();")
        op.execute("DROP TRIGGER IF EXISTS valuation_run_details_protect_evidence ON valuation_run_details;")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_protect_valuation_run_details();")
        op.execute("DROP TRIGGER IF EXISTS rollforward_summaries_protect_continuity ON rollforward_summaries;")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_protect_rollforward_summaries();")

    op.drop_table("rollforward_summaries")
    op.drop_table("valuation_run_details")

    with op.batch_alter_table("derived_datasets") as batch_op:
        batch_op.drop_index("ix_derived_datasets_source_model_id")
        batch_op.drop_index("ix_derived_datasets_source_artifact_id")
        batch_op.drop_index("ix_derived_datasets_source_run_id")
        batch_op.drop_constraint("fk_derived_datasets_approved_by", type_="foreignkey")
        batch_op.drop_constraint("fk_derived_datasets_source_model_id", type_="foreignkey")
        batch_op.drop_constraint("fk_derived_datasets_source_artifact_id", type_="foreignkey")
        batch_op.drop_constraint("fk_derived_datasets_source_run_id", type_="foreignkey")
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by")
        batch_op.drop_column("dimensions_available")
        batch_op.drop_column("product_code")
        batch_op.drop_column("source_model_id")
        batch_op.drop_column("source_artifact_id")
        batch_op.drop_column("source_run_id")

    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_index("ix_runs_derived_dataset_id")
        batch_op.drop_index("ix_runs_source_projection_run_id")
        batch_op.drop_constraint("fk_runs_derived_dataset_id", type_="foreignkey")
        batch_op.drop_constraint("fk_runs_source_projection_run_id", type_="foreignkey")
        batch_op.drop_column("derived_dataset_id")
        batch_op.drop_column("source_projection_run_id")
        batch_op.drop_column("methodology_version")
        batch_op.drop_column("methodology")
        batch_op.drop_column("basis")
        batch_op.drop_column("run_type")
