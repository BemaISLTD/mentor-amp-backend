"""merge the M1 history (main) with the dev history (audit, run manifests, products)

Revision ID: a3c5e7f9b1d2
Revises: 4d8e2f6a1c90, c48a2d7159be
Create Date: 2026-09-30

Two histories branched from 2f6d51e920a4:

  main: 4d8e2f6a1c90 (M1 models, projection sets, run sets, run events)
  dev:  7c3f19ad0e82 (audit logging) -> 91b4e26d7fa0 (run_manifests, run_artifacts)
        -> c48a2d7159be (products, product mappings, asset positions)

The dev files are included byte-for-byte (same revision IDs), so a database at either head
upgrades to the single canonical head without replaying or skipping anything:

  - at 4d8e2f6a1c90 (M1 Neon): applies 7c3f19ad0e82, 91b4e26d7fa0, c48a2d7159be, this merge;
  - at c48a2d7159be (a dev database): applies 4d8e2f6a1c90, this merge;
  - empty database: applies everything.

The two branches touch disjoint tables, so the merge itself changes nothing.
"""
from typing import Sequence, Union


revision: str = "a3c5e7f9b1d2"
down_revision: Union[str, Sequence[str], None] = ("4d8e2f6a1c90", "c48a2d7159be")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
