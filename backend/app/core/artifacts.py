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
from app.services.common import ServiceError

RUN_ARTIFACT_SCHEMAS = frozenset({"v1", "m1-v1"})


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

    def write_bytes(self, namespace: str, artifact_type: str, filename: str,
                    content: bytes) -> StoredArtifact:
        """Persist an immutable raw upload without interpreting or rewriting it."""
        suffix = Path(filename).suffix.lower()
        relative = (
            Path(self._safe_component(namespace)) / self._safe_component(artifact_type)
            / f"{uuid.uuid4()}{suffix}"
        )
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredArtifact(
            uri=f"local://{relative.as_posix()}", row_count=0,
            checksum_sha256=hashlib.sha256(content).hexdigest(),
        )

    def read_bytes(self, uri: str) -> bytes:
        return self.path_for_uri(uri).read_bytes()

    def path_for_uri(self, uri: str) -> Path:
        prefix = "local://"
        if not uri.startswith(prefix):
            raise ValueError(f"Unsupported local artifact URI: {uri}")
        path = (self.root / uri.removeprefix(prefix)).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Artifact URI escapes the configured storage root.")
        return path

    def read_rows(self, uri: str) -> list[dict[str, Any]]:
        return pl.read_parquet(self.path_for_uri(uri)).to_dicts()


def get_artifact_store() -> LocalParquetArtifactStore:
    if settings.artifact_storage_backend != "local":
        raise ValueError(
            f"Unsupported artifact storage backend: {settings.artifact_storage_backend}"
        )
    return LocalParquetArtifactStore(settings.artifact_storage_path)


def _artifact_error(code: str, message: str, uri: str) -> ServiceError:
    return ServiceError(409, code, message, {"storage_uri": uri})


def verified_artifact_bytes(
    store: LocalParquetArtifactStore,
    uri: str,
    checksum_sha256: str | None,
    *,
    expected_size: int | None = None,
) -> bytes:
    """Read bytes only when retained metadata proves their identity."""
    try:
        path = store.path_for_uri(uri)
        size = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except FileNotFoundError as error:
        raise _artifact_error(
            "ARTIFACT_MISSING", "The registered artifact file is missing.", uri
        ) from error
    if not checksum_sha256 or digest.hexdigest() != checksum_sha256:
        raise _artifact_error(
            "ARTIFACT_CHECKSUM_MISMATCH",
            "The artifact checksum does not match its registered evidence.",
            uri,
        )
    if expected_size is not None and size != expected_size:
        raise _artifact_error(
            "ARTIFACT_ROW_COUNT_MISMATCH",
            "The artifact size does not match its registered evidence.",
            uri,
        )
    return path.read_bytes()


def verified_parquet_rows(
    store: LocalParquetArtifactStore,
    uri: str,
    checksum_sha256: str | None,
    row_count: int | None,
    schema_version: str | None,
    *,
    supported_schemas: frozenset[str],
    expected_run_id: str | None = None,
    expected_attempt_number: int | None = None,
    filters: dict[str, Any] | None = None,
    ranges: dict[str, tuple[int | None, int | None]] | None = None,
) -> list[dict[str, Any]]:
    try:
        path = store.path_for_uri(uri)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except FileNotFoundError as error:
        raise _artifact_error(
            "ARTIFACT_MISSING", "The registered artifact file is missing.", uri
        ) from error
    if not checksum_sha256 or digest.hexdigest() != checksum_sha256:
        raise _artifact_error(
            "ARTIFACT_CHECKSUM_MISMATCH",
            "The artifact checksum does not match its registered evidence.",
            uri,
        )
    if schema_version not in supported_schemas:
        raise _artifact_error(
            "ARTIFACT_SCHEMA_UNSUPPORTED",
            f"Artifact schema '{schema_version}' is not supported.",
            uri,
        )
    try:
        base = pl.scan_parquet(path)
        actual_count = base.select(pl.len()).collect().item()
        if row_count is None or actual_count != row_count:
            raise _artifact_error(
                "ARTIFACT_ROW_COUNT_MISMATCH",
                "The artifact row count does not match its registered evidence.",
                uri,
            )
        identity = None
        if expected_run_id is not None:
            identity = pl.col("run_id") != expected_run_id
        if expected_attempt_number is not None:
            mismatch = pl.col("attempt_number") != expected_attempt_number
            identity = mismatch if identity is None else identity | mismatch
        if identity is not None and base.filter(identity).limit(1).collect().height:
            raise _artifact_error(
                "ARTIFACT_IDENTITY_MISMATCH",
                "Artifact rows do not match the registered run attempt.",
                uri,
            )
        query = base
        for column, value in (filters or {}).items():
            if value is None:
                continue
            if isinstance(value, (list, tuple, set, frozenset)):
                query = query.filter(pl.col(column).is_in(list(value)))
            else:
                query = query.filter(pl.col(column) == value)
        for column, bounds in (ranges or {}).items():
            lower, upper = bounds
            if lower is not None:
                query = query.filter(pl.col(column) >= lower)
            if upper is not None:
                query = query.filter(pl.col(column) <= upper)
        rows = query.collect().to_dicts()
    except ServiceError:
        raise
    except Exception as error:
        raise _artifact_error(
            "ARTIFACT_UNREADABLE", "The registered Parquet artifact cannot be read.", uri
        ) from error
    return rows


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
        rows.extend(verified_parquet_rows(
            configured_store,
            artifact.storage_uri,
            artifact.checksum_sha256,
            artifact.row_count,
            artifact.schema_version,
            supported_schemas=RUN_ARTIFACT_SCHEMAS,
            expected_run_id=run_id,
        ))
    return rows


def read_attempt_artifact_rows(
    db: Session,
    run_id: str,
    artifact_type: str,
    attempt_number: int | None,
    store: LocalParquetArtifactStore | None = None,
    *,
    filters: dict[str, Any] | None = None,
    ranges: dict[str, tuple[int | None, int | None]] | None = None,
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
        rows.extend(verified_parquet_rows(
            configured_store,
            artifact.storage_uri,
            artifact.checksum_sha256,
            artifact.row_count,
            artifact.schema_version,
            supported_schemas=RUN_ARTIFACT_SCHEMAS,
            expected_run_id=run_id,
            expected_attempt_number=attempt_number,
            filters=filters,
            ranges=ranges,
        ))
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
