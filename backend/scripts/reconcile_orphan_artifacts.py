"""Dry-run or delete old, unregistered local artifact files."""

from __future__ import annotations

import argparse

from app.config import settings
from app.db.database import SessionLocal
from app.services.artifact_reconciliation import reconcile_local_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true", help="Delete listed orphan files.")
    parser.add_argument("--min-age-seconds", type=int, default=86_400)
    parser.add_argument(
        "--confirm-database",
        help="Required with --delete; must exactly match the configured database URL.",
    )
    args = parser.parse_args()
    if args.delete and args.confirm_database != settings.database_url:
        parser.error("--delete requires --confirm-database matching DATABASE_URL exactly")
    with SessionLocal() as db:
        result = reconcile_local_artifacts(
            db, delete=args.delete, min_age_seconds=args.min_age_seconds
        )
    action = "deleted" if args.delete else "would delete"
    for uri in result.orphan_uris:
        print(f"{action}: {uri}")
    print(
        f"registered={result.registered_count} "
        f"orphans={len(result.orphan_uris)} deleted={len(result.deleted_uris)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
