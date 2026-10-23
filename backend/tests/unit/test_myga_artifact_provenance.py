"""Artifact and RunAttempt provenance tests (M2B-A-01 through M2B-A-07).

Verifies that m2-v1 identity-neutral Parquet artifacts:
- M2B-A-01: Wrong run ID is refused with ARTIFACT_IDENTITY_MISMATCH.
- M2B-A-02: Wrong attempt number is refused with ARTIFACT_IDENTITY_MISMATCH.
- M2B-A-03: Failed-attempt artifact cannot be returned as canonical result.
- M2B-A-04: Cancelled-attempt artifact cannot be returned as canonical result.
- M2B-A-05: Accepted attempt is the only canonical result source.
- M2B-A-06: Retry attempt is distinguishable from source attempt.
- M2B-A-07: Bit-identical business result payloads remain possible across equivalent runs.
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


def test_m2b_a_01_wrong_run_id_refused(tmp_path):
    """M2B-A-01: Verifier refuses artifact when expected_run_id does not match storage identity."""
    store = LocalParquetArtifactStore(tmp_path)
    rows = [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}]
    stored = store.write_rows("run-real", "outputs", rows, attempt_number=1)

    with pytest.raises(ServiceError) as exc:
        verified_parquet_rows(
            store,
            stored.uri,
            stored.checksum_sha256,
            stored.row_count,
            IDENTITY_NEUTRAL_SCHEMA,
            supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
            expected_run_id="run-imposter",
            expected_attempt_number=1,
        )
    assert exc.value.code == "ARTIFACT_IDENTITY_MISMATCH"


def test_m2b_a_02_wrong_attempt_number_refused(tmp_path):
    """M2B-A-02: Verifier refuses artifact when expected_attempt_number does not match attempt path."""
    store = LocalParquetArtifactStore(tmp_path)
    rows = [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}]
    # Stored under attempt 1
    stored = store.write_rows("run-001", "outputs", rows, attempt_number=1)

    # Verifying with expected_attempt_number=2 must be refused
    with pytest.raises(ServiceError) as exc:
        verified_parquet_rows(
            store,
            stored.uri,
            stored.checksum_sha256,
            stored.row_count,
            IDENTITY_NEUTRAL_SCHEMA,
            supported_schemas=artifacts.RUN_ARTIFACT_SCHEMAS,
            expected_run_id="run-001",
            expected_attempt_number=2,
        )
    assert exc.value.code == "ARTIFACT_IDENTITY_MISMATCH"


def test_m2b_a_03_failed_attempt_artifact_cannot_be_canonical_result(db_session, tmp_path):
    """M2B-A-03: Failed-attempt artifact cannot be returned as canonical result."""
    store = LocalParquetArtifactStore(tmp_path)
    run_id = "run-failed-001"

    db_session.add(Run(
        id=run_id, project_id="p1", run_set_id="rs1", name="Failed Run", status="failed", accepted_attempt_number=None,
    ))
    db_session.add(RunAttempt(id="att-1", run_id=run_id, attempt_number=1, status="failed"))
    stored = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}], attempt_number=1)
    db_session.add(RunArtifact(
        run_id=run_id, attempt_number=1, artifact_type="outputs", storage_uri=stored.uri, storage_backend="local",
        file_format="parquet", schema_version=IDENTITY_NEUTRAL_SCHEMA, row_count=1, checksum_sha256=stored.checksum_sha256,
        partition={"attempt_number": 1},
    ))
    db_session.commit()

    with pytest.raises(ServiceError) as err:
        results_service.result_run(db_session, run_id)
    assert err.value.code == "RESULTS_NOT_AVAILABLE"

    rows = read_attempt_artifact_rows(db_session, run_id, "outputs", attempt_number=None, store=store)
    assert rows == []


def test_m2b_a_04_cancelled_attempt_artifact_cannot_be_canonical_result(db_session, tmp_path):
    """M2B-A-04: Cancelled-attempt artifact cannot be returned as canonical result."""
    store = LocalParquetArtifactStore(tmp_path)
    run_id = "run-cancelled-001"

    db_session.add(Run(
        id=run_id, project_id="p1", run_set_id="rs1", name="Cancelled Run", status="cancelled", accepted_attempt_number=None,
    ))
    db_session.add(RunAttempt(id="att-can-1", run_id=run_id, attempt_number=1, status="cancelled"))
    stored = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}], attempt_number=1)
    db_session.add(RunArtifact(
        run_id=run_id, attempt_number=1, artifact_type="outputs", storage_uri=stored.uri, storage_backend="local",
        file_format="parquet", schema_version=IDENTITY_NEUTRAL_SCHEMA, row_count=1, checksum_sha256=stored.checksum_sha256,
        partition={"attempt_number": 1},
    ))
    db_session.commit()

    with pytest.raises(ServiceError) as err:
        results_service.result_run(db_session, run_id)
    assert err.value.code == "RESULTS_NOT_AVAILABLE"


def test_m2b_a_05_accepted_attempt_is_only_canonical_result_source(db_session, tmp_path):
    """M2B-A-05: Accepted attempt is the only canonical result source."""
    store = LocalParquetArtifactStore(tmp_path)
    run_id = "run-acc-001"

    stored = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "104500"}], attempt_number=1)
    db_session.add(RunArtifact(
        run_id=run_id, attempt_number=1, artifact_type="outputs", storage_uri=stored.uri, storage_backend="local",
        file_format="parquet", schema_version=IDENTITY_NEUTRAL_SCHEMA, row_count=1, checksum_sha256=stored.checksum_sha256,
        partition={"attempt_number": 1},
    ))
    db_session.add(RunAttempt(id="att-acc-1", run_id=run_id, attempt_number=1, status="success"))
    db_session.add(Run(
        id=run_id, project_id="p1", run_set_id="rs1", name="Success Run", status="success", accepted_attempt_number=1,
    ))
    db_session.commit()

    rows = read_attempt_artifact_rows(db_session, run_id, "outputs", attempt_number=1, store=store)
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["attempt_number"] == 1
    assert rows[0]["value_json"] == "104500"


def test_m2b_a_06_retry_attempt_is_distinguishable_from_source(db_session, tmp_path):
    """M2B-A-06: Retry attempt is distinguishable from source failed attempt."""
    store = LocalParquetArtifactStore(tmp_path)
    run_id = "run-retry-002"

    # Attempt 1 (failed)
    stored_1 = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "50"}], attempt_number=1)
    db_session.add(RunArtifact(
        run_id=run_id, attempt_number=1, artifact_type="outputs", storage_uri=stored_1.uri, storage_backend="local",
        file_format="parquet", schema_version=IDENTITY_NEUTRAL_SCHEMA, row_count=1, checksum_sha256=stored_1.checksum_sha256,
        partition={"attempt_number": 1},
    ))

    # Attempt 2 (succeeded)
    stored_2 = store.write_rows(run_id, "outputs", [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}], attempt_number=2)
    db_session.add(RunArtifact(
        run_id=run_id, attempt_number=2, artifact_type="outputs", storage_uri=stored_2.uri, storage_backend="local",
        file_format="parquet", schema_version=IDENTITY_NEUTRAL_SCHEMA, row_count=1, checksum_sha256=stored_2.checksum_sha256,
        partition={"attempt_number": 2},
    ))
    db_session.add(Run(
        id=run_id, project_id="p1", run_set_id="rs1", name="Retried Run", status="success", accepted_attempt_number=2,
    ))
    db_session.commit()

    # Storage paths are distinctly separated by attempt
    assert "/attempt/1/" in stored_1.uri
    assert "/attempt/2/" in stored_2.uri

    canonical_rows = read_attempt_artifact_rows(db_session, run_id, "outputs", attempt_number=2, store=store)
    assert len(canonical_rows) == 1
    assert canonical_rows[0]["attempt_number"] == 2
    assert canonical_rows[0]["value_json"] == "100"


def test_m2b_a_07_bit_identical_business_payloads_across_runs(tmp_path):
    """M2B-A-07: Bit-identical business result payloads remain possible across equivalent runs."""
    store = LocalParquetArtifactStore(tmp_path)
    rows = [
        {"policy_id": "MYGA-0001", "scenario_id": "Base", "projection_month": 1, "variable_name": "av", "value_json": "100367.48"},
        {"policy_id": "MYGA-0001", "scenario_id": "Base", "projection_month": 1, "variable_name": "csv", "value_json": "94041.76"},
    ]

    first = store.write_rows("run-001", "outputs", rows, attempt_number=1)
    second = store.write_rows("run-002", "outputs", rows, attempt_number=1)

    assert store.read_bytes(first.uri) == store.read_bytes(second.uri)
    assert first.checksum_sha256 == second.checksum_sha256


def test_m2b_a_08_new_m2_writes_require_attempt_scoped_path(tmp_path):
    """M2B-A-08: New m2-v1 artifact writes strictly require attempt_number."""
    store = LocalParquetArtifactStore(tmp_path)
    rows = [{"policy_id": "P1", "variable_name": "av", "value_json": "100"}]

    # Writing with attempt_number succeeds and constructs attempt-scoped path
    stored = store.write_rows("run-001", "outputs", rows, attempt_number=1, schema_version=IDENTITY_NEUTRAL_SCHEMA)
    assert "/attempt/1/" in stored.uri

    # Attempting to write m2-v1 without attempt_number fails with ValueError
    with pytest.raises(ValueError) as exc:
        store.write_rows("run-001", "outputs", rows, attempt_number=None, schema_version=IDENTITY_NEUTRAL_SCHEMA)
    assert "attempt_number is required for new 'm2-v1' artifact writes" in str(exc.value)

