"""Tests for Derived Dataset Registry (M5B) and Resolver integration.

Verifies:
1. Output-as-input promotion from completed runs to Parquet datasets.
2. Zero IDOR leakage: cross-project queries raise not_found (HTTP 404).
3. Four-eyes segregation of duties: creators cannot approve their own datasets.
4. Content fingerprinting and physical storage verification.
5. Variable resolver lookups for kind='prior_output' bound to derived datasets.
"""

from datetime import datetime, timezone
import uuid
import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core.artifacts import LocalParquetArtifactStore
from app.core.variable_registry.registry import register
from app.core.variable_registry.resolver import resolve
from app.db.database import Base
from app.db.models.governance import DerivedDataset
from app.db.models.project import Project
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.user import User
from app.models.schemas import ProjectionContext, VariableDefinition
from app.services.common import ServiceError
from app.services.derived_dataset_service import (
    AmbiguousDerivedOutputError,
    DerivedDatasetGovernanceError,
    approve_derived_dataset,
    get_derived_dataset,
    list_derived_datasets,
    lookup_derived_output_value,
    promote_run_to_derived_dataset,
    supersede_derived_dataset,
    validate_derived_dataset,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def test_environment(db_session, tmp_path, monkeypatch):
    """Seed test project, users, run, and Parquet storage."""
    monkeypatch.setattr(settings, "artifact_storage_path", str(tmp_path / "artifacts"))
    store = LocalParquetArtifactStore(tmp_path / "artifacts")

    # Seed Project
    project = Project(id="proj-1", name="Alpha Project")
    db_session.add(project)

    # Seed Users
    user1 = User(id="user-actuary-1", email="actuary1@mentoramp.com", full_name="Alice Actuary", password_hash="hash1")
    user2 = User(id="user-lead-2", email="lead2@mentoramp.com", full_name="Bob PeerLead", password_hash="hash2")
    db_session.add_all([user1, user2])

    # Seed Run
    run = Run(
        id="run-baseline-1",
        project_id="proj-1",
        run_type="projection",
        projection_key="MYGA",
        status="success",
    )
    db_session.add(run)
    db_session.commit()

    # Create sample Parquet output rows
    sample_rows = [
        {"policy_id": "POL-100", "projection_month": 0, "account_value": 100000.0, "credited_rate": 0.045},
        {"policy_id": "POL-100", "projection_month": 1, "account_value": 100375.0, "credited_rate": 0.045},
        {"policy_id": "POL-100", "projection_month": 2, "account_value": 100751.4, "credited_rate": 0.045},
        {"policy_id": "POL-200", "projection_month": 0, "account_value": 50000.0, "credited_rate": 0.040},
        {"policy_id": "POL-200", "projection_month": 1, "account_value": 50166.7, "credited_rate": 0.040},
    ]
    stored = store.write_rows("run-baseline-1", "outputs", sample_rows, attempt_number=1, schema_version="m2-v1")

    # Create RunArtifact
    artifact = RunArtifact(
        id="art-1",
        run_id="run-baseline-1",
        attempt_number=1,
        artifact_type="outputs",
        storage_uri=stored.uri,
        storage_backend="local",
        row_count=stored.row_count,
        checksum_sha256=stored.checksum_sha256,
        schema_version="m2-v1",
    )
    db_session.add(artifact)
    db_session.commit()

    return {
        "store": store,
        "project": project,
        "run": run,
        "artifact": artifact,
        "user1": user1,
        "user2": user2,
    }


def test_promote_run_to_derived_dataset_success(db_session, test_environment):
    env = test_environment
    dataset = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="baseline_account_values",
        dataset_version="1",
        user_id="user-actuary-1",
        description="Frozen baseline account value projections for valuation",
        product_code="MYGA",
    )

    assert dataset.id is not None
    assert dataset.name == "baseline_account_values"
    assert dataset.status == "draft"
    assert dataset.row_count == 5
    assert dataset.source_run_id == "run-baseline-1"
    assert "account_value" in dataset.schema
    assert dataset.fingerprint == env["artifact"].checksum_sha256
    assert "policy_id" in dataset.dimensions_available
    assert "projection_month" in dataset.dimensions_available


def test_promote_non_success_run_fails(db_session, test_environment):
    failed_run = Run(
        id="run-failed-99",
        project_id="proj-1",
        status="failed",
    )
    db_session.add(failed_run)
    db_session.commit()

    with pytest.raises(ServiceError) as exc_info:
        promote_run_to_derived_dataset(
            db=db_session,
            project_id="proj-1",
            run_id="run-failed-99",
            dataset_name="bad_dataset",
            dataset_version="1",
            user_id="user-actuary-1",
        )
    assert exc_info.value.status_code == 400
    assert "non-success status" in exc_info.value.message


def test_idor_cross_project_isolation(db_session, test_environment):
    """Accessing or promoting across project boundary strictly raises 404."""
    # Attempting to promote using unauthorized project_id
    with pytest.raises(ServiceError) as exc_info:
        promote_run_to_derived_dataset(
            db=db_session,
            project_id="other-tenant-proj-999",
            run_id="run-baseline-1",
            dataset_name="leaked_dataset",
            dataset_version="1",
            user_id="user-actuary-1",
        )
    assert exc_info.value.status_code == 404

    # Successfully promote in proj-1
    dataset = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="proj1_dataset",
        dataset_version="1",
        user_id="user-actuary-1",
    )

    # Cross-tenant query raises 404
    with pytest.raises(ServiceError) as exc_info:
        get_derived_dataset(db_session, "other-tenant-proj-999", dataset.id)
    assert exc_info.value.status_code == 404


def test_four_eyes_segregation_of_duties(db_session, test_environment):
    dataset = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="segregation_test_dataset",
        dataset_version="1",
        user_id="user-actuary-1",
    )

    # 1. Creator attempts to approve their own dataset -> must fail with 400
    with pytest.raises(ServiceError) as exc_info:
        approve_derived_dataset(
            db=db_session,
            project_id="proj-1",
            dataset_id=dataset.id,
            approved_by_user_id="user-actuary-1",
        )
    assert exc_info.value.status_code == 400
    assert "Segregation of duties" in exc_info.value.message

    # 2. Independent peer lead approves -> succeeds
    approved = approve_derived_dataset(
        db=db_session,
        project_id="proj-1",
        dataset_id=dataset.id,
        approved_by_user_id="user-lead-2",
    )
    assert approved.status == "approved"
    assert approved.approved_by == "user-lead-2"
    assert approved.approved_at is not None


def test_resolver_prior_output_binding(db_session, test_environment):
    # Promote and approve dataset
    dataset = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="prior_account_values",
        dataset_version="1",
        user_id="user-actuary-1",
    )
    approve_derived_dataset(
        db=db_session,
        project_id="proj-1",
        dataset_id=dataset.id,
        approved_by_user_id="user-lead-2",
    )

    # Register variable with kind='prior_output' bound to dataset_id
    var_def = VariableDefinition(
        id=str(uuid.uuid4()),
        name="prior_run_account_value",
        kind="prior_output",
        source={
            "type": "prior_output",
            "dataset_id": dataset.id,
            "column_name": "account_value",
        },
        required=True,
    )
    register(db_session, var_def, actor_user_id="user-actuary-1")

    # Test context for POL-100 month 1
    ctx1 = ProjectionContext(
        project_id="proj-1",
        policy_id="POL-100",
        projection_month=1,
    )
    res1 = resolve("prior_run_account_value", ctx1, db_session)
    assert res1.value == pytest.approx(100375.0)
    assert res1.source_type == "prior_output"

    # Test context for POL-200 month 0
    ctx2 = ProjectionContext(
        project_id="proj-1",
        policy_id="POL-200",
        projection_month=0,
    )
    res2 = resolve("prior_run_account_value", ctx2, db_session)
    assert res2.value == pytest.approx(50000.0)


def test_governed_resolution_lifecycle_and_security(db_session, test_environment):
    """Directives 1 & 8: Verify strict governance state and zero cross-project IDOR leakage."""
    # 1. Draft dataset -> rejected
    draft_ds = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="draft_dataset",
        dataset_version="1",
        user_id="user-actuary-1",
    )
    with pytest.raises(DerivedDatasetGovernanceError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id=draft_ds.id,
            variable_name="account_value",
            project_id="proj-1",
            governed_execution=True,
            policy_id="POL-100",
            projection_month=1,
        )
    assert exc_info.value.status_code == 400
    assert "draft" in exc_info.value.message

    # 2. Validated but unapproved dataset -> rejected
    validate_derived_dataset(db_session, "proj-1", draft_ds.id, "user-lead-2")
    assert draft_ds.status == "validated"
    with pytest.raises(DerivedDatasetGovernanceError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id=draft_ds.id,
            variable_name="account_value",
            project_id="proj-1",
            governed_execution=True,
            policy_id="POL-100",
            projection_month=1,
        )
    assert "validated" in exc_info.value.message

    # 3. Approved dataset -> succeeds
    approve_derived_dataset(db_session, "proj-1", draft_ds.id, "user-lead-2")
    val = lookup_derived_output_value(
        db=db_session,
        dataset_id=draft_ds.id,
        variable_name="account_value",
        project_id="proj-1",
        governed_execution=True,
        policy_id="POL-100",
        projection_month=1,
    )
    assert val == pytest.approx(100375.0)

    # 4. Foreign project access -> returns HTTP 404 (zero IDOR leakage)
    with pytest.raises(ServiceError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id=draft_ds.id,
            variable_name="account_value",
            project_id="proj-FOREIGN-TENANT",
            governed_execution=True,
            policy_id="POL-100",
            projection_month=1,
        )
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.message.lower()

    # 5. Nonexistent dataset -> 404
    with pytest.raises(ServiceError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id="nonexistent-uuid",
            variable_name="account_value",
            project_id="proj-1",
            governed_execution=True,
        )
    assert exc_info.value.status_code == 404

    # 6. Superseded dataset -> rejected
    supersede_derived_dataset(db_session, "proj-1", draft_ds.id, "user-lead-2")
    assert draft_ds.status == "superseded"
    with pytest.raises(DerivedDatasetGovernanceError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id=draft_ds.id,
            variable_name="account_value",
            project_id="proj-1",
            governed_execution=True,
            policy_id="POL-100",
            projection_month=1,
        )
    assert "superseded" in exc_info.value.message.lower()

    # 7. Deleted dataset -> 404
    deleted_ds = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="to_be_deleted",
        dataset_version="1",
        user_id="user-actuary-1",
    )
    deleted_ds.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(ServiceError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id=deleted_ds.id,
            variable_name="account_value",
            project_id="proj-1",
            governed_execution=True,
        )
    assert exc_info.value.status_code == 404

    # 8. Fingerprint mismatch -> rejected
    approved_tampered = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="tampered_dataset",
        dataset_version="1",
        user_id="user-actuary-1",
    )
    approve_derived_dataset(db_session, "proj-1", approved_tampered.id, "user-lead-2")
    # Tamper recorded fingerprint
    approved_tampered.fingerprint = "0000000000000000000000000000000000000000000000000000000000000000"
    db_session.commit()
    with pytest.raises(DerivedDatasetGovernanceError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id=approved_tampered.id,
            variable_name="account_value",
            project_id="proj-1",
            governed_execution=True,
            policy_id="POL-100",
            projection_month=1,
        )
    assert "fingerprint mismatch" in exc_info.value.message.lower()


def test_multi_segment_dimensional_resolution_and_ambiguity(db_session, test_environment, tmp_path):
    """Directive 2: Multi-segment RILA resolution and ambiguity rejection."""
    store = test_environment["store"]
    
    # Create multi-segment RILA output rows
    rila_rows = [
        {"policy_id": "POL-999", "account_id": "ACC-INDEX-A", "segment_id": "SEG-BUFFER-1", "scenario_id": "BASE", "projection_month": 12, "account_value": 105000.0, "credited_rate": 0.05},
        {"policy_id": "POL-999", "account_id": "ACC-INDEX-A", "segment_id": "SEG-FLOOR-2", "scenario_id": "BASE", "projection_month": 12, "account_value": 98000.0, "credited_rate": -0.02},
        {"policy_id": "POL-999", "account_id": "ACC-INDEX-A", "segment_id": "SEG-BUFFER-1", "scenario_id": "BASE", "projection_month": 24, "account_value": 112000.0, "credited_rate": 0.07},
    ]
    stored_rila = store.write_rows("run-baseline-1", "outputs", rila_rows, attempt_number=2, schema_version="m2-v1")
    artifact_record = RunArtifact(
        id=str(uuid.uuid4()),
        run_id="run-baseline-1",
        attempt_number=2,
        artifact_type="outputs",
        storage_uri=stored_rila.uri,
        storage_backend="local",
        file_format="parquet",
        row_count=stored_rila.row_count,
        checksum_sha256=stored_rila.checksum_sha256,
        schema_version="m2-v1",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(artifact_record)
    db_session.commit()

    # Promote to derived dataset
    rila_ds = promote_run_to_derived_dataset(
        db=db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="rila_segment_outputs",
        dataset_version="1",
        user_id="user-actuary-1",
        artifact_id=artifact_record.id,
    )
    
    # Assert dimensions_available automatically discovered segment_id and account_id
    assert "segment_id" in rila_ds.dimensions_available
    assert "account_id" in rila_ds.dimensions_available
    assert "policy_id" in rila_ds.dimensions_available
    assert "projection_month" in rila_ds.dimensions_available

    approve_derived_dataset(db_session, "proj-1", rila_ds.id, "user-lead-2")

    # 1. Exact match for segment 1 -> resolves 105000.0
    val_seg1 = lookup_derived_output_value(
        db=db_session,
        dataset_id=rila_ds.id,
        variable_name="account_value",
        project_id="proj-1",
        governed_execution=True,
        policy_id="POL-999",
        account_id="ACC-INDEX-A",
        segment_id="SEG-BUFFER-1",
        projection_month=12,
        scenario_id="BASE",
    )
    assert val_seg1 == pytest.approx(105000.0)

    # 2. Exact match for segment 2 -> resolves 98000.0 (proves segments do NOT cross-resolve!)
    val_seg2 = lookup_derived_output_value(
        db=db_session,
        dataset_id=rila_ds.id,
        variable_name="account_value",
        project_id="proj-1",
        governed_execution=True,
        policy_id="POL-999",
        account_id="ACC-INDEX-A",
        segment_id="SEG-FLOOR-2",
        projection_month=12,
        scenario_id="BASE",
    )
    assert val_seg2 == pytest.approx(98000.0)
    assert val_seg1 != val_seg2

    # 3. Ambiguous match: segment_id omitted for month 12 -> matches 2 rows -> MUST raise AmbiguousDerivedOutputError!
    with pytest.raises(AmbiguousDerivedOutputError) as exc_info:
        lookup_derived_output_value(
            db=db_session,
            dataset_id=rila_ds.id,
            variable_name="account_value",
            project_id="proj-1",
            governed_execution=True,
            policy_id="POL-999",
            account_id="ACC-INDEX-A",
            projection_month=12,
            scenario_id="BASE",
        )
    assert exc_info.value.status_code == 400
    assert "matched 2 rows" in exc_info.value.message

    # 4. Zero matches -> returns None
    val_none = lookup_derived_output_value(
        db=db_session,
        dataset_id=rila_ds.id,
        variable_name="account_value",
        project_id="proj-1",
        governed_execution=True,
        policy_id="POL-999",
        account_id="ACC-INDEX-A",
        segment_id="SEG-NONEXISTENT",
        projection_month=12,
    )
    assert val_none is None

    # 5. Variable resolver integration with multi-segment ProjectionContext
    var_def_rila = VariableDefinition(
        id=str(uuid.uuid4()),
        name="rila_prior_av",
        kind="prior_output",
        source={
            "type": "prior_output",
            "dataset_id": rila_ds.id,
            "column_name": "account_value",
        },
        required=True,
    )
    register(db_session, var_def_rila, actor_user_id="user-actuary-1")

    ctx_seg1 = ProjectionContext(
        project_id="proj-1",
        policy_id="POL-999",
        account_id="ACC-INDEX-A",
        segment_id="SEG-BUFFER-1",
        projection_month=12,
        scenario_id="BASE",
    )
    res_seg1 = resolve("rila_prior_av", ctx_seg1, db_session)
    assert res_seg1.value == pytest.approx(105000.0)

    ctx_seg2 = ProjectionContext(
        project_id="proj-1",
        policy_id="POL-999",
        account_id="ACC-INDEX-A",
        segment_id="SEG-FLOOR-2",
        projection_month=12,
        scenario_id="BASE",
    )
    res_seg2 = resolve("rila_prior_av", ctx_seg2, db_session)
    assert res_seg2.value == pytest.approx(98000.0)


def test_prior_output_variable_resolver_does_not_default_on_governance_errors(db_session, test_environment):
    """Directive 3: Governance/security/ambiguity errors MUST NOT default in Variable Resolver.

    Proves:
    1. Cross-project dataset -> raises not_found (404), does NOT return default_value.
    2. Unapproved dataset -> raises DerivedDatasetGovernanceError, does NOT return default_value.
    3. Superseded dataset -> raises DerivedDatasetGovernanceError, does NOT return default_value.
    4. Fingerprint mismatch -> raises DerivedDatasetGovernanceError, does NOT return default_value.
    5. Ambiguous multi-segment match -> raises AmbiguousDerivedOutputError, does NOT return default_value.
    6. Legitimate missing data row (valid approved source) -> DOES return default_value safely.
    """
    env = test_environment
    store = env["store"]

    # Seed run and parquet artifact in proj-1
    run = Run(id="run-gov-test", project_id="proj-1", status="success", run_type="projection")
    db_session.add(run)
    db_session.commit()

    rows = [
        {"policy_id": "P1", "segment_id": "S1", "projection_month": 1, "value": 100.0},
        {"policy_id": "P1", "segment_id": "S2", "projection_month": 1, "value": 200.0},
    ]
    stored = store.write_rows("run-gov-test", "outputs", rows, attempt_number=1, schema_version="m2-v1")
    art = RunArtifact(
        id=str(uuid.uuid4()),
        run_id="run-gov-test",
        attempt_number=1,
        artifact_type="outputs",
        storage_uri=stored.uri,
        checksum_sha256=stored.checksum_sha256,
        row_count=stored.row_count,
        storage_backend="local",
        schema_version="m2-v1",
    )
    db_session.add(art)
    db_session.commit()

    # Promote to derived dataset
    ds = promote_run_to_derived_dataset(
        db_session,
        project_id="proj-1",
        run_id="run-gov-test",
        dataset_name="gov_test_ds",
        dataset_version="1",
        user_id="user-actuary-1",
        artifact_id=art.id,
    )

    # Register variable with an explicit default_value
    var_name = "test_gov_protected_var"
    var_def = VariableDefinition(
        id=str(uuid.uuid4()),
        name=var_name,
        kind="prior_output",
        source={"type": "prior_output", "dataset_id": ds.id, "column_name": "value"},
        default_value=9999.99,
        required=True,
    )
    register(db_session, var_def, actor_user_id="user-actuary-1")

    # 1. Unapproved dataset: resolution MUST raise DerivedDatasetGovernanceError, NOT return 9999.99
    ctx_unapproved = ProjectionContext(
        project_id="proj-1",
        policy_id="P1",
        segment_id="S1",
        projection_month=1,
    )
    with pytest.raises(DerivedDatasetGovernanceError) as exc_info:
        resolve(var_name, ctx_unapproved, db_session)
    assert "Governed runs only accept approved derived datasets" in exc_info.value.message

    # Approve dataset
    approve_derived_dataset(db_session, "proj-1", ds.id, "user-lead-2")

    # 2. Cross-project request: resolution MUST raise not_found (404), NOT return 9999.99
    ctx_foreign = ProjectionContext(
        project_id="foreign-project-id",
        policy_id="P1",
        segment_id="S1",
        projection_month=1,
    )
    with pytest.raises(ServiceError) as exc_info:
        resolve(var_name, ctx_foreign, db_session)
    assert exc_info.value.status_code == 404

    # 3. Ambiguous match: omitting segment_id matches 2 rows -> MUST raise AmbiguousDerivedOutputError, NOT return 9999.99
    ctx_ambiguous = ProjectionContext(
        project_id="proj-1",
        policy_id="P1",
        projection_month=1,
    )
    with pytest.raises(AmbiguousDerivedOutputError) as exc_info:
        resolve(var_name, ctx_ambiguous, db_session)
    assert exc_info.value.status_code == 400

    # 4. Fingerprint mismatch: tampering physical file -> MUST raise DerivedDatasetGovernanceError, NOT return 9999.99
    file_path = store.path_for_uri(ds.storage_uri)
    original_bytes = file_path.read_bytes()
    try:
        file_path.write_bytes(original_bytes + b"\x00TAMPERED")
        with pytest.raises(DerivedDatasetGovernanceError) as exc_info:
            resolve(var_name, ctx_unapproved, db_session)
        assert "Storage fingerprint mismatch" in exc_info.value.message
    finally:
        file_path.write_bytes(original_bytes)

    # 5. Superseded dataset: MUST raise DerivedDatasetGovernanceError, NOT return 9999.99
    supersede_derived_dataset(db_session, "proj-1", ds.id, "user-lead-2")
    with pytest.raises(DerivedDatasetGovernanceError) as exc_info:
        resolve(var_name, ctx_unapproved, db_session)
    assert "superseded and cannot be consumed" in exc_info.value.message

    # 6. Legitimate missing row on an approved dataset: DOES safely fall back to default_value
    ds2 = promote_run_to_derived_dataset(
        db_session,
        project_id="proj-1",
        run_id="run-baseline-1",
        dataset_name="gov_test_ds_2",
        dataset_version="2",
        user_id="user-actuary-1",
    )
    approve_derived_dataset(db_session, "proj-1", ds2.id, "user-lead-2")
    var_def_2 = VariableDefinition(
        id=str(uuid.uuid4()),
        name="test_gov_missing_row_var",
        kind="prior_output",
        source={"type": "prior_output", "dataset_id": ds2.id, "column_name": "account_value"},
        default_value=7777.77,
        required=True,
    )
    register(db_session, var_def_2, actor_user_id="user-actuary-1")
    ctx_missing_row = ProjectionContext(
        project_id="proj-1",
        policy_id="P_DOES_NOT_EXIST",
        projection_month=1,
    )
    res_missing = resolve("test_gov_missing_row_var", ctx_missing_row, db_session)
    assert res_missing.value == 7777.77
    assert res_missing.was_defaulted is True
