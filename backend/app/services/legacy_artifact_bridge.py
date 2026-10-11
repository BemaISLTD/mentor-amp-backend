"""Verified two-stage bridge from M1 relational evidence to canonical Parquet.

The operator first upgrades only to e2b5d8f0c316. At that point the legacy
tables and run_artifacts coexist. This module locks both source tables,
writes attempt-1 Parquet, registers its metadata, rereads and verifies it, and
only then deletes the relational rows in the same database transaction. The
existing destructive migration still refuses any populated source table, so a
plain upgrade cannot silently discard M1 evidence.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, inspect, text

from app.core.artifacts import LocalParquetArtifactStore, StoredArtifact

PRE_DROP_REVISION = "e2b5d8f0c316"
BRIDGE_SCHEMA_VERSION = "m1-v1"


@dataclass(frozen=True)
class LegacyExportReport:
    output_rows: int
    trace_rows: int
    artifacts: int
    run_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Export:
    run_id: str
    artifact_type: str
    rows: list[dict[str, Any]]
    source_sha256: str
    stored: StoredArtifact


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _rows_sha256(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_json_text(rows).encode("utf-8")).hexdigest()


def _output_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "attempt_number": 1,
        "policy_id": row["policy_id"],
        "scenario_id": row["scenario_id"],
        "projection_month": row["projection_month"],
        "variable_name": row["variable_name"],
        "value_json": _json_text(row["value"]),
        "product": row["product"],
    }


def _trace_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "attempt_number": 1,
        "policy_id": row["policy_id"],
        "scenario_id": row["scenario_id"],
        "projection_month": row["projection_month"],
        "variable_name": row["variable_name"],
        "formula_id": row["formula_id"],
        "input_values_json": _json_text(row["input_values"]),
        "output_value_json": _json_text(row["output_value"]),
        "source_type": row["source_type"],
        "source_table": row["source_table"],
        "lookup_keys_json": _json_text(row["lookup_keys"]),
        "error_message": row["error_message"],
    }


def _read_sources(connection) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    outputs = connection.execute(text(
        "SELECT id, run_id, policy_id, scenario_id, projection_month, variable_name, "
        "value, product FROM run_outputs ORDER BY run_id, id"
    )).mappings()
    for row in outputs:
        converted = _output_row(dict(row))
        grouped[(converted["run_id"], "outputs")].append(converted)

    traces = connection.execute(text(
        "SELECT id, run_id, policy_id, scenario_id, projection_month, formula_id, "
        "variable_name, input_values, output_value, source_type, source_table, "
        "lookup_keys, error_message FROM trace_logs ORDER BY run_id, id"
    )).mappings()
    for row in traces:
        converted = _trace_row(dict(row))
        grouped[(converted["run_id"], "traces")].append(converted)
    return grouped


def _verify_file(store: LocalParquetArtifactStore, export: _Export) -> None:
    checksum = hashlib.sha256(store.read_bytes(export.stored.uri)).hexdigest()
    if checksum != export.stored.checksum_sha256:
        raise RuntimeError(f"Checksum verification failed for {export.stored.uri}.")
    rows = store.read_rows(export.stored.uri)
    if len(rows) != len(export.rows):
        raise RuntimeError(f"Row-count verification failed for {export.stored.uri}.")
    if _rows_sha256(rows) != export.source_sha256:
        raise RuntimeError(f"Content verification failed for {export.stored.uri}.")


def export_legacy_run_artifacts(
    engine: Engine,
    store: LocalParquetArtifactStore,
) -> LegacyExportReport:
    """Export, register, verify, then remove legacy result and trace rows."""
    tables = set(inspect(engine).get_table_names())
    required = {"run_outputs", "trace_logs", "run_artifacts"}
    if not required <= tables:
        missing = ", ".join(sorted(required - tables))
        raise RuntimeError(
            "Legacy export must run at the pre-drop migration revision; "
            f"missing table(s): {missing or 'none'}."
        )

    created: list[_Export] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    try:
        with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                # Prevent M1 workers from appending rows between snapshot and deletion.
                connection.execute(text(
                    "LOCK TABLE run_outputs, trace_logs IN SHARE ROW EXCLUSIVE MODE"
                ))
            grouped = _read_sources(connection)
            for (run_id, artifact_type), rows in sorted(grouped.items()):
                source_sha256 = _rows_sha256(rows)
                stored = store.write_rows(run_id, artifact_type, rows)
                export = _Export(run_id, artifact_type, rows, source_sha256, stored)
                _verify_file(store, export)
                created.append(export)
                partition = {
                    "attempt_number": 1,
                    "legacy_bridge": True,
                    "source_rows_sha256": source_sha256,
                }
                connection.execute(text(
                    "INSERT INTO run_artifacts "
                    "(id, run_id, artifact_type, storage_uri, storage_backend, file_format, "
                    "schema_version, row_count, checksum_sha256, partition, created_at) "
                    "VALUES (:id, :run_id, :artifact_type, :uri, 'local', 'parquet', "
                    ":schema_version, :row_count, :checksum, CAST(:partition AS json), "
                    "CURRENT_TIMESTAMP)"
                ), {
                    "id": str(uuid.uuid4()),
                    "run_id": run_id,
                    "artifact_type": artifact_type,
                    "uri": stored.uri,
                    "schema_version": BRIDGE_SCHEMA_VERSION,
                    "row_count": stored.row_count,
                    "checksum": stored.checksum_sha256,
                    "partition": _json_text(partition),
                })

            # Check registered metadata and reread every file before source deletion.
            for export in created:
                registered = connection.execute(text(
                    "SELECT row_count, checksum_sha256, partition FROM run_artifacts "
                    "WHERE storage_uri = :uri"
                ), {"uri": export.stored.uri}).mappings().one()
                partition = registered["partition"]
                if isinstance(partition, str):
                    partition = json.loads(partition)
                if (
                    registered["row_count"] != len(export.rows)
                    or registered["checksum_sha256"] != export.stored.checksum_sha256
                    or partition.get("source_rows_sha256") != export.source_sha256
                ):
                    raise RuntimeError(
                        f"Registered evidence verification failed for {export.stored.uri}."
                    )
                _verify_file(store, export)

            connection.execute(text("DELETE FROM trace_logs"))
            connection.execute(text("DELETE FROM run_outputs"))
    except Exception:
        for export in created:
            store.path_for_uri(export.stored.uri).unlink(missing_ok=True)
        raise

    runs = tuple(sorted({run_id for run_id, _kind in grouped}))
    return LegacyExportReport(
        output_rows=sum(
            len(rows) for (_run, kind), rows in grouped.items() if kind == "outputs"
        ),
        trace_rows=sum(
            len(rows) for (_run, kind), rows in grouped.items() if kind == "traces"
        ),
        artifacts=len(created),
        run_ids=runs,
    )
