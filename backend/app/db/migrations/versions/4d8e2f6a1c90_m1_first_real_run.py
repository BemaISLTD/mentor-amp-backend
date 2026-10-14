"""m1_first_real_run: models, projection sets, run sets, run events, and M1 columns

Revision ID: 4d8e2f6a1c90
Revises: 2f6d51e920a4
Create Date: 2026-09-30

Adds the M1 modelling and execution layer described in
docs/build/02_BACKEND_FRONTEND_CONTRACT.md Part D.2.

Note: the run manifest is stored on `runs` (columns `manifest`, `manifest_fingerprint`)
rather than in a `run_manifests` table, because Noah's `dev` branch creates that table in
migration 91b4e26d7fa0. M2 convergence will reconcile the two.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "4d8e2f6a1c90"
down_revision: Union[str, None] = "2f6d51e920a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    # --- new tables -----------------------------------------------------------------------
    op.create_table(
        "models",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        *_timestamps(),
        sa.UniqueConstraint("project_id", "name", name="uq_models_project_name"),
    )
    op.create_index("ix_models_project_id", "models", ["project_id"])

    op.create_table(
        "model_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_label", sa.String(30), nullable=False),
        sa.Column("block_name", sa.String(255), nullable=True),
        sa.Column("profile_name", sa.String(255), nullable=True),
        sa.Column("basis", sa.String(100), nullable=False),
        sa.Column("methodology", sa.String(100), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("illustrative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("model_id", "version_label", name="uq_model_versions_label"),
    )
    op.create_index("ix_model_versions_model_id", "model_versions", ["model_id"])

    op.create_table(
        "formula_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("lineage_state", sa.String(20), nullable=False, server_default="local"),
        sa.Column("version_label", sa.String(30), nullable=False, server_default="v1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_formula_groups_model_version_id", "formula_groups", ["model_version_id"])

    op.create_table(
        "model_published_outputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variable_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("dimension", sa.String(255), nullable=True),
        sa.Column("aggregation", sa.String(20), nullable=False, server_default="sum"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("model_version_id", "variable_name", name="uq_published_outputs_var"),
    )
    op.create_index("ix_model_published_outputs_model_version_id", "model_published_outputs", ["model_version_id"])

    op.create_table(
        "projection_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version_label", sa.String(30), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("inforce_file_ids", sa.JSON(), nullable=False),
        sa.Column("assumption_table_ids", sa.JSON(), nullable=False),
        sa.Column("scenario_ids", sa.JSON(), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("horizon_months", sa.Integer(), nullable=False),
        sa.Column("time_step", sa.String(20), nullable=False, server_default="monthly"),
        sa.Column("output_variables", sa.JSON(), nullable=False),
        sa.Column("trace_scope", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("project_id", "name", "version_label", name="uq_projection_sets_name_version"),
    )
    op.create_index("ix_projection_sets_project_id", "projection_sets", ["project_id"])

    op.create_table(
        "run_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("projection_set_ids", sa.JSON(), nullable=False),
        sa.Column("scenario_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_sets_project_id", "run_sets", ["project_id"])

    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("level", sa.String(10), nullable=False, server_default="info"),
        sa.Column("step", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])

    # --- new columns on existing tables -----------------------------------------------------
    op.add_column("runs", sa.Column("name", sa.String(255), nullable=True))
    op.add_column("runs", sa.Column("run_set_id", sa.String(36), sa.ForeignKey("run_sets.id", ondelete="SET NULL"), nullable=True))
    op.add_column("runs", sa.Column("projection_set_id", sa.String(36), sa.ForeignKey("projection_sets.id", ondelete="SET NULL"), nullable=True))
    op.add_column("runs", sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True))
    op.add_column("runs", sa.Column("scenario_id", sa.String(36), nullable=True))
    op.add_column("runs", sa.Column("valuation_date", sa.Date(), nullable=True))
    op.add_column("runs", sa.Column("horizon_months", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("progress_done", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("summary", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("manifest", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("manifest_fingerprint", sa.String(64), nullable=True))
    op.add_column("runs", sa.Column("illustrative", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("runs", sa.Column("triggered_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_runs_run_set_id", "runs", ["run_set_id"])

    op.add_column("formula_registry", sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True))
    op.add_column("formula_registry", sa.Column("group_id", sa.String(36), sa.ForeignKey("formula_groups.id", ondelete="SET NULL"), nullable=True))
    op.add_column("formula_registry", sa.Column("expression_text", sa.Text(), nullable=True))
    op.add_column("formula_registry", sa.Column("explanation", sa.Text(), nullable=True))
    op.add_column("formula_registry", sa.Column("unit", sa.String(50), nullable=True))
    op.add_column("formula_registry", sa.Column("illustrative", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_formula_registry_model_version_id", "formula_registry", ["model_version_id"])

    op.add_column("variable_registry", sa.Column("unit", sa.String(50), nullable=True))
    op.add_column("variable_registry", sa.Column("source", sa.JSON(), nullable=True))

    for table in ("inforce_files", "assumption_tables", "scenario_tables"):
        op.add_column(table, sa.Column("status", sa.String(30), nullable=False, server_default="validated"))
        op.add_column(table, sa.Column("version_label", sa.String(30), nullable=True))
        op.add_column(table, sa.Column("fingerprint", sa.String(64), nullable=True))
    op.add_column("inforce_files", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("assumption_tables", sa.Column("value_column", sa.String(100), nullable=True))
    op.add_column("assumption_tables", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("scenario_tables", sa.Column("scenario_type", sa.String(30), nullable=False, server_default="deterministic"))
    op.add_column("scenario_tables", sa.Column("as_of_date", sa.Date(), nullable=True))
    op.add_column("scenario_tables", sa.Column("path_count", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("scenario_tables", "path_count")
    op.drop_column("scenario_tables", "as_of_date")
    op.drop_column("scenario_tables", "scenario_type")
    op.drop_column("assumption_tables", "description")
    op.drop_column("assumption_tables", "value_column")
    op.drop_column("inforce_files", "description")
    for table in ("inforce_files", "assumption_tables", "scenario_tables"):
        op.drop_column(table, "fingerprint")
        op.drop_column(table, "version_label")
        op.drop_column(table, "status")

    op.drop_column("variable_registry", "source")
    op.drop_column("variable_registry", "unit")

    op.drop_index("ix_formula_registry_model_version_id", table_name="formula_registry")
    for column in ("illustrative", "unit", "explanation", "expression_text", "group_id", "model_version_id"):
        op.drop_column("formula_registry", column)

    op.drop_index("ix_runs_run_set_id", table_name="runs")
    for column in (
        "triggered_by", "illustrative", "manifest_fingerprint", "manifest", "summary",
        "warning_count", "error_count", "progress_done", "progress_total", "horizon_months",
        "valuation_date", "scenario_id", "model_version_id", "projection_set_id", "run_set_id", "name",
    ):
        op.drop_column("runs", column)

    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_run_sets_project_id", table_name="run_sets")
    op.drop_table("run_sets")
    op.drop_index("ix_projection_sets_project_id", table_name="projection_sets")
    op.drop_table("projection_sets")
    op.drop_index("ix_model_published_outputs_model_version_id", table_name="model_published_outputs")
    op.drop_table("model_published_outputs")
    op.drop_index("ix_formula_groups_model_version_id", table_name="formula_groups")
    op.drop_table("formula_groups")
    op.drop_index("ix_model_versions_model_id", table_name="model_versions")
    op.drop_table("model_versions")
    op.drop_index("ix_models_project_id", table_name="models")
    op.drop_table("models")
