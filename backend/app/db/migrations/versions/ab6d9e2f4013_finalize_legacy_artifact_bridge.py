"""Finalize attempt identity for verified M1 bridge artifacts.

Revision ID: ab6d9e2f4013
Revises: e6a9c4d2f817
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ab6d9e2f4013"
down_revision: Union[str, Sequence[str], None] = "e6a9c4d2f817"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace_fk(
    bind, table: str, column: str, referred_table: str, ondelete: str
) -> None:
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if (
            fk["constrained_columns"] == [column]
            and fk["referred_table"] == referred_table
        ):
            op.drop_constraint(fk["name"], table, type_="foreignkey")
            op.create_foreign_key(
                fk["name"],
                table,
                referred_table,
                [column],
                ["id"],
                ondelete=ondelete,
            )
            return
    raise RuntimeError(f"Foreign key {table}.{column} was not found.")


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_runs_retried_from_run_id", "runs", ["retried_from_run_id"]
    )
    bind = op.get_bind()
    op.create_table(
        "import_session_datasets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_session_id", sa.String(36), nullable=False),
        sa.Column("dataset_kind", sa.String(30), nullable=False),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["import_session_id"], ["import_sessions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "import_session_id", "dataset_kind", "dataset_id",
            name="uq_import_session_dataset",
        ),
    )
    op.create_index(
        "ix_import_session_datasets_import_session_id",
        "import_session_datasets",
        ["import_session_id"],
    )
    for table, kind in (
        ("inforce_files", "inforce"),
        ("assumption_tables", "assumption"),
        ("factor_tables", "factor"),
        ("scenario_tables", "scenario"),
    ):
        op.create_unique_constraint(
            f"uq_{table}_version_group_number",
            table,
            ["version_group_id", "version_number"],
        )
        bind.execute(sa.text(
            f"INSERT INTO import_session_datasets "
            "(import_session_id, dataset_kind, dataset_id, created_at) "
            f"SELECT import_session_id, :kind, id, CURRENT_TIMESTAMP FROM {table} "
            "WHERE import_session_id IS NOT NULL "
            "ON CONFLICT (import_session_id, dataset_kind, dataset_id) DO NOTHING"
        ), {"kind": kind})
    op.add_column(
        "validation_runs",
        sa.Column("canonical_checksum_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "validation_runs",
        sa.Column("canonical_row_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "validation_runs",
        sa.Column("canonical_schema_version", sa.String(30), nullable=True),
    )
    op.add_column(
        "rejected_records",
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "rejected_records",
        sa.Column("artifact_row_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "rejected_records",
        sa.Column("artifact_schema_version", sa.String(30), nullable=True),
    )
    _replace_fk(bind, "run_artifacts", "run_id", "runs", "RESTRICT")
    _replace_fk(bind, "audit_logs", "actor_user_id", "users", "RESTRICT")
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(
            "UPDATE run_artifacts SET attempt_number = 1 "
            "WHERE attempt_number IS NULL "
            "AND partition->>'legacy_bridge' = 'true'"
        ))
        missing = bind.execute(sa.text(
            "SELECT COUNT(*) FROM run_artifacts "
            "WHERE partition->>'legacy_bridge' = 'true' AND attempt_number IS NULL"
        )).scalar_one()
        if missing:
            raise RuntimeError(
                "Verified legacy artifacts are missing attempt identity."
            )
        op.execute(
            "CREATE TRIGGER run_artifacts_write_once BEFORE UPDATE ON run_artifacts "
            "FOR EACH ROW EXECUTE FUNCTION mentoramp_refuse_update()"
        )
        op.execute(
            "CREATE TRIGGER run_artifacts_retained BEFORE DELETE ON run_artifacts "
            "FOR EACH ROW EXECUTE FUNCTION mentoramp_refuse_evidence_delete()"
        )
        op.execute("""
            CREATE FUNCTION mentoramp_refuse_audit_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_logs rows are append-only'
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(
            "CREATE TRIGGER audit_logs_append_only "
            "BEFORE UPDATE OR DELETE ON audit_logs "
            "FOR EACH ROW EXECUTE FUNCTION mentoramp_refuse_audit_mutation()"
        )
        op.execute("""
            CREATE FUNCTION mentoramp_protect_committed_dataset() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF OLD.committed_at IS NOT NULL THEN
                        RAISE EXCEPTION '% committed content is immutable', TG_TABLE_NAME
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    RETURN OLD;
                END IF;
                IF OLD.committed_at IS NOT NULL
                   AND (to_jsonb(NEW) - ARRAY[
                        'status', 'approved_by', 'approved_at', 'rejected_by',
                        'rejected_at', 'review_reason'
                   ]) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY[
                        'status', 'approved_by', 'approved_at', 'rejected_by',
                        'rejected_at', 'review_reason'
                   ])
                THEN
                    RAISE EXCEPTION '% committed content is immutable', TG_TABLE_NAME
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in (
            "inforce_files", "assumption_tables", "factor_tables", "scenario_tables"
        ):
            op.execute(
                f"CREATE TRIGGER {table}_committed_content "
                f"BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_committed_dataset()"
            )
        op.execute("""
            CREATE FUNCTION mentoramp_protect_inforce_record() RETURNS trigger AS $$
            DECLARE committed timestamptz;
            BEGIN
                SELECT committed_at INTO committed
                FROM inforce_files WHERE id = OLD.file_id;
                IF committed IS NOT NULL THEN
                    RAISE EXCEPTION 'inforce_records committed content is immutable'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(
            "CREATE TRIGGER inforce_records_committed_content "
            "BEFORE UPDATE OR DELETE ON inforce_records "
            "FOR EACH ROW EXECUTE FUNCTION mentoramp_protect_inforce_record()"
        )


def downgrade() -> None:
    op.drop_constraint("uq_runs_retried_from_run_id", "runs", type_="unique")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS inforce_records_committed_content "
            "ON inforce_records"
        )
        op.execute("DROP FUNCTION IF EXISTS mentoramp_protect_inforce_record()")
        for table in (
            "inforce_files", "assumption_tables", "factor_tables", "scenario_tables"
        ):
            op.execute(
                f"DROP TRIGGER IF EXISTS {table}_committed_content ON {table}"
            )
        op.execute("DROP FUNCTION IF EXISTS mentoramp_protect_committed_dataset()")
        op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_refuse_audit_mutation()")
        op.execute("DROP TRIGGER IF EXISTS run_artifacts_retained ON run_artifacts")
        op.execute("DROP TRIGGER IF EXISTS run_artifacts_write_once ON run_artifacts")
    _replace_fk(bind, "audit_logs", "actor_user_id", "users", "SET NULL")
    _replace_fk(bind, "run_artifacts", "run_id", "runs", "CASCADE")
    op.drop_column("rejected_records", "artifact_schema_version")
    op.drop_column("rejected_records", "artifact_row_count")
    op.drop_column("rejected_records", "checksum_sha256")
    op.drop_column("validation_runs", "canonical_schema_version")
    op.drop_column("validation_runs", "canonical_row_count")
    op.drop_column("validation_runs", "canonical_checksum_sha256")
    for table in (
        "scenario_tables", "factor_tables", "assumption_tables", "inforce_files"
    ):
        op.drop_constraint(
            f"uq_{table}_version_group_number", table, type_="unique"
        )
    op.drop_index(
        "ix_import_session_datasets_import_session_id",
        table_name="import_session_datasets",
    )
    op.drop_table("import_session_datasets")
    # Attempt identity remains retained evidence rather than transient scaffolding.
