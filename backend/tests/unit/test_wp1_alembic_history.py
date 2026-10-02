"""Work Package 1 — the migration history has one canonical head that includes both branches.

This check needs no database. The PostgreSQL upgrade/downgrade paths are exercised by
tests/postgres/test_wp1_migrations_postgres.py (opt-in; needs a disposable PostgreSQL database).
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
HEAD = "f9d3e5a7b012"
MERGE = "a3c5e7f9b1d2"


def script() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "app" / "db" / "migrations"))
    return ScriptDirectory.from_config(config)


def test_there_is_exactly_one_head():
    assert script().get_heads() == [HEAD]


def test_the_merge_joins_the_m1_and_dev_histories():
    merge = script().get_revision(MERGE)
    assert set(merge.down_revision) == {"4d8e2f6a1c90", "c48a2d7159be"}
    assert script().get_revision("b7e4d2a9c613").down_revision == MERGE
    assert set(script().get_revision("f8c2d4e6a901").down_revision) == {
        "a7f4c2d9e180", "b7e4d2a9c613",
    }
    assert script().get_revision(HEAD).down_revision == "f8c2d4e6a901"


def test_both_branch_heads_and_their_ancestors_are_upgraded_by_head():
    ancestors = {revision.revision for revision in script().iterate_revisions(HEAD, "base")}
    assert {
        "e0c51a917e8e", "bfc45372c779", "fbe10ad9a08b", "2f6d51e920a4",  # shared base
        "4d8e2f6a1c90",                                                  # main (M1)
        "7c3f19ad0e82", "91b4e26d7fa0", "c48a2d7159be",                  # dev (Noah)
        MERGE, "b7e4d2a9c613", "a7f4c2d9e180", "f8c2d4e6a901", HEAD,
    } <= ancestors
    assert len(script().get_bases()) == 1
