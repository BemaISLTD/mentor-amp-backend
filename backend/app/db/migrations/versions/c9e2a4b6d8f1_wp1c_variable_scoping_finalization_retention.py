"""wp1 correction: model-scoped variables, finalization outcome, run-set resolution,
inforce fingerprint v2, retained run evidence

Revision ID: c9e2a4b6d8f1
Revises: b7e4d2a9c613
Create Date: 2026-09-30

Forward-only correction of Work Package 1 (b7e4d2a9c613 is already shared; it is not edited).

Schema
  model_variable_definitions  NEW. How one model version resolves one semantic variable
                              (source rule, type, unit, default, allow_scenario_override).
                              UNIQUE (model_version_id, variable_name); variable_name references
                              the semantic catalog variable_registry.name (RESTRICT).
  run_attempts                + intended_outcome (the terminal outcome, recorded before promotion)
  run_sets                    + resolution (how the submitted scenarios were resolved);
                              scenario_ids now means "scenarios actually submitted"
  inforce_files               + fingerprint_scheme (inforce-v1 | inforce-v2)
  foreign keys                run_packages.run_id / project_id, run_manifests.run_id /
                              run_package_id, run_attempts.run_id: CASCADE -> RESTRICT
  triggers (PostgreSQL)       run_packages and run_manifests refuse DELETE unless the session
                              sets mentoramp.allow_evidence_deletion = 'on' (future retention
                              workflow); the WP1 write-once UPDATE triggers stay.

Data
  - Variable definitions are backfilled for every model version that has formulas: the variables
    its formulas, published outputs and Projection Set outputs need (following lookup keys, prior
    outputs and valuation references), copied from variable_registry. allow_scenario_override is
    true only for variables that a scenario of the same project already overrides (so existing
    validated scenarios keep working); everything else is not overridable. Names missing from the
    registry are not invented — validation reports them as VARIABLE_NOT_DEFINED.
  - Inforce fingerprints: a file whose stored (v1) fingerprint still verifies against its records
    and whose policy IDs are unique is re-fingerprinted with inforce-v2. Otherwise its scheme stays
    inforce-v1 and a validated/approved file becomes needs_review (it must be re-validated).
  - Run sets: scenario_ids and resolution are backfilled from their child runs.

Downgrade restores CASCADE, drops the new columns/table/triggers, restores v1 fingerprints for
files upgraded to v2 and the requested scenario_ids of run sets. Status changes to needs_review
are not reverted (the downgraded code would treat those files as validated otherwise).
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c9e2a4b6d8f1"
down_revision: Union[str, None] = "b7e4d2a9c613"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVIDENCE_FKS = (
    ("run_packages", "run_id", "runs"),
    ("run_packages", "project_id", "projects"),
    ("run_manifests", "run_id", "runs"),
    ("run_manifests", "run_package_id", "run_packages"),
    ("run_attempts", "run_id", "runs"),
)
CALCULATED_KINDS = ("formula", "valuation", "output")


# --- canonical fingerprints (self-contained copy of app.core.execution.fingerprints) ----------

def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sequence_fingerprint(items) -> str:
    digest = hashlib.sha256(b"[")
    for index, item in enumerate(items):
        if index:
            digest.update(b",")
        digest.update(_canonical(item).encode("utf-8"))
    digest.update(b"]")
    return digest.hexdigest()


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


# --- helpers ------------------------------------------------------------------------------------

def _replace_fks(bind, ondelete: str) -> None:
    inspector = sa.inspect(bind)
    for table, column, referred in EVIDENCE_FKS:
        for fk in inspector.get_foreign_keys(table):
            if fk["constrained_columns"] == [column] and fk["referred_table"] == referred:
                op.drop_constraint(fk["name"], table, type_="foreignkey")
                op.create_foreign_key(fk["name"], table, referred, [column], ["id"], ondelete=ondelete)


def _referenced(source: dict) -> set[str]:
    names = set((source.get("key_map") or {}).values())
    for key in ("variable", "cash_flow", "rate", "consistency_sum_of"):
        if source.get(key):
            names.add(str(source[key]))
    return names


def _legacy_source(row) -> dict:
    source = _json(row["source"])
    if source:
        return dict(source)
    derived = {"type": row["source_type"]}
    if row["source_table"]:
        derived["table"] = row["source_table"]
    lookup_keys = _json(row["lookup_keys"]) or []
    if lookup_keys:
        derived["key_map"] = {key: key for key in lookup_keys}
    if row["source_type"] == "input":
        derived["column"] = row["name"]
    return derived


def _backfill_definitions(bind) -> None:
    registry = {
        row["name"]: row
        for row in bind.execute(sa.text(
            "SELECT name, display_name, description, data_type, source_type, source_table, "
            "lookup_keys, default_value, required, unit, source, version FROM variable_registry"
        )).mappings()
    }
    versions = bind.execute(sa.text(
        "SELECT DISTINCT fr.model_version_id, m.project_id FROM formula_registry fr "
        "JOIN model_versions mv ON mv.id = fr.model_version_id JOIN models m ON m.id = mv.model_id"
    )).all()
    definitions = sa.table(
        "model_variable_definitions",
        *(sa.column(name) for name in (
            "id", "model_version_id", "variable_name", "display_name", "description", "kind",
            "data_type", "unit", "required", "version")),
        sa.column("default_value", sa.JSON), sa.column("source", sa.JSON),
        sa.column("allow_scenario_override", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    for version_id, project_id in versions:
        wanted: set[str] = set()
        for output, dependency in bind.execute(sa.text(
            "SELECT fr.output_variable, fd.depends_on_variable FROM formula_registry fr "
            "LEFT JOIN formula_dependencies fd ON fd.formula_id = fr.id WHERE fr.model_version_id = :v"
        ), {"v": version_id}):
            wanted.add(output)
            if dependency:
                wanted.add(dependency)
        wanted.update(bind.execute(sa.text(
            "SELECT variable_name FROM model_published_outputs WHERE model_version_id = :v"
        ), {"v": version_id}).scalars())
        for outputs in bind.execute(sa.text(
            "SELECT output_variables FROM projection_sets WHERE model_version_id = :v"
        ), {"v": version_id}).scalars():
            wanted.update(_json(outputs) or [])
        closure: dict[str, dict] = {}
        pending = sorted(wanted)
        while pending:
            name = pending.pop()
            if name in closure or name not in registry:
                continue
            source = _legacy_source(registry[name])
            closure[name] = source
            pending.extend(sorted(_referenced(source) - closure.keys()))
        overridden: set[str] = set()
        for overrides in bind.execute(sa.text(
            "SELECT st.overrides FROM scenario_tables st JOIN scenario_sets ss ON ss.id = st.set_id "
            "WHERE ss.project_id = :p"
        ), {"p": project_id}).scalars():
            overridden.update(item.get("target_variable") for item in _json(overrides) or [] if isinstance(item, dict))
        rows = []
        for name in sorted(closure):
            row = registry[name]
            default = _json(row["default_value"])
            if isinstance(default, dict):
                default = default.get("value")
            kind = row["source_type"]
            rows.append({
                "id": str(uuid.uuid4()), "model_version_id": version_id, "variable_name": name,
                "display_name": row["display_name"], "description": row["description"], "kind": kind,
                "data_type": row["data_type"], "unit": row["unit"],
                "required": bool(row["required"]) if row["required"] is not None else True,
                "version": row["version"] or "v1", "default_value": default, "source": closure[name],
                "allow_scenario_override": name in overridden and kind not in CALCULATED_KINDS
                and closure[name].get("type") not in CALCULATED_KINDS,
                "created_at": now, "updated_at": now,
            })
        if rows:
            bind.execute(definitions.insert(), rows)


def _upgrade_inforce_fingerprints(bind) -> None:
    files = bind.execute(sa.text(
        "SELECT id, fingerprint, status FROM inforce_files WHERE fingerprint IS NOT NULL"
    )).mappings().all()
    for file in files:
        records = bind.execute(sa.text(
            "SELECT id, policy_id, data FROM inforce_records WHERE file_id = :f"
        ), {"f": file["id"]}).mappings().all()
        ordered = sorted(records, key=lambda record: (record["policy_id"], record["id"]))
        v1 = _sequence_fingerprint(dict(_json(record["data"]) or {}) for record in ordered)
        policy_ids = [record["policy_id"] for record in ordered]
        if v1 == file["fingerprint"] and len(set(policy_ids)) == len(policy_ids):
            v2 = _sequence_fingerprint(
                {"policy_id": record["policy_id"], "data": dict(_json(record["data"]) or {})}
                for record in ordered
            )
            bind.execute(sa.text(
                "UPDATE inforce_files SET fingerprint = :fp, fingerprint_scheme = 'inforce-v2' WHERE id = :id"
            ), {"fp": v2, "id": file["id"]})
        else:
            status = "needs_review" if file["status"] in ("validated", "approved") else file["status"]
            bind.execute(sa.text(
                "UPDATE inforce_files SET fingerprint_scheme = 'inforce-v1', status = :s WHERE id = :id"
            ), {"s": status, "id": file["id"]})


def _backfill_run_sets(bind) -> None:
    for run_set_id, requested in bind.execute(sa.text("SELECT id, scenario_ids FROM run_sets")).all():
        requested = list(_json(requested) or [])
        runs = bind.execute(sa.text(
            "SELECT id, projection_set_id, scenario_id FROM runs WHERE run_set_id = :s ORDER BY created_at, name"
        ), {"s": run_set_id}).all()
        resolved = list(dict.fromkeys(scenario for _run, _ps, scenario in runs if scenario))
        by_projection_set: dict[str, dict] = {}
        for run_id, projection_set_id, scenario_id in runs:
            entry = by_projection_set.setdefault(projection_set_id, {
                "projection_set_id": projection_set_id,
                "scenario_source": "request" if requested else "projection_set",
                "scenario_ids": [], "runs": [],
            })
            if scenario_id and scenario_id not in entry["scenario_ids"]:
                entry["scenario_ids"].append(scenario_id)
            entry["runs"].append({"run_id": run_id, "scenario_id": scenario_id})
        resolution = {"requested_scenario_ids": requested, "backfilled_from_runs": True,
                      "projection_sets": list(by_projection_set.values())}
        bind.execute(sa.text(
            "UPDATE run_sets SET scenario_ids = CAST(:ids AS json), resolution = CAST(:res AS json) WHERE id = :id"
        ), {"ids": json.dumps(resolved), "res": json.dumps(resolution), "id": run_set_id})


# --- upgrade / downgrade ------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "model_variable_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variable_name", sa.String(255), sa.ForeignKey("variable_registry.name", ondelete="RESTRICT"), nullable=False),
        sa.Column("display_name", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("data_type", sa.String(50), nullable=False),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_value", sa.JSON(), nullable=True),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("allow_scenario_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_version_id", "variable_name", name="uq_model_variable_definitions_name"),
    )
    op.create_index("ix_model_variable_definitions_model_version_id", "model_variable_definitions", ["model_version_id"])
    _backfill_definitions(bind)

    op.add_column("run_attempts", sa.Column("intended_outcome", sa.JSON(), nullable=True))
    op.add_column("run_sets", sa.Column("resolution", sa.JSON(), nullable=True))
    _backfill_run_sets(bind)
    op.add_column("inforce_files", sa.Column("fingerprint_scheme", sa.String(20), nullable=True))
    _upgrade_inforce_fingerprints(bind)

    _replace_fks(bind, "RESTRICT")
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION mentoramp_refuse_evidence_delete() RETURNS trigger AS $$
            BEGIN
                IF coalesce(current_setting('mentoramp.allow_evidence_deletion', true), '') = 'on' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION '% rows are retained run evidence; deleting them requires the retention workflow',
                    TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in ("run_packages", "run_manifests"):
            op.execute(
                f"CREATE TRIGGER {table}_retained BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION mentoramp_refuse_evidence_delete()"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("run_packages", "run_manifests"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_retained ON {table}")
        op.execute("DROP FUNCTION IF EXISTS mentoramp_refuse_evidence_delete()")
    _replace_fks(bind, "CASCADE")

    for file_id in bind.execute(sa.text(
        "SELECT id FROM inforce_files WHERE fingerprint_scheme = 'inforce-v2'"
    )).scalars().all():
        records = bind.execute(sa.text(
            "SELECT id, policy_id, data FROM inforce_records WHERE file_id = :f"
        ), {"f": file_id}).mappings().all()
        ordered = sorted(records, key=lambda record: (record["policy_id"], record["id"]))
        v1 = _sequence_fingerprint(dict(_json(record["data"]) or {}) for record in ordered)
        bind.execute(sa.text("UPDATE inforce_files SET fingerprint = :fp WHERE id = :id"), {"fp": v1, "id": file_id})
    op.drop_column("inforce_files", "fingerprint_scheme")

    for run_set_id, resolution in bind.execute(sa.text(
        "SELECT id, resolution FROM run_sets WHERE resolution IS NOT NULL"
    )).all():
        requested = (_json(resolution) or {}).get("requested_scenario_ids") or []
        bind.execute(sa.text("UPDATE run_sets SET scenario_ids = CAST(:ids AS json) WHERE id = :id"),
                     {"ids": json.dumps(requested), "id": run_set_id})
    op.drop_column("run_sets", "resolution")
    op.drop_column("run_attempts", "intended_outcome")
    op.drop_index("ix_model_variable_definitions_model_version_id", table_name="model_variable_definitions")
    op.drop_table("model_variable_definitions")
