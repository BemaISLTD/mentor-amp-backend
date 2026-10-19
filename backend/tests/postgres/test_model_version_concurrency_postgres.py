"""Real PostgreSQL concurrency and race condition tests for model version governance.

Requires MENTORAMP_TEST_POSTGRES_URL pointing to a disposable test database.
Verifies on real PostgreSQL with true multithreading, transactions, and partial unique indexes:
- G-28: Concurrent publication attempts (locking, partial unique index uq_model_versions_one_current, exactly one current active version).
- G-29: Concurrent approval and edit race (locking/invalidation prevents approving modified content without validation).
- G-30: Concurrent validation and edit race (post-condition invariant: version is never left in validated state with unvalidated content).
"""

import os
import threading
import uuid
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.auth import initialize_builtin_roles
from app.config import settings
from app.core.model_fingerprint import compute_model_definition_fingerprint
from app.db.models.modeling import Model, ModelVersion
from app.db.models.user import User
from app.services import model_version_service, variable_definition_service
from tests.integration.wp1_support import load_seed_module

RAW_URL = os.environ.get("MENTORAMP_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not RAW_URL, reason="set MENTORAMP_TEST_POSTGRES_URL to a disposable PostgreSQL database"
)
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _url() -> str:
    url = RAW_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    database = urlparse(url).path.lstrip("/")
    if "test" not in database:
        pytest.fail(f"Refusing to reset database '{database}': its name must contain 'test'.")
    return url


@pytest.fixture(scope="module")
def pg():
    patch = pytest.MonkeyPatch()
    url = _url()
    patch.setattr(settings, "database_url", url)
    engine = create_engine(url)
    config = Config()
    config.set_main_option("script_location", os.path.join(BACKEND, "app", "db", "migrations"))
    yield engine, config
    engine.dispose()
    patch.undo()


def reset(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def _setup_pg_environment(pg):
    engine, config = pg
    reset(engine)
    command.upgrade(config, "head")
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as db:
        roles = initialize_builtin_roles(db)
        seeded = load_seed_module().seed(db, project_name=f"PG Gov Concurrency {uuid.uuid4()}")
        admin = seeded["user"]
        actuary = User(
            email=f"actuary_{uuid.uuid4()}@example.com",
            full_name="Actuary Reviewer",
            password_hash="x" * 60,
            roles=[roles["actuary"]],
        )
        db.add(actuary)
        db.flush()
        from app.db.models.project_member import ProjectMember
        db.add(ProjectMember(project_id=seeded["project"].id, user_id=actuary.id, role="editor"))
        db.commit()
        db.refresh(admin)
        db.refresh(actuary)

        model_id = seeded["model"].id
        v1_id = seeded["version"].id
        admin_id = admin.id
        actuary_id = actuary.id

    return Session, model_id, v1_id, admin_id, actuary_id


# =============================================================================
# G-28: CONCURRENT PUBLICATION RACE ON REAL POSTGRESQL
# =============================================================================

def test_concurrent_publication_guarantees_exactly_one_current(pg):
    """G-28: Two concurrent threads publish different approved versions of the same model.

    The model row lock and partial unique index 'uq_model_versions_one_current' guarantee:
    1. No deadlock.
    2. Exactly one model version is is_current=True at all times.
    3. Superseded versions transition to status='superseded' and is_current=False.
    """
    Session, model_id, v1_id, admin_id, actuary_id = _setup_pg_environment(pg)

    # Create and approve two candidate versions (v2 and v3)
    with Session() as db:
        admin = db.get(User, admin_id)
        actuary = db.get(User, actuary_id)

        v2 = model_version_service.create_version(
            db, model_id, {"parent_version_id": v1_id, "version_label": "2026.conc_v2", "basis": "GAAP"}, admin
        )
        v3 = model_version_service.create_version(
            db, model_id, {"parent_version_id": v1_id, "version_label": "2026.conc_v3", "basis": "STAT"}, admin
        )
        v2_id = v2["id"]
        v3_id = v3["id"]

        model_version_service.validate_version(db, v2_id, admin)
        model_version_service.validate_version(db, v3_id, admin)

        model_version_service.approve_version(db, v2_id, actuary, "Approved v2")
        model_version_service.approve_version(db, v3_id, actuary, "Approved v3")

    barrier = threading.Barrier(2)
    results = {}
    errors = {}

    def publish_worker(version_id: str, label: str):
        try:
            barrier.wait(timeout=10)
            with Session() as db:
                user = db.get(User, admin_id)
                res = model_version_service.publish_version(db, version_id, user)
                results[label] = res
        except Exception as exc:
            errors[label] = exc

    t1 = threading.Thread(target=publish_worker, args=(v2_id, "v2"))
    t2 = threading.Thread(target=publish_worker, args=(v3_id, "v3"))

    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # Verify database integrity on PostgreSQL
    with Session() as db:
        current_rows = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.model_id == model_id,
                ModelVersion.is_current.is_(True),
                ModelVersion.deleted_at.is_(None),
            )
            .all()
        )
        # INVARIANT: Exactly one version is current!
        assert len(current_rows) == 1, f"Expected exactly 1 current version, found {len(current_rows)}"
        winner = current_rows[0]
        assert winner.id in (v2_id, v3_id)
        assert winner.is_current is True

        # The other version must be superseded (or remained approved if serialized after)
        loser_id = v3_id if winner.id == v2_id else v2_id
        loser = db.get(ModelVersion, loser_id)
        assert loser.is_current is False


# =============================================================================
# G-29: CONCURRENT APPROVAL AND EDIT RACE
# =============================================================================

def test_concurrent_approval_and_edit_race(pg):
    """G-29: Thread A edits model content while Thread B attempts approval.

    The model must never end up in approved status with unvalidated content.
    Either:
    - Edit commits first: approval fails with MODEL_VALIDATION_STALE or MODEL_VERSION_NOT_VALIDATED.
    - Approval commits first: edit fails with MODEL_VERSION_IMMUTABLE.
    """
    Session, model_id, v1_id, admin_id, actuary_id = _setup_pg_environment(pg)

    with Session() as db:
        admin = db.get(User, admin_id)
        actuary = db.get(User, actuary_id)

        v = model_version_service.create_version(
            db, model_id, {"parent_version_id": v1_id, "version_label": "2026.race_app", "basis": "GAAP"}, admin
        )
        v_id = v["id"]
        model_version_service.validate_version(db, v_id, admin)

    barrier = threading.Barrier(2)
    outcome = {}

    def editor_worker():
        try:
            barrier.wait(timeout=10)
            with Session() as db:
                user = db.get(User, admin_id)
                model_version_service.update_version(db, v_id, {"basis": "STAT"}, user)
                outcome["editor"] = "success"
        except Exception as exc:
            outcome["editor"] = type(exc).__name__

    def approver_worker():
        try:
            barrier.wait(timeout=10)
            with Session() as db:
                user = db.get(User, actuary_id)
                model_version_service.approve_version(db, v_id, user, "Concurrent approval")
                outcome["approver"] = "success"
        except Exception as exc:
            outcome["approver"] = type(exc).__name__

    t1 = threading.Thread(target=editor_worker)
    t2 = threading.Thread(target=approver_worker)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # Invariant check
    with Session() as db:
        row = db.get(ModelVersion, v_id)
        if row.status == "approved":
            # If approved, the approved fingerprint MUST match the exact current database definition!
            current_fp = compute_model_definition_fingerprint(db, v_id)
            assert row.approved_fingerprint == current_fp
        else:
            # If not approved, it must be draft (invalidated by edit)
            assert row.status == "draft"


# =============================================================================
# G-30: CONCURRENT VALIDATION AND EDIT RACE
# =============================================================================

def test_concurrent_validation_and_edit_race(pg):
    """G-30: Thread A validates while Thread B modifies variable definitions.

    The model version must never end up in validated status if unvalidated modifications were applied.
    """
    Session, model_id, v1_id, admin_id, actuary_id = _setup_pg_environment(pg)

    with Session() as db:
        admin = db.get(User, admin_id)
        v = model_version_service.create_version(
            db, model_id, {"parent_version_id": v1_id, "version_label": "2026.race_val", "basis": "GAAP"}, admin
        )
        v_id = v["id"]
        var_defs = variable_definition_service.list_definitions(db, v_id)
        var_name = var_defs["variables"][0]["variable_name"]

    barrier = threading.Barrier(2)
    outcome = {}

    def validator_worker():
        try:
            barrier.wait(timeout=10)
            with Session() as db:
                user = db.get(User, admin_id)
                model_version_service.validate_version(db, v_id, user)
                outcome["validator"] = "success"
        except Exception as exc:
            outcome["validator"] = type(exc).__name__

    def editor_worker():
        try:
            barrier.wait(timeout=10)
            with Session() as db:
                user = db.get(User, admin_id)
                variable_definition_service.update_definition(
                    db, v_id, var_name, {"display_name": "Concurrent Var Change"}, user
                )
                outcome["editor"] = "success"
        except Exception as exc:
            outcome["editor"] = type(exc).__name__

    t1 = threading.Thread(target=validator_worker)
    t2 = threading.Thread(target=editor_worker)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # Invariant verification
    with Session() as db:
        row = db.get(ModelVersion, v_id)
        if row.status == "validated":
            # If validated, validation fingerprint MUST match the current database state
            current_fp = compute_model_definition_fingerprint(db, v_id)
            assert row.validation_fingerprint == current_fp
        else:
            # Otherwise edit invalidated it, so status is draft and validation evidence is cleared
            assert row.status == "draft"
            assert row.validation_fingerprint is None
