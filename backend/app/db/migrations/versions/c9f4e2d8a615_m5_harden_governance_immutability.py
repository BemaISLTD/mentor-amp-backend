"""Harden M5 governance immutability and refine valuation detail schema.

Revision ID: c9f4e2d8a615
Revises: b8f3d1e7c504
Create Date: 2026-11-01 17:20:00.000000

Implements Senior Review Directives 4, 8, 9, 13:
1. Directive 13: Adds source_model_version_id FK to derived_datasets.
2. Directive 4: Removes redundant execution identity columns from valuation_run_details
   so canonical Run remains the sole owner of project, basis, methodology, and lineage.
3. Directive 8: Closes derived_datasets immutability loophole in PostgreSQL trigger so
   material evidence cannot be mutated even during supersession; prevents deletion of superseded.
4. Directive 9: Enforces immutability on valuation_run_details for finalized/completed runs.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c9f4e2d8a615"
down_revision = "b8f3d1e7c504"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add source_model_version_id to derived_datasets (Directive 13)
    with op.batch_alter_table("derived_datasets") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_model_version_id",
                sa.String(36),
                sa.ForeignKey("model_versions.id", ondelete="RESTRICT"),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_derived_datasets_source_model_version_id",
            ["source_model_version_id"],
        )

    # 2. Refine valuation_run_details schema to eliminate redundant identity (Directive 4)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_valuation_run_details_project_id;")
        op.execute("DROP INDEX IF EXISTS ix_valuation_run_details_source_projection_run_id;")
        op.execute("ALTER TABLE valuation_run_details DROP CONSTRAINT IF EXISTS ck_valuation_basis;")
        op.execute("ALTER TABLE valuation_run_details DROP CONSTRAINT IF EXISTS ck_valuation_methodology;")

    with op.batch_alter_table("valuation_run_details") as batch_op:
        batch_op.drop_column("project_id")
        batch_op.drop_column("source_projection_run_id")
        batch_op.drop_column("derived_dataset_id")
        batch_op.drop_column("basis")
        batch_op.drop_column("methodology")
        batch_op.drop_column("methodology_version")

    # 3. Harden PostgreSQL immutability triggers (Directives 8 & 9)
    if bind.dialect.name == "postgresql":
        # Directive 8: Close loophole on derived_datasets
        op.execute("""
            CREATE OR REPLACE FUNCTION mentoramp_protect_derived_datasets() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.status IN ('approved', 'superseded') THEN
                        RAISE EXCEPTION 'Approved or superseded derived_datasets cannot be deleted'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    IF OLD.status IN ('approved', 'superseded') THEN
                        -- Cannot revert superseded status
                        IF OLD.status = 'superseded' AND NEW.status != 'superseded' THEN
                            RAISE EXCEPTION 'Superseded derived_datasets status cannot be changed'
                                USING ERRCODE = 'integrity_constraint_violation';
                        END IF;

                        -- Forbid any modification to material provenance and analytical evidence
                        IF OLD.fingerprint != NEW.fingerprint OR
                           OLD.storage_uri != NEW.storage_uri OR
                           OLD.source_run_id IS DISTINCT FROM NEW.source_run_id OR
                           OLD.source_artifact_id IS DISTINCT FROM NEW.source_artifact_id OR
                           OLD.source_model_id IS DISTINCT FROM NEW.source_model_id OR
                           OLD.source_model_version_id IS DISTINCT FROM NEW.source_model_version_id OR
                           OLD.schema::text != NEW.schema::text OR
                           OLD.row_count IS DISTINCT FROM NEW.row_count OR
                           OLD.dimensions_available::text != NEW.dimensions_available::text OR
                           OLD.project_id != NEW.project_id OR
                           OLD.name != NEW.name OR
                           OLD.version_number != NEW.version_number THEN
                            RAISE EXCEPTION 'Approved derived_datasets material evidence is immutable'
                                USING ERRCODE = 'integrity_constraint_violation';
                        END IF;
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)

        # Directive 9: Completed valuation execution evidence is immutable
        op.execute("""
            CREATE OR REPLACE FUNCTION mentoramp_protect_valuation_run_details() RETURNS trigger AS $$
            DECLARE
                parent_status VARCHAR(20);
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'valuation_run_details execution evidence cannot be deleted'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    SELECT status INTO parent_status FROM runs WHERE id = OLD.run_id;
                    IF parent_status IN ('completed', 'accepted', 'finalized', 'success') THEN
                        RAISE EXCEPTION 'valuation_run_details execution evidence is immutable for completed runs'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)

        # Re-attach update/delete trigger on valuation_run_details
        op.execute("DROP TRIGGER IF EXISTS valuation_run_details_protect_evidence ON valuation_run_details;")
        op.execute("""
            CREATE TRIGGER valuation_run_details_protect_evidence
            BEFORE UPDATE OR DELETE ON valuation_run_details
            FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_valuation_run_details();
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Restore original triggers
        op.execute("DROP TRIGGER IF EXISTS valuation_run_details_protect_evidence ON valuation_run_details;")
        op.execute("""
            CREATE OR REPLACE FUNCTION mentoramp_protect_valuation_run_details() RETURNS trigger AS $$
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

    with op.batch_alter_table("valuation_run_details") as batch_op:
        batch_op.add_column(sa.Column("methodology_version", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("methodology", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("basis", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("derived_dataset_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("source_projection_run_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("project_id", sa.String(36), nullable=True))

    with op.batch_alter_table("derived_datasets") as batch_op:
        batch_op.drop_index("ix_derived_datasets_source_model_version_id")
        batch_op.drop_column("source_model_version_id")
