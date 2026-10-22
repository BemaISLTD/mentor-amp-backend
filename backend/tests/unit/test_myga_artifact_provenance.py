"""Artifact and RunAttempt provenance tests (MYGA-A-01 through MYGA-A-05).

Verifies that m2-v1 identity-neutral Parquet artifacts:
- Produce bit-identical Parquet byte streams across identical runs.
- Maintain immutable, unambiguous RunAttempt bindings in metadata and storage URI.
- Prevent non-accepted (failed or cancelled) attempts from being served as canonical.
- Keep retry attempts fully isolated and distinguishable from failed attempts.
"""

from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import artifacts
from app.core.artifacts import (
    IDENTITY_NEUTRAL_SCHEMA,
    LocalParquetArtifactStore,
    read_attempt_artifact_rows,
    verified_parquet_rows,
)
from app.db.models.project import Project
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunAttempt
from app.services import results_service
from app.services.common import ServiceError


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine("sqlite://")
    Project.__table__.create(engine)
    Run.__table__.create(engine)
    RunAttempt.__table__.create(engine)
    RunArtifact.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def test_myga_a_01_identical_runs_produce_bit_identical_business_parquet(tmp_path):
    """MYGA-A-01: Two identical successful runs produce bit-identical Parquet bytes."""
    store = LocalParquetArtifactStore(tmp_path)
    rows = [
        {
            "policy_id": "MYGA-0001",
            "scenario_id": "Base",
            "projection_month": 1,
            "variable_name": "account_value_close",
            "value_json": "100367.48",
            "product": "MYGA",
        },
        {
            "policy_id": "MYGA-0001",
            "scenario_id": "Base",
            "projection_month": 1,
            "variable_name": "surrender_benefit",
            "value_json": "94041.76",
            "product": "MYGA",
        },
    ]

    first = store.write_rows("run-001", "outputs", rows)
    second = store.write_rows("run-002", "outputs", rows)

    first_bytes = store.read_bytes(first.uri)
    second_bytes = store.read_bytes(second.uri)

    assert first_bytes == second_bytes
    assert first.checksum_sha256 == second.checksum_sha256
    assert first.row_count == second.row_count == 2


def test_myga_a_02_artifacts_have_distinct_run_attempt_evidence(tmp_path):
    """MYGA-A-02: Artifacts still maintain distinct run/attempt storage and verification boundaries."""
    store = LocalParquetArtifactStore(tmp_path)
    rows = [
        {
            "policy_id": "MYGA-0001",
            "scenario_id": "Base",
            "projection_month": 1,
            "variable_name": "reserve",
            "value_json": "100000.0",
            "product": "MYGA",
        }
    ]

    # Stored for run-A attempt 1
    artifact_a = store.write_rows("run-A", "outputs", rows)

    # Reading with matching expected_run_id and expected_attempt_number succeeds
    verified = verified_parquet_rows(
        store,
        artifact_a.uri,
        artifact_a.checksum_sha256,
        artifact_a.row_count,
        IDENTITY_NEUTRAL_SCHEMA,
        supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
        expected_run_id="run-A",
        expected_attempt_number=1,
    )
    assert len(verified) == 1
    assert verified[0]["run_id"] == "run-A"
    assert verified[0]["attempt_number"] == 1

    # Reading with mismatched expected_run_id fails with ARTIFACT_IDENTITY_MISMATCH
    with pytest.raises(ServiceError) as exc_info:
        verified_parquet_rows(
            store,
            artifact_a.uri,
            artifact_a.checksum_sha256,
            artifact_a.row_count,
            IDENTITY_NEUTRAL_SCHEMA,
            supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
            expected_run_id="run-B",
            expected_attempt_number=1,
        )
    assert exc_info.value.code == "ARTIFACT_IDENTITY_MISMATCH"


def test_myga_a_03_failed_attempt_artifact_cannot_be_canonical_result(db_session, tmp_path):
    """MYGA-A-03: Failed attempt artifacts cannot be returned as canonical results."""
    store = LocalParquetArtifactStore(tmp_path)
    run_id = "run-failed-001"

    # Run record in failed state (no accepted attempt)
    db_session.add(Run(
        id=run_id,
        project_id="proj-1",
        run_set_id="rs-1",
        name="Failed Run",
        status="failed",
        accepted_attempt_number=None,
    ))
    db_session.add(RunAttempt(
        id="att-1",
        run_id=run_id,
        attempt_number=1,
        status="failed",
    ))
    stored = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}])
    db_session.add(RunArtifact(
        run_id=run_id,
        attempt_number=1,
        artifact_type="outputs",
        storage_uri=stored.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version=IDENTITY_NEUTRAL_SCHEMA,
        row_count=1,
        checksum_sha256=stored.checksum_sha256,
        partition={"attempt_number": 1},
    ))
    db_session.commit()

    # Accessing results raises 409 RESULTS_NOT_AVAILABLE
    with pytest.raises(ServiceError) as err:
        results_service.result_run(db_session, run_id)
    assert err.value.status_code == 409
    assert err.value.code == "RESULTS_NOT_AVAILABLE"

    # Reading attempt rows with attempt_number=None returns empty list
    rows = read_attempt_artifact_rows(db_session, run_id, "outputs", attempt_number=None, store=store)
    assert rows == []


def test_myga_a_04_cancelled_attempt_artifact_cannot_be_canonical_result(db_session, tmp_path):
    """MYGA-A-04: Cancelled attempt artifacts cannot be returned as canonical results."""
    store = LocalParquetArtifactStore(tmp_path)
    run_id = "run-cancelled-001"

    db_session.add(Run(
        id=run_id,
        project_id="proj-1",
        run_set_id="rs-1",
        name="Cancelled Run",
        status="cancelled",
        accepted_attempt_number=None,
    ))
    db_session.add(RunAttempt(
        id="att-cancelled-1",
        run_id=run_id,
        attempt_number=1,
        status="cancelled",
    ))
    stored = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}])
    db_session.add(RunArtifact(
        run_id=run_id,
        attempt_number=1,
        artifact_type="outputs",
        storage_uri=stored.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version=IDENTITY_NEUTRAL_SCHEMA,
        row_count=1,
        checksum_sha256=stored.checksum_sha256,
        partition={"attempt_number": 1},
    ))
    db_session.commit()

    # Results service rejects cancelled run
    with pytest.raises(ServiceError) as err:
        results_service.result_run(db_session, run_id)
    assert err.value.status_code == 409
    assert err.value.code == "RESULTS_NOT_AVAILABLE"


def test_myga_a_05_retry_attempt_remains_distinguishable_from_source(db_session, tmp_path):
    """MYGA-A-05: Retry attempt remains distinguishable from source failed attempt."""
    store = LocalParquetArtifactStore(tmp_path)
    run_id = "run-retry-001"

    # Attempt 1: Failed
    stored_1 = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "50"}])
    db_session.add(RunArtifact(
        run_id=run_id,
        attempt_number=1,
        artifact_type="outputs",
        storage_uri=stored_1.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version=IDENTITY_NEUTRAL_SCHEMA,
        row_count=1,
        checksum_sha256=stored_1.checksum_sha256,
        partition={"attempt_number": 1},
    ))

    # Attempt 2: Succeeded and accepted
    stored_2 = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}])
    db_session.add(RunArtifact(
        run_id=run_id,
        attempt_number=2,
        artifact_type="outputs",
        storage_uri=stored_2.uri,
        storage_backend="local",
        file_format="parquet",
        schema_version=IDENTITY_NEUTRAL_SCHEMA,
        row_count=1,
        checksum_sha256=stored_2.checksum_sha256,
        partition={"attempt_number": 2},
    ))
    db_session.add(Run(
        id=run_id,
        project_id="proj-1",
        run_set_id="rs-1",
        name="Retried Run",
        status="success",
        accepted_attempt_number=2,
    ))
    db_session.commit()

    # Canonical read returns only accepted attempt 2 rows
    canonical_rows = read_attempt_artifact_rows(db_session, run_id, "outputs", attempt_number=2, store=store)
    assert len(canonical_rows) == 1
    assert canonical_rows[0]["attempt_number"] == 2
    assert canonical_rows[0]["value_json"] == "100"

    # Attempt 1 rows are NOT returned as canonical
    assert not any(r["attempt_number"] == 1 for r in canonical_rows)
