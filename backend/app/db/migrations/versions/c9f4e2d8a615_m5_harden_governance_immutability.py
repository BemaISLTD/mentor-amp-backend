"""Harden M5 governance immutability, foreign keys, and valuation detail schema.

Revision ID: c9f4e2d8a615
Revises: b8f3d1e7c504

Downgrade Governance & Data-Loss Policy:
- Downgrading `valuation_run_details` from `c9f4e2d8a615` to `b8f3d1e7c504` restores
  legacy columns (`project_id`, `source_projection_run_id`, `derived_dataset_id`,
  `basis`, `methodology`, `methodology_version`) strictly from the parent `runs` row.
  It NEVER fabricates default values (`'STATUTORY'`, `'VM22'`, `'v1'`). A preflight
  check verifies that every `valuation_run_details` row has a valid parent `runs` row
  with non-null `project_id`, `basis`, `methodology`, and `methodology_version` matching
  the target check constraints; if any row is incomplete or invalid, downgrade fails
  closed with an actionable `RuntimeError`.
- Dropping `derived_datasets.source_model_version_id` during downgrade discards any
  populated model-version lineage on `derived_datasets`. Preflight checks whether any
  `derived_datasets` row has `source_model_version_id IS NOT NULL`; if populated,
  downgrade fails closed unless `MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE=1` is explicitly
  set in the environment.
"""

import logging
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


logger = logging.getLogger("alembic.runtime.migration")

revision: str = "c9f4e2d8a615"
down_revision: Union[str, Sequence[str], None] = "b8f3d1e7c504"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VALID_BASES = ("GAAP", "STATUTORY", "TAX")
VALID_METHODOLOGIES = ("VM21", "VM22", "LDTI", "DAC", "MRB", "ASC815")


def upgrade() -> None:
    # 1. Add source_model_version_id to derived_datasets with FK to model_versions.id
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

    # 3. Harden PostgreSQL immutability triggers (Directives 4, 8 & 9)
    if bind.dialect.name == "postgresql":
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
                        -- Lifecycle transition constraint (Directive 4):
                        -- approved can ONLY stay approved or transition to superseded
                        IF OLD.status = 'approved' AND NEW.status NOT IN ('approved', 'superseded') THEN
                            RAISE EXCEPTION 'Approved derived_datasets status can only remain approved or transition to superseded'
                                USING ERRCODE = 'integrity_constraint_violation';
                        END IF;

                        -- superseded status is terminal: can NEVER be changed
                        IF OLD.status = 'superseded' AND NEW.status != 'superseded' THEN
                            RAISE EXCEPTION 'Superseded derived_datasets status cannot be changed'
                                USING ERRCODE = 'integrity_constraint_violation';
                        END IF;

                        -- Soft-delete protection: approved or superseded evidence cannot be soft-deleted
                        IF (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL) OR
                           (OLD.deleted_by IS NULL AND NEW.deleted_by IS NOT NULL) THEN
                            RAISE EXCEPTION 'Approved or superseded derived_datasets cannot be soft-deleted'
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

        # Directive 9: Protect valuation_run_details
        op.execute("""
            CREATE OR REPLACE FUNCTION mentoramp_protect_valuation_run_details() RETURNS trigger AS $$
            DECLARE
                parent_status VARCHAR(30);
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'valuation_run_details execution evidence cannot be deleted'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    SELECT status INTO parent_status FROM runs WHERE id = OLD.run_id;
                    IF parent_status IN ('completed', 'success') THEN
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


def _preflight_downgrade_checks(bind: sa.engine.Connection) -> None:
    """Fail closed before downgrade if parent runs metadata is missing/invalid or unacknowledged data loss would occur."""
    # 1. Check for orphan valuation_run_details rows without a parent runs row
    orphan_rows = bind.execute(
        sa.text("""
            SELECT v.run_id
            FROM valuation_run_details v
            LEFT JOIN runs r ON v.run_id = r.id
            WHERE r.id IS NULL
        """)
    ).fetchall()
    if orphan_rows:
        orphan_ids = [row[0] for row in orphan_rows]
        raise RuntimeError(
            "Downgrade preflight failed: orphan valuation_run_details rows without parent runs row: "
            f"{orphan_ids}. Remediate orphan rows before downgrading to b8f3d1e7c504."
        )

    # 2. Check for parent runs rows with NULL or invalid basis/methodology/methodology_version/project_id
    invalid_parent_rows = bind.execute(
        sa.text("""
            SELECT v.run_id, r.project_id, r.basis, r.methodology, r.methodology_version
            FROM valuation_run_details v
            JOIN runs r ON v.run_id = r.id
            WHERE r.project_id IS NULL
               OR r.basis IS NULL
               OR r.methodology IS NULL
               OR r.methodology_version IS NULL
               OR r.basis NOT IN ('GAAP', 'STATUTORY', 'TAX')
               OR r.methodology NOT IN ('VM21', 'VM22', 'LDTI', 'DAC', 'MRB', 'ASC815')
        """)
    ).fetchall()
    if invalid_parent_rows:
        details = [
            {
                "run_id": row[0],
                "project_id": row[1],
                "basis": row[2],
                "methodology": row[3],
                "methodology_version": row[4],
            }
            for row in invalid_parent_rows
        ]
        raise RuntimeError(
            "Downgrade preflight failed: parent runs rows for valuation_run_details contain NULL or invalid "
            f"governance metadata (refusing to fabricate fallback defaults): {details}"
        )

    # 3. Check for populated derived_datasets.source_model_version_id data-loss risk
    populated_mv_rows = bind.execute(
        sa.text("""
            SELECT id, source_model_version_id
            FROM derived_datasets
            WHERE source_model_version_id IS NOT NULL
        """)
    ).fetchall()
    if populated_mv_rows:
        allow_override = os.environ.get("MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE") == "1"
        lineage_map = {row[0]: row[1] for row in populated_mv_rows}
        if not allow_override:
            raise RuntimeError(
                "Downgrade preflight failed: derived_datasets.source_model_version_id is populated on "
                f"{len(populated_mv_rows)} row(s) ({lineage_map}). Dropping this column will discard governed "
                "model-version lineage. Set MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE=1 to acknowledge and proceed."
            )
        logger.warning(
            "MENTORAMP_ALLOW_DATA_LOSS_DOWNGRADE=1 acknowledged; dropping derived_datasets.source_model_version_id "
            "for rows: %s",
            lineage_map,
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = (bind.dialect.name == "postgresql")

    # 0. Fail-closed preflight verification before mutating schema or triggers
    _preflight_downgrade_checks(bind)

    # 1. Restore b8 triggers and functions on PostgreSQL (Directive 5)
    if is_postgres:
        # Restore b8 DELETE-only trigger on valuation_run_details
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

        # Restore b8 trigger function for derived_datasets
        op.execute("""
            CREATE OR REPLACE FUNCTION mentoramp_protect_derived_datasets() RETURNS trigger AS $$
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

    # 2. Restore b8 valuation_run_details columns, nullability, foreign keys, constraints, and indexes
    # Staged downgrade:
    # 2a. Add legacy columns as nullable without foreign keys or server defaults
    with op.batch_alter_table("valuation_run_details") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("source_projection_run_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("derived_dataset_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("basis", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("methodology", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("methodology_version", sa.String(50), nullable=True))

    # 2b. Backfill existing rows strictly from canonical parent runs table (zero COALESCE fabrication)
    if is_postgres:
        op.execute("""
            UPDATE valuation_run_details v
            SET 
                project_id = r.project_id,
                source_projection_run_id = r.source_projection_run_id,
                derived_dataset_id = r.derived_dataset_id,
                basis = r.basis,
                methodology = r.methodology,
                methodology_version = r.methodology_version
            FROM runs r
            WHERE v.run_id = r.id;
        """)
    else:
        op.execute("""
            UPDATE valuation_run_details
            SET 
                project_id = (SELECT project_id FROM runs WHERE runs.id = valuation_run_details.run_id),
                source_projection_run_id = (SELECT source_projection_run_id FROM runs WHERE runs.id = valuation_run_details.run_id),
                derived_dataset_id = (SELECT derived_dataset_id FROM runs WHERE runs.id = valuation_run_details.run_id),
                basis = (SELECT basis FROM runs WHERE runs.id = valuation_run_details.run_id),
                methodology = (SELECT methodology FROM runs WHERE runs.id = valuation_run_details.run_id),
                methodology_version = (SELECT methodology_version FROM runs WHERE runs.id = valuation_run_details.run_id)
            WHERE EXISTS (SELECT 1 FROM runs WHERE runs.id = valuation_run_details.run_id);
        """)

    # 2c. Enforce NOT NULL, foreign keys, check constraints, and indexes
    with op.batch_alter_table("valuation_run_details") as batch_op:
        batch_op.alter_column("project_id", nullable=False)
        batch_op.alter_column("basis", nullable=False)
        batch_op.alter_column("methodology", nullable=False)
        batch_op.alter_column("methodology_version", nullable=False)
        batch_op.create_foreign_key(
            "fk_valuation_run_details_project_id",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_valuation_run_details_source_projection_run_id",
            "runs",
            ["source_projection_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_valuation_run_details_derived_dataset_id",
            "derived_datasets",
            ["derived_dataset_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_valuation_run_details_project_id",
            ["project_id"],
        )
        batch_op.create_index(
            "ix_valuation_run_details_source_projection_run_id",
            ["source_projection_run_id"],
        )

    if is_postgres:
        op.create_check_constraint(
            "ck_valuation_basis",
            "valuation_run_details",
            "basis IN ('GAAP', 'STATUTORY', 'TAX')",
        )
        op.create_check_constraint(
            "ck_valuation_methodology",
            "valuation_run_details",
            "methodology IN ('VM21', 'VM22', 'LDTI', 'DAC', 'MRB', 'ASC815')",
        )

    # 3. Drop source_model_version_id column and index from derived_datasets
    with op.batch_alter_table("derived_datasets") as batch_op:
        batch_op.drop_index("ix_derived_datasets_source_model_version_id")
        batch_op.drop_column("source_model_version_id")
