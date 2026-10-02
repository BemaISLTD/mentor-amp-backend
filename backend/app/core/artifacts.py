"""Configurable analytical artifact storage and buffered Parquet writer."""

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models.run_artifact import RunArtifact
from app.models.schemas import ProjectionContext, ProjectionSummary, VariableResolutionResult


@dataclass(frozen=True)
class StoredArtifact:
    uri: str
    row_count: int
    checksum_sha256: str


class LocalParquetArtifactStore:
    backend_name = "local"

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_component(value: str) -> str:
        if value not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            return value
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def write_rows(
        self,
        run_id: str,
        artifact_type: str,
        rows: list[dict[str, Any]],
    ) -> StoredArtifact:
        if not rows:
            raise ValueError("Cannot write an empty artifact.")
        relative = Path(self._safe_component(run_id)) / artifact_type / f"{uuid.uuid4()}.parquet"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        try:
            pl.DataFrame(rows).write_parquet(temporary, compression="zstd")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
        return StoredArtifact(
            uri=f"local://{relative.as_posix()}",
            row_count=len(rows),
            checksum_sha256=checksum,
        )

    def read_rows(self, uri: str) -> list[dict[str, Any]]:
        prefix = "local://"
        if not uri.startswith(prefix):
            raise ValueError(f"Unsupported local artifact URI: {uri}")
        path = (self.root / uri.removeprefix(prefix)).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Artifact URI escapes the configured storage root.")
        return pl.read_parquet(path).to_dicts()


def get_artifact_store() -> LocalParquetArtifactStore:
    if settings.artifact_storage_backend != "local":
        raise ValueError(
            f"Unsupported artifact storage backend: {settings.artifact_storage_backend}"
        )
    return LocalParquetArtifactStore(settings.artifact_storage_path)


def read_artifact_rows(
    db: Session,
    run_id: str,
    artifact_type: str,
    store: LocalParquetArtifactStore | None = None,
) -> list[dict[str, Any]]:
    configured_store = store or get_artifact_store()
    artifacts = (
        db.query(RunArtifact)
        .filter(
            RunArtifact.run_id == run_id,
            RunArtifact.artifact_type == artifact_type,
        )
        .order_by(RunArtifact.created_at, RunArtifact.id)
        .all()
    )
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.storage_backend != configured_store.backend_name:
            raise ValueError(
                f"Artifact backend '{artifact.storage_backend}' is not configured."
            )
        rows.extend(configured_store.read_rows(artifact.storage_uri))
    return rows


def read_attempt_artifact_rows(
    db: Session,
    run_id: str,
    artifact_type: str,
    attempt_number: int | None,
    store: LocalParquetArtifactStore | None = None,
) -> list[dict[str, Any]]:
    """Read only an accepted attempt; None means no analytical results are visible."""
    if attempt_number is None:
        return []
    configured_store = store or get_artifact_store()
    artifacts = (
        db.query(RunArtifact)
        .filter(
            RunArtifact.run_id == run_id,
            RunArtifact.artifact_type == artifact_type,
            RunArtifact.attempt_number == attempt_number,
        )
        .order_by(RunArtifact.created_at, RunArtifact.id)
        .all()
    )
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.storage_backend != configured_store.backend_name:
            raise ValueError(f"Artifact backend '{artifact.storage_backend}' is not configured.")
        rows.extend(configured_store.read_rows(artifact.storage_uri))
    return rows


class RunArtifactBuffer:
    """Buffers one policy partition while preserving prior-period lookups."""

    def __init__(self, run_id: str, store: LocalParquetArtifactStore | None = None):
        self.run_id = run_id
        self.store = store or get_artifact_store()
        self.output_rows: list[dict[str, Any]] = []
        self.trace_rows: list[dict[str, Any]] = []
        self.lookup: dict[tuple[str, str, int, str], Any] = {}
        self.policy_ids: set[str] = set()
        self.scenario_ids: set[str] = set()
        self.max_month = 0
        self.output_count = 0

    def add_output(
        self,
        policy_id: str,
        scenario_id: str,
        month: int,
        variable_name: str,
        value: Any,
        product: str = "",
    ) -> None:
        self.output_rows.append(
            {
                "run_id": self.run_id,
                "policy_id": policy_id,
                "scenario_id": scenario_id,
                "projection_month": month,
                "variable_name": variable_name,
                "value_json": json.dumps(value, separators=(",", ":"), default=str),
                "product": product,
            }
        )
        self.lookup[(policy_id, scenario_id, month, variable_name)] = value
        self.policy_ids.add(policy_id)
        self.scenario_ids.add(scenario_id)
        self.max_month = max(self.max_month, month)
        self.output_count += 1

    def get_output(
        self,
        policy_id: str,
        scenario_id: str,
        month: int,
        variable_name: str,
    ) -> Any:
        return self.lookup.get((policy_id, scenario_id, month, variable_name))

    def add_trace(
        self, context: ProjectionContext, result: VariableResolutionResult
    ) -> None:
        self.trace_rows.append(
            {
                "run_id": self.run_id,
                "policy_id": context.policy_id,
                "scenario_id": context.scenario_id,
                "projection_month": context.projection_month,
                "variable_name": result.variable_name,
                "output_value_json": json.dumps(
                    result.value, separators=(",", ":"), default=str
                ),
                "source_type": result.source_type,
                "source_table": result.source_table,
                "lookup_keys_json": json.dumps(result.lookup_keys, separators=(",", ":")),
                "error_message": result.error_message,
            }
        )

    def flush_policy(self, db: Session, policy_id: str) -> list[RunArtifact]:
        artifacts: list[RunArtifact] = []
        for artifact_type, rows in (
            ("outputs", self.output_rows),
            ("traces", self.trace_rows),
        ):
            if not rows:
                continue
            stored = self.store.write_rows(self.run_id, artifact_type, rows)
            artifact = RunArtifact(
                run_id=self.run_id,
                artifact_type=artifact_type,
                storage_uri=stored.uri,
                storage_backend=self.store.backend_name,
                file_format="parquet",
                schema_version="v1",
                row_count=stored.row_count,
                checksum_sha256=stored.checksum_sha256,
                partition={"policy_id": policy_id},
            )
            db.add(artifact)
            artifacts.append(artifact)
        db.flush()
        self.output_rows.clear()
        self.trace_rows.clear()
        self.lookup = {
            key: value for key, value in self.lookup.items() if key[0] != policy_id
        }
        return artifacts

    def summary(self, error_count: int = 0) -> ProjectionSummary:
        return ProjectionSummary(
            policy_count=len(self.policy_ids),
            period_count=self.max_month,
            scenario_count=len(self.scenario_ids),
            calculated_variable_count=self.output_count,
            error_count=error_count,
            warning_count=0,
        )
