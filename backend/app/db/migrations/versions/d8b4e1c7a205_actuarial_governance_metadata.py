"""Add governed actuarial workflow, reporting, version, and execution metadata.

Revision ID: d8b4e1c7a205
Revises: e4a1c2b9d0f3
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8b4e1c7a205"
down_revision: Union[str, Sequence[str], None] = "e4a1c2b9d0f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _actor_column(name: str) -> sa.Column:
    return sa.Column(name, sa.String(36), nullable=True)


def _actor_fk(table: str, column: str) -> None:
    op.create_foreign_key(
        f"fk_{table}_{column}_users", table, "users", [column], ["id"], ondelete="SET NULL"
    )


def upgrade() -> None:
    for table in ("variable_registry", "formula_registry"):
        op.add_column(table, _actor_column("deleted_by"))
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        _actor_fk(table, "deleted_by")

    for column in ("created_by", "updated_by", "deleted_by"):
        op.add_column("models", _actor_column(column))
        _actor_fk("models", column)
    op.add_column("models", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("model_versions", sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("model_versions", sa.Column("parent_version_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_model_versions_parent_version", "model_versions", "model_versions",
        ["parent_version_id"], ["id"], ondelete="RESTRICT",
    )
    op.add_column("model_versions", sa.Column("change_summary", sa.Text(), nullable=True))
    op.add_column(
        "model_versions", sa.Column("configuration", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
    )
    for column in ("created_by", "updated_by", "published_by", "deleted_by"):
        op.add_column("model_versions", _actor_column(column))
        _actor_fk("model_versions", column)
    op.add_column("model_versions", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_versions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("projection_sets", _actor_column("updated_by"))
    _actor_fk("projection_sets", "updated_by")
    op.execute("UPDATE projection_sets SET updated_by = created_by WHERE updated_by IS NULL")

    for column in ("created_by", "updated_by"):
        op.add_column("model_variable_definitions", _actor_column(column))
        _actor_fk("model_variable_definitions", column)

    op.create_table(
        "rollforward_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_template_id", sa.String(36), nullable=True),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("configuration", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        _actor_column("created_by"), _actor_column("updated_by"), _actor_column("published_by"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        _actor_column("deleted_by"), sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", "version_number", name="uq_rollforward_template_version"),
        sa.ForeignKeyConstraint(["parent_template_id"], ["rollforward_templates.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_rollforward_templates_project_id", "rollforward_templates", ["project_id"])
    for column in ("created_by", "updated_by", "published_by", "deleted_by"):
        _actor_fk("rollforward_templates", column)

    op.create_table(
        "rollforward_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("template_id", sa.String(36), sa.ForeignKey("rollforward_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("run_set_id", sa.String(36), sa.ForeignKey("run_sets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        _actor_column("created_by"), _actor_column("updated_by"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rollforward_jobs_project_id", "rollforward_jobs", ["project_id"])
    for column in ("created_by", "updated_by"):
        _actor_fk("rollforward_jobs", column)

    op.create_table(
        "rollforward_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("template_id", sa.String(36), sa.ForeignKey("rollforward_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("rollforward_jobs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("template_step_id", sa.String(36), nullable=True),
        sa.Column("step_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("configuration", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("result", sa.JSON(), nullable=True),
        _actor_column("created_by"), _actor_column("updated_by"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_step_id"], ["rollforward_steps.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("template_id", "job_id", "sequence", name="uq_rollforward_step_sequence"),
    )
    op.create_index("ix_rollforward_steps_template_id", "rollforward_steps", ["template_id"])
    op.create_index("ix_rollforward_steps_job_id", "rollforward_steps", ["job_id"])
    for column in ("created_by", "updated_by"):
        _actor_fk("rollforward_steps", column)

    op.create_table(
        "reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("report_type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_report_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("definition", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("artifact_uri", sa.String(1000), nullable=True),
        sa.Column("artifact_fingerprint", sa.String(64), nullable=True),
        _actor_column("created_by"), _actor_column("updated_by"), _actor_column("published_by"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        _actor_column("deleted_by"), sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "name", "version_number", name="uq_report_version"),
    )
    op.create_index("ix_reports_project_id", "reports", ["project_id"])
    for column in ("created_by", "updated_by", "published_by", "deleted_by"):
        _actor_fk("reports", column)

    op.create_table(
        "derived_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("dataset_type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_dataset_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("source_run_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("storage_uri", sa.String(1000), nullable=True),
        sa.Column("storage_backend", sa.String(50), nullable=True),
        sa.Column("file_format", sa.String(30), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        _actor_column("created_by"), _actor_column("updated_by"), _actor_column("published_by"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        _actor_column("deleted_by"), sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_dataset_id"], ["derived_datasets.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "name", "version_number", name="uq_derived_dataset_version"),
    )
    op.create_index("ix_derived_datasets_project_id", "derived_datasets", ["project_id"])
    for column in ("created_by", "updated_by", "published_by", "deleted_by"):
        _actor_fk("derived_datasets", column)

    op.create_table(
        "run_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("step_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "step_key", name="uq_run_steps_key"),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_steps_run_id", table_name="run_steps")
    op.drop_table("run_steps")
    for table in ("derived_datasets", "reports", "rollforward_steps", "rollforward_jobs", "rollforward_templates"):
        op.drop_table(table)

    op.drop_constraint("fk_projection_sets_updated_by_users", "projection_sets", type_="foreignkey")
    op.drop_column("projection_sets", "updated_by")

    for column in ("updated_by", "created_by"):
        op.drop_constraint(
            f"fk_model_variable_definitions_{column}_users",
            "model_variable_definitions", type_="foreignkey",
        )
        op.drop_column("model_variable_definitions", column)

    for column in ("deleted_by", "published_by", "updated_by", "created_by"):
        op.drop_constraint(f"fk_model_versions_{column}_users", "model_versions", type_="foreignkey")
    op.drop_constraint("fk_model_versions_parent_version", "model_versions", type_="foreignkey")
    for column in (
        "deleted_at", "published_at", "deleted_by", "published_by", "updated_by", "created_by",
        "configuration", "change_summary", "parent_version_id", "version_number",
    ):
        op.drop_column("model_versions", column)

    for column in ("deleted_by", "updated_by", "created_by"):
        op.drop_constraint(f"fk_models_{column}_users", "models", type_="foreignkey")
    for column in ("deleted_at", "deleted_by", "updated_by", "created_by"):
        op.drop_column("models", column)

    for table in ("formula_registry", "variable_registry"):
        op.drop_constraint(f"fk_{table}_deleted_by_users", table, type_="foreignkey")
        op.drop_column(table, "deleted_at")
        op.drop_column(table, "deleted_by")
