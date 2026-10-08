"""wp1: immutable run packages, run attempts, final manifests, factor pinning, project access

Revision ID: b7e4d2a9c613
Revises: a3c5e7f9b1d2
Create Date: 2026-09-30

Schema
  new tables
    run_packages      frozen, fingerprinted inputs of each run (write-once; UPDATE refused)
    run_attempts      one row per execution attempt; UNIQUE (run_id, attempt_number)
    project_members   which users may access which project (owner / editor / viewer)
  runs              + run_package_fingerprint, attempt_count, accepted_attempt_number,
                      final_manifest_fingerprint, CHECK (status IN legal statuses)
                    - manifest, manifest_fingerprint (moved to run_manifests, see below)
  run_outputs,
  trace_logs        + attempt_number (existing rows = attempt 1)
  trace_logs        - foreign key formula_id -> formula_registry (trace rows are write-once
                      evidence; deleting a formula must not rewrite them)
  run_manifests     + schema_version, run_package_id, run_package_fingerprint, attempt_number
                      (Noah's table becomes the canonical final-manifest store; write-once)
  projection_sets   + factor_table_ids
  factor_tables     + status, version_label, fingerprint, value_column, description

Data (nothing is deleted)
  - Every existing run becomes a *legacy* run: it has no run package. Runs that started get
    attempt 1; success/partial_success runs get accepted_attempt_number = 1, so their existing
    result rows stay readable.
  - runs.manifest is copied into run_manifests (schema "mentoramp.m1_manifest/legacy"), with the
    original fingerprint kept inside the document, before the column is dropped.
  - Legacy runs still pending or running cannot execute any more (they have no run package), so
    they are marked failed with an explanatory run event.
  - Inputs that were never fingerprinted (created before M1 validation) become
    "legacy_unvalidated" instead of the "validated" that migration 4d8e2f6a1c90 gave them by
    default; new rows default to "uploaded" (data) or "draft" (scenarios).
  - Existing projects whose creator is recorded (projects.created_by, from 7c3f19ad0e82) get that
    user as owner.

Downgrade restores the previous columns (runs.manifest is copied back from run_manifests),
deletes result/trace rows of attempts that were never accepted (they are indistinguishable
once attempt_number is dropped), and restores the old status defaults.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b7e4d2a9c613"
down_revision: Union[str, None] = "a3c5e7f9b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUN_STATUSES = ("pending", "running", "success", "partial_success", "failed", "cancelled")
ATTEMPT_STATUSES = ("running", "success", "partial_success", "failed", "cancelled")
LEGACY_MANIFEST_SCHEMA = "mentoramp.m1_manifest/legacy"
WP1_MANIFEST_SCHEMA = "mentoramp.run_manifest/v1"


def _in_list(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upgrade() -> None:
    bind = op.get_bind()

    # --- new tables --------------------------------------------------------------------------
    op.create_table(
        "project_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        sa.CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="ck_project_members_role"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    op.create_table(
        "run_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("fingerprint_algorithm", sa.String(40), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        # Plain JSON (not JSONB): the document is stored exactly as written.
        sa.Column("package", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_packages_project_id", "run_packages", ["project_id"])
    op.create_index("ix_run_packages_fingerprint", "run_packages", ["fingerprint"])

    op.create_table(
        "run_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_reason", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("output_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trace_row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cleanup_status", sa.String(20), nullable=True),
        sa.Column("executed_build", sa.JSON(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "attempt_number", name="uq_run_attempts_run_number"),
        sa.CheckConstraint(f"status IN ({_in_list(ATTEMPT_STATUSES)})", name="ck_run_attempts_status"),
    )
    op.create_index("ix_run_attempts_run_id", "run_attempts", ["run_id"])

    # --- runs ----------------------------------------------------------------------------------
    op.add_column("runs", sa.Column("run_package_fingerprint", sa.String(64), nullable=True))
    op.add_column("runs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("accepted_attempt_number", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("final_manifest_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_runs_run_package_fingerprint", "runs", ["run_package_fingerprint"])

    # --- attempt-scoped result and trace rows ---------------------------------------------------
    # Trace rows are historical evidence: they keep the frozen package's formula ID as plain data.
    # The old foreign key (ON DELETE SET NULL) let a formula deletion rewrite past trace rows.
    # The parallel artifact-storage history may already have removed these legacy tables.
    # They are not recreated: new attempts persist analytical rows as artifacts.
    legacy_tables = all(sa.inspect(bind).has_table(table) for table in ("run_outputs", "trace_logs"))
    if legacy_tables:
        op.drop_constraint("trace_logs_formula_id_fkey", "trace_logs", type_="foreignkey")
        for table in ("run_outputs", "trace_logs"):
            op.add_column(table, sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"))
            op.alter_column(table, "attempt_number", server_default=None)

    # --- run_manifests (Noah's table) becomes the final-manifest store -----------------------
    op.add_column("run_manifests", sa.Column("schema_version", sa.String(64), nullable=True))
    op.add_column("run_manifests", sa.Column("run_package_id", sa.String(36), nullable=True))
    op.add_column("run_manifests", sa.Column("run_package_fingerprint", sa.String(64), nullable=True))
    op.add_column("run_manifests", sa.Column("attempt_number", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_run_manifests_run_package", "run_manifests", "run_packages",
        ["run_package_id"], ["id"], ondelete="CASCADE",
    )

    _migrate_legacy_runs(bind)

    op.drop_column("runs", "manifest_fingerprint")
    op.drop_column("runs", "manifest")

    invalid = bind.execute(sa.text(
        f"SELECT DISTINCT status FROM runs WHERE status IS NOT NULL AND status NOT IN ({_in_list(RUN_STATUSES)})"
    )).scalars().all()
    if invalid:
        raise RuntimeError(
            f"runs contains statuses outside the run state machine: {invalid}. "
            "Resolve them deliberately before applying this migration."
        )
    op.create_check_constraint("ck_runs_status", "runs", f"status IN ({_in_list(RUN_STATUSES)})")

    # --- factor pinning and factor lifecycle ------------------------------------------------------
    op.add_column("projection_sets", sa.Column("factor_table_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.alter_column("projection_sets", "factor_table_ids", server_default=None)
    op.add_column("factor_tables", sa.Column("status", sa.String(30), nullable=False, server_default="legacy_unvalidated"))
    op.alter_column("factor_tables", "status", server_default="uploaded")
    op.add_column("factor_tables", sa.Column("version_label", sa.String(30), nullable=True))
    op.add_column("factor_tables", sa.Column("fingerprint", sa.String(64), nullable=True))
    op.add_column("factor_tables", sa.Column("value_column", sa.String(100), nullable=True))
    op.add_column("factor_tables", sa.Column("description", sa.Text(), nullable=True))

    # --- data lifecycle: no input is "validated" merely because a column was added -------------
    for table, default in (("inforce_files", "uploaded"), ("assumption_tables", "uploaded"), ("scenario_tables", "draft")):
        op.alter_column(table, "status", server_default=default)
        bind.execute(sa.text(f"UPDATE {table} SET status = 'legacy_unvalidated' WHERE fingerprint IS NULL"))

    # --- project access -----------------------------------------------------------------------
    projects = bind.execute(sa.text(
        "SELECT id, created_by FROM projects WHERE created_by IS NOT NULL"
    )).all()
    if projects:
        members = sa.table(
            "project_members",
            sa.column("id", sa.String), sa.column("project_id", sa.String), sa.column("user_id", sa.String),
            sa.column("role", sa.String), sa.column("created_by", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
        )
        bind.execute(members.insert(), [
            {"id": str(uuid.uuid4()), "project_id": project_id, "user_id": user_id, "role": "owner",
             "created_by": None, "created_at": _now()}
            for project_id, user_id in projects
        ])

    # --- write-once enforcement in the database -------------------------------------------------
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION mentoramp_refuse_update() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% rows are immutable (write-once)', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in ("run_packages", "run_manifests"):
            op.execute(
                f"CREATE TRIGGER {table}_write_once BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION mentoramp_refuse_update()"
            )


def _migrate_legacy_runs(bind) -> None:
    runs = bind.execute(sa.text(
        "SELECT id, status, started_at, completed_at, manifest, manifest_fingerprint FROM runs"
    )).mappings().all()
    if not runs:
        return
    attempts = sa.table(
        "run_attempts",
        sa.column("id", sa.String), sa.column("run_id", sa.String), sa.column("attempt_number", sa.Integer),
        sa.column("status", sa.String), sa.column("worker_id", sa.String),
        sa.column("started_at", sa.DateTime(timezone=True)), sa.column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)), sa.column("error_type", sa.String),
        sa.column("error_message", sa.Text), sa.column("cleanup_status", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    manifests = sa.table(
        "run_manifests",
        sa.column("run_id", sa.String), sa.column("fingerprint", sa.String),
        sa.column("manifest", postgresql.JSONB), sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("schema_version", sa.String), sa.column("attempt_number", sa.Integer),
    )
    events = sa.table(
        "run_events",
        sa.column("run_id", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("level", sa.String), sa.column("step", sa.String), sa.column("message", sa.Text),
    )
    now = _now()
    for run in runs:
        status = run["status"] or "pending"
        started = run["started_at"] is not None
        if status in ("pending", "running"):
            reason = ("Legacy run submitted before run packages existed; it has no frozen inputs "
                      "and cannot be executed reproducibly (Work Package 1 migration).")
            bind.execute(sa.text(
                "UPDATE runs SET status = 'failed', completed_at = :now WHERE id = :id"
            ), {"now": now, "id": run["id"]})
            bind.execute(events.insert(), [{"run_id": run["id"], "created_at": now, "level": "error",
                                            "step": "failed", "message": reason}])
            status = "failed"
        if started:
            bind.execute(attempts.insert(), [{
                "id": str(uuid.uuid4()), "run_id": run["id"], "attempt_number": 1,
                "status": status if status in ATTEMPT_STATUSES else "failed",
                "worker_id": "legacy-m1", "started_at": run["started_at"],
                "heartbeat_at": run["completed_at"] or run["started_at"],
                "completed_at": run["completed_at"] or now,
                "error_type": None if status in ("success", "partial_success") else "legacy",
                "error_message": None, "cleanup_status": "not_needed", "created_at": now,
            }])
        accepted = 1 if started and status in ("success", "partial_success") else None
        existing_manifest = bind.execute(sa.text(
            "SELECT fingerprint FROM run_manifests WHERE run_id = :run_id"
        ), {"run_id": run["id"]}).scalar_one_or_none()
        final_fingerprint = existing_manifest
        if run["manifest"] is not None and existing_manifest is None:
            document = run["manifest"]
            if isinstance(document, str):
                document = json.loads(document)
            document = dict(document)
            document["legacy"] = {
                "note": "M1 manifest migrated by Work Package 1. Inputs were not frozen at "
                        "submission; this run cannot be reproduced from a run package.",
                "original_manifest_fingerprint": run["manifest_fingerprint"],
            }
            final_fingerprint = hashlib.sha256(
                f"{run['id']}:{run['manifest_fingerprint'] or ''}:{LEGACY_MANIFEST_SCHEMA}".encode("utf-8")
            ).hexdigest()
            bind.execute(manifests.insert(), [{
                "run_id": run["id"], "fingerprint": final_fingerprint, "manifest": document,
                "created_at": run["completed_at"] or now, "schema_version": LEGACY_MANIFEST_SCHEMA,
                "attempt_number": 1 if started else None,
            }])
        bind.execute(sa.text(
            "UPDATE runs SET attempt_count = :count, accepted_attempt_number = :accepted, "
            "final_manifest_fingerprint = :fingerprint WHERE id = :id"
        ), {"count": 1 if started else 0, "accepted": accepted, "fingerprint": final_fingerprint,
            "id": run["id"]})


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("run_packages", "run_manifests"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_write_once ON {table}")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_refuse_update()")

    for table in ("inforce_files", "assumption_tables", "scenario_tables"):
        bind.execute(sa.text(f"UPDATE {table} SET status = 'validated' WHERE status = 'legacy_unvalidated'"))
        op.alter_column(table, "status", server_default="validated")
    for column in ("description", "value_column", "fingerprint", "version_label", "status"):
        op.drop_column("factor_tables", column)
    op.drop_column("projection_sets", "factor_table_ids")

    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.add_column("runs", sa.Column("manifest", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("manifest_fingerprint", sa.String(64), nullable=True))
    bind.execute(sa.text(
        "UPDATE runs SET manifest = (rm.manifest - 'legacy')::json, "
        "manifest_fingerprint = COALESCE(rm.run_package_fingerprint, "
        "rm.manifest->'legacy'->>'original_manifest_fingerprint') "
        "FROM run_manifests rm WHERE rm.run_id = runs.id"
    ))
    bind.execute(sa.text(
        "DELETE FROM run_manifests WHERE schema_version IN (:legacy, :wp1)"
    ), {"legacy": LEGACY_MANIFEST_SCHEMA, "wp1": WP1_MANIFEST_SCHEMA})
    op.drop_constraint("fk_run_manifests_run_package", "run_manifests", type_="foreignkey")
    for column in ("attempt_number", "run_package_fingerprint", "run_package_id", "schema_version"):
        op.drop_column("run_manifests", column)

    if all(sa.inspect(bind).has_table(table) for table in ("run_outputs", "trace_logs")):
        for table in ("run_outputs", "trace_logs"):
            bind.execute(sa.text(
                f"DELETE FROM {table} t USING runs r WHERE t.run_id = r.id "
                "AND (r.accepted_attempt_number IS NULL OR t.attempt_number <> r.accepted_attempt_number)"
            ))
            op.drop_column(table, "attempt_number")
        bind.execute(sa.text(
            "UPDATE trace_logs SET formula_id = NULL WHERE formula_id IS NOT NULL "
            "AND formula_id NOT IN (SELECT id FROM formula_registry)"
        ))
        op.create_foreign_key(
            "trace_logs_formula_id_fkey", "trace_logs", "formula_registry",
            ["formula_id"], ["id"], ondelete="SET NULL",
        )

    op.drop_index("ix_runs_run_package_fingerprint", table_name="runs")
    for column in ("final_manifest_fingerprint", "accepted_attempt_number", "attempt_count", "run_package_fingerprint"):
        op.drop_column("runs", column)

    op.drop_index("ix_run_attempts_run_id", table_name="run_attempts")
    op.drop_table("run_attempts")
    op.drop_index("ix_run_packages_fingerprint", table_name="run_packages")
    op.drop_index("ix_run_packages_project_id", table_name="run_packages")
    op.drop_table("run_packages")
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_table("project_members")
