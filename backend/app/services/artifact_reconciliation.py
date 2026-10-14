"""Conservative reconciliation for unregistered local artifact files."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.artifacts import LocalParquetArtifactStore, get_artifact_store
from app.db.models.data_manager import ImportSession, RejectedRecord, ValidationRun
from app.db.models.run_artifact import RunArtifact


@dataclass(frozen=True)
class ArtifactReconciliation:
    registered_count: int
    orphan_uris: tuple[str, ...]
    deleted_uris: tuple[str, ...]


def registered_local_uris(db: Session) -> set[str]:
    """Return every local URI retained by authoritative database metadata."""
    uris: set[str] = set()
    queries = (
        db.query(RunArtifact.storage_uri),
        db.query(ImportSession.raw_storage_uri),
        db.query(ValidationRun.canonical_storage_uri),
        db.query(RejectedRecord.storage_uri),
    )
    for query in queries:
        uris.update(
            value for (value,) in query.all()
            if value and value.startswith("local://")
        )
    return uris


def reconcile_local_artifacts(
    db: Session,
    *,
    store: LocalParquetArtifactStore | None = None,
    delete: bool = False,
    min_age_seconds: int = 86_400,
    now: float | None = None,
) -> ArtifactReconciliation:
    """Find old unregistered files and optionally delete exactly those files.

    This never deletes a URI registered by run or Data Manager evidence. New files
    are age-gated so an in-flight transaction cannot be mistaken for an orphan.
    """
    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be non-negative.")
    configured = store or get_artifact_store()
    registered = registered_local_uris(db)
    cutoff = (time.time() if now is None else now) - min_age_seconds
    candidates: list[tuple[str, Path]] = []
    for path in configured.root.rglob("*"):
        if not path.is_file() or path.stat().st_mtime > cutoff:
            continue
        relative = path.resolve().relative_to(configured.root).as_posix()
        uri = f"local://{relative}"
        if uri not in registered:
            candidates.append((uri, path))
    candidates.sort(key=lambda item: item[0])
    deleted: list[str] = []
    if delete:
        for uri, path in candidates:
            resolved = path.resolve()
            resolved.relative_to(configured.root)
            resolved.unlink(missing_ok=True)
            deleted.append(uri)
    return ArtifactReconciliation(
        registered_count=len(registered),
        orphan_uris=tuple(uri for uri, _path in candidates),
        deleted_uris=tuple(deleted),
    )
