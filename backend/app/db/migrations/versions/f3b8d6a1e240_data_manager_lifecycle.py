"""Add the governed Data Manager lifecycle.

Revision ID: f3b8d6a1e240
Revises: d8b4e1c7a205
"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3b8d6a1e240"
down_revision: Union[str, Sequence[str], None] = "d8b4e1c7a205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _actor(name: str) -> sa.Column:
    return sa.Column(name, sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


def _add_dataset_versioning(table: str, parent_column: str) -> None:
    op.add_column(table, sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(table, sa.Column("version_group_id", sa.String(36), nullable=True))
    op.create_index(f"ix_{table}_version_group_id", table, ["version_group_id"])
    op.add_column(table, sa.Column(parent_column, sa.String(36), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_{parent_column}", table, table, [parent_column], ["id"], ondelete="RESTRICT"
    )
    op.add_column(table, sa.Column("import_session_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_import_session", table, "import_sessions",
        ["import_session_id"], ["id"], ondelete="RESTRICT",
    )
    op.add_column(table, sa.Column("raw_fingerprint", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("mapping_version", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("validation_run_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_validation_run", table, "validation_runs",
        ["validation_run_id"], ["id"], ondelete="RESTRICT",
    )
    for column in ("committed_by", "approved_by", "rejected_by"):
        op.add_column(table, sa.Column(column, sa.String(36), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_{column}_users", table, "users", [column], ["id"], ondelete="SET NULL"
        )
    op.add_column(table, sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("review_reason", sa.Text(), nullable=True))
    op.execute(sa.text(f"UPDATE {table} SET version_group_id = id WHERE version_group_id IS NULL"))


def upgrade() -> None:
    op.create_table(
        "mapping_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_profile_id", sa.String(36), sa.ForeignKey("mapping_profiles.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=False),
        _actor("created_by"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "category", "name", "version_number",
                            name="uq_mapping_profile_version"),
    )
    op.create_index("ix_mapping_profiles_project_id", "mapping_profiles", ["project_id"])

    op.create_table(
        "import_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version_label", sa.String(30), nullable=True),
        sa.Column("replaces_dataset_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=True),
        sa.Column("file_format", sa.String(20), nullable=True),
        sa.Column("raw_storage_uri", sa.String(1000), nullable=True),
        sa.Column("raw_fingerprint", sa.String(64), nullable=True),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("detected_types", sa.JSON(), nullable=False),
        sa.Column("mapping_fields", sa.JSON(), nullable=False),
        sa.Column("mapping_profile_id", sa.String(36), sa.ForeignKey("mapping_profiles.id", ondelete="RESTRICT"), nullable=True),
        _actor("created_by"), _actor("updated_by"), _actor("committed_by"),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dataset_kind", sa.String(30), nullable=True),
        sa.Column("dataset_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_sessions_project_id", "import_sessions", ["project_id"])

    op.create_table(
        "validation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_session_id", sa.String(36), sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("canonical_storage_uri", sa.String(1000), nullable=True),
        sa.Column("canonical_fingerprint", sa.String(64), nullable=True),
        sa.Column("fingerprint_scheme", sa.String(30), nullable=True),
        _actor("created_by"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_validation_runs_import_session_id", "validation_runs", ["import_session_id"])

    op.create_table(
        "validation_issues",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("validation_run_id", sa.String(36), sa.ForeignKey("validation_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("column_name", sa.String(255), nullable=True),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_validation_issues_validation_run_id", "validation_issues", ["validation_run_id"])

    op.create_table(
        "rejected_records",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("validation_run_id", sa.String(36), sa.ForeignKey("validation_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(1000), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("validation_run_id", "row_number", name="uq_rejected_record_row"),
    )
    op.create_index("ix_rejected_records_validation_run_id", "rejected_records", ["validation_run_id"])

    op.create_table(
        "import_session_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("import_session_id", sa.String(36), sa.ForeignKey("import_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_session_events_import_session_id", "import_session_events", ["import_session_id"])

    _add_dataset_versioning("inforce_files", "parent_file_id")
    _add_dataset_versioning("assumption_tables", "parent_table_id")
    _add_dataset_versioning("factor_tables", "parent_table_id")
    _add_dataset_versioning("scenario_tables", "parent_table_id")

    bind = op.get_bind()
    permission_id = str(uuid.uuid4())
    existing = bind.execute(sa.text("SELECT id FROM permissions WHERE name = 'imports:approve'")) .scalar()
    if existing:
        permission_id = existing
    else:
        bind.execute(sa.text(
            "INSERT INTO permissions (id, name, description, created_at) "
            "VALUES (:id, 'imports:approve', 'Approve or reject actuarial dataset versions.', :created_at)"
        ), {"id": permission_id, "created_at": datetime.now(timezone.utc)})
    bind.execute(sa.text(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT roles.id, :permission_id FROM roles "
        "WHERE roles.name IN ('admin', 'actuary', 'reviewer') "
        "AND NOT EXISTS (SELECT 1 FROM role_permissions rp "
        "WHERE rp.role_id = roles.id AND rp.permission_id = :permission_id)"
    ), {"permission_id": permission_id})


def downgrade() -> None:
    bind = op.get_bind()
    permission_id = bind.execute(sa.text(
        "SELECT id FROM permissions WHERE name = 'imports:approve'"
    )).scalar()
    if permission_id:
        bind.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :id"), {"id": permission_id})
        bind.execute(sa.text("DELETE FROM permissions WHERE id = :id"), {"id": permission_id})

    for table, parent_column in (
        ("scenario_tables", "parent_table_id"), ("factor_tables", "parent_table_id"),
        ("assumption_tables", "parent_table_id"), ("inforce_files", "parent_file_id"),
    ):
        for column in ("rejected_by", "approved_by", "committed_by"):
            op.drop_constraint(f"fk_{table}_{column}_users", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_validation_run", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_import_session", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_{parent_column}", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_version_group_id", table_name=table)
        for column in (
            "review_reason", "rejected_at", "rejected_by", "approved_at", "approved_by",
            "committed_at", "committed_by", "validation_run_id", "mapping_version",
            "raw_fingerprint", "import_session_id", parent_column, "version_group_id", "version_number",
        ):
            op.drop_column(table, column)

    for table, index in (
        ("import_session_events", "ix_import_session_events_import_session_id"),
        ("rejected_records", "ix_rejected_records_validation_run_id"),
        ("validation_issues", "ix_validation_issues_validation_run_id"),
        ("validation_runs", "ix_validation_runs_import_session_id"),
        ("import_sessions", "ix_import_sessions_project_id"),
        ("mapping_profiles", "ix_mapping_profiles_project_id"),
    ):
        op.drop_index(index, table_name=table)
        op.drop_table(table)
