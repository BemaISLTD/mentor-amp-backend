"""Final run manifests and analytical artifacts.

The two tables were introduced on Noah's ``dev`` branch (migration ``91b4e26d7fa0``); this
module keeps his column definitions and adds the Work Package 1 columns (migration
``b7e4d2a9c613``). ``run_manifests`` is the canonical store of the *final* manifest: written
once, when a run reaches a terminal status, and never updated.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.immutability import forbid_updates
from app.db.types import JSONBDocument

JSON_VALUE = JSONBDocument


class RunManifest(Base):
    __tablename__ = "run_manifests"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    # final_manifest_fingerprint: covers configuration identity AND the execution outcome.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # --- Work Package 1 ---
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # CASCADE (not SET NULL): a SET NULL would be an UPDATE, which the immutability trigger refuses.
    run_package_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("run_packages.id", ondelete="CASCADE"), nullable=True
    )
    run_package_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


forbid_updates(RunManifest)


class RunArtifact(Base):
    __tablename__ = "run_artifacts"
    __table_args__ = (
        UniqueConstraint("storage_uri", name="uq_run_artifacts_storage_uri"),
        Index("idx_run_artifacts_run_type", "run_id", "artifact_type"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(30), nullable=False)
    file_format: Mapped[str] = mapped_column(String(20), default="parquet", nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    partition: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
