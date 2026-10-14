"""Operator command for the verified M1 relational-to-Parquet bridge."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.config import settings
from app.core.artifacts import LocalParquetArtifactStore
from app.services.legacy_artifact_bridge import (
    PRE_DROP_REVISION,
    export_legacy_run_artifacts,
)


def _database_name(url: str) -> str:
    normalized = url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return urlparse(normalized).path.lstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export populated M1 result/trace tables to verified Parquet, then upgrade."
    )
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument(
        "--confirm-database",
        required=True,
        help="Exact database name; guards against migrating a different database by mistake.",
    )
    args = parser.parse_args()
    database = _database_name(args.database_url)
    if args.confirm_database != database:
        parser.error(
            f"--confirm-database must exactly match URL database '{database}'."
        )

    # Alembic env.py reads the settings object at command execution time.
    settings.database_url = args.database_url
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"),
    )
    command.upgrade(config, PRE_DROP_REVISION)

    engine_url = args.database_url.replace(
        "postgresql://", "postgresql+psycopg2://", 1
    )
    engine = create_engine(engine_url, pool_pre_ping=True)
    try:
        report = export_legacy_run_artifacts(
            engine, LocalParquetArtifactStore(args.artifact_root)
        )
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    print(
        "Verified legacy export complete: "
        f"{report.output_rows} output rows, {report.trace_rows} trace rows, "
        f"{report.artifacts} Parquet artifacts, {len(report.run_ids)} runs."
    )


if __name__ == "__main__":
    main()
