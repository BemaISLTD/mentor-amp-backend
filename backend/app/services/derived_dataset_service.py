"""Derived Dataset Registry & Promotion Service (Work Package M5B).

Authoritative Launch Requirement: LR1-CORE-028 & CAS §5.
Enables a completed run's Parquet outputs to be promoted to an immutable,
governed dataset that serves as input (kind='prior_output') to downstream
valuation or sensitivity runs.

Enforces:
1. Zero IDOR leakage: cross-project queries raise not_found (HTTP 404).
2. Four-eyes segregation of duties: creator cannot approve their own dataset.
3. Immutability: approved datasets cannot be modified (except marked superseded).
4. Content fingerprinting: SHA-256 integrity checks against Parquet storage.
5. Accepted-attempt artifact verification on promotion.
"""

from datetime import datetime, timezone
import hashlib
from typing import Any
import uuid

import polars as pl
from sqlalchemy.orm import Session

from app.core.artifacts import get_artifact_store
from app.db.models.governance import DerivedDataset
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.services.common import ServiceError, bad_request, conflict, not_found


def _now() -> datetime:
    return datetime.now(timezone.utc)


def promote_run_to_derived_dataset(
    db: Session,
    project_id: str,
    run_id: str,
    dataset_name: str,
    dataset_version: str,
    user_id: str,
    description: str | None = None,
    product_code: str | None = None,
    artifact_id: str | None = None,
    dimensions: list[str] | None = None,
) -> DerivedDataset:
    """Promote an analytical Parquet artifact from a completed run into a governed DerivedDataset."""
    # 1. Scoped run verification (prevent IDOR leakage)
    run = db.query(Run).filter(Run.id == run_id, Run.project_id == project_id).first()
    if not run:
        raise not_found(f"Run '{run_id}' not found in project '{project_id}'.")

    if run.status not in ("success", "completed"):
        raise bad_request(
            f"Cannot promote run '{run_id}' with non-success status '{run.status}'. "
            "Only completed runs with status in ('success', 'completed') can be promoted."
        )

    # 2. Check name/version uniqueness in project
    existing = db.query(DerivedDataset).filter(
        DerivedDataset.project_id == project_id,
        DerivedDataset.name == dataset_name,
        DerivedDataset.version_number == (int(dataset_version) if dataset_version.isdigit() else 1),
    ).first()
    if existing:
        raise conflict(
            f"Derived dataset '{dataset_name}' version '{dataset_version}' already exists in this project."
        )

    # 3. Locate target artifact from accepted attempt
    canonical_types = ("outputs", "valuation_results", "cash_flows")
    if artifact_id:
        artifact = db.query(RunArtifact).filter(
            RunArtifact.id == artifact_id,
            RunArtifact.run_id == run_id,
        ).first()
        if not artifact:
            raise not_found(f"Artifact '{artifact_id}' not found for run '{run_id}'.")
        if artifact.artifact_type not in canonical_types:
            raise bad_request(
                f"Artifact '{artifact_id}' has unsupported type '{artifact.artifact_type}' for dataset promotion."
            )
        if run.accepted_attempt_number is not None and artifact.attempt_number is not None:
            if artifact.attempt_number != run.accepted_attempt_number:
                raise bad_request(
                    f"Artifact '{artifact_id}' belongs to attempt {artifact.attempt_number}, "
                    f"not accepted attempt {run.accepted_attempt_number}."
                )
    else:
        artifact_query = db.query(RunArtifact).filter(
            RunArtifact.run_id == run_id,
            RunArtifact.artifact_type.in_(list(canonical_types)),
        )
        if run.accepted_attempt_number is not None:
            artifact_query = artifact_query.filter(
                RunArtifact.attempt_number == run.accepted_attempt_number
            )
        artifact = artifact_query.order_by(RunArtifact.created_at.desc()).first()

    if not artifact:
        raise not_found(f"No suitable analytical artifact found for run '{run_id}'.")

    # 4. Inspect storage, verify SHA-256 checksum, and verify rows / schema
    store = get_artifact_store()
    try:
        path = store.path_for_uri(artifact.storage_uri)
        if not path.exists():
            raise bad_request(f"Artifact storage file does not exist at '{artifact.storage_uri}'.")
        raw_bytes = path.read_bytes()
        computed_sha = hashlib.sha256(raw_bytes).hexdigest()
        if artifact.checksum_sha256 and computed_sha != artifact.checksum_sha256:
            raise bad_request(
                f"Artifact storage SHA-256 mismatch during promotion: expected {artifact.checksum_sha256}, got {computed_sha}."
            )
        df = pl.read_parquet(path)
    except ServiceError:
        raise
    except Exception as e:
        raise bad_request(f"Failed to inspect artifact Parquet file: {str(e)}")

    row_count = df.height
    col_names = df.columns
    schema_dict = {col: str(df.schema[col]) for col in col_names}

    # Available canonical dimensions (Directive 2)
    CANONICAL_DIMENSIONS = [
        "policy_id", "coverage_id", "rider_id", "account_id",
        "segment_id", "cohort_id", "scenario_id",
        "projection_month", "time_step_month"
    ]
    if dimensions is not None:
        avail_dims = list(dimensions)
    else:
        avail_dims = [col for col in CANONICAL_DIMENSIONS if col in col_names]
        for col in col_names:
            if col.endswith("_id") and col not in avail_dims and col not in {"source_run_id", "artifact_id", "user_id"}:
                avail_dims.append(col)

    version_int = int(dataset_version) if dataset_version.isdigit() else 1

    # Resolve model lineage correctly (Directive 13)
    source_model_id = None
    source_model_version_id = None
    if run.model_version_id:
        from app.db.models.modeling import ModelVersion
        mv = db.query(ModelVersion).filter(ModelVersion.id == run.model_version_id).first()
        if mv:
            source_model_id = mv.model_id
            source_model_version_id = mv.id
        else:
            source_model_version_id = run.model_version_id

    dataset = DerivedDataset(
        id=str(uuid.uuid4()),
        project_id=project_id,
        source_run_id=run_id,
        source_run_ids=[run_id],
        source_artifact_id=artifact.id,
        source_model_id=source_model_id,
        source_model_version_id=source_model_version_id,
        name=dataset_name,
        dataset_type="projection_output" if run.run_type == "projection" else "valuation_output",
        description=description,
        version_number=version_int,
        product_code=product_code or run.projection_key,
        status="draft",
        schema=schema_dict,
        storage_uri=artifact.storage_uri,
        storage_backend="local",
        file_format="parquet",
        row_count=row_count,
        fingerprint=artifact.checksum_sha256 or computed_sha,
        dimensions_available=avail_dims,
        created_by=user_id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


def get_derived_dataset(
    db: Session,
    project_id: str,
    dataset_id: str,
) -> DerivedDataset:
    """Fetch a derived dataset strictly scoped to a project_id (raises 404 on cross-tenant access)."""
    dataset = db.query(DerivedDataset).filter(
        DerivedDataset.id == dataset_id,
        DerivedDataset.project_id == project_id,
        DerivedDataset.deleted_at.is_(None),
    ).first()
    if not dataset:
        raise not_found(f"Derived dataset '{dataset_id}' not found in project '{project_id}'.")
    return dataset


def list_derived_datasets(
    db: Session,
    project_id: str,
    status: str | None = None,
) -> list[DerivedDataset]:
    """List derived datasets for a project."""
    q = db.query(DerivedDataset).filter(
        DerivedDataset.project_id == project_id,
        DerivedDataset.deleted_at.is_(None),
    )
    if status:
        q = q.filter(DerivedDataset.status == status)
    return q.order_by(DerivedDataset.name.asc(), DerivedDataset.version_number.desc()).all()


def validate_derived_dataset(
    db: Session,
    project_id: str,
    dataset_id: str,
    user_id: str,
) -> DerivedDataset:
    """Verify physical Parquet integrity and mark dataset as validated."""
    dataset = get_derived_dataset(db, project_id, dataset_id)
    if dataset.status in ("approved", "superseded"):
        raise bad_request(f"Cannot re-validate dataset in status '{dataset.status}'.")

    store = get_artifact_store()
    path = store.path_for_uri(dataset.storage_uri)
    if not path.exists():
        raise bad_request(f"Dataset Parquet file is missing from '{dataset.storage_uri}'.")

    # Checksum validation
    content = path.read_bytes()
    computed_sha = hashlib.sha256(content).hexdigest()
    if dataset.fingerprint and computed_sha != dataset.fingerprint:
        raise bad_request(
            f"Checksum mismatch for dataset '{dataset_id}'. Expected {dataset.fingerprint}, got {computed_sha}."
        )

    dataset.status = "validated"
    dataset.updated_by = user_id
    dataset.updated_at = _now()
    db.commit()
    db.refresh(dataset)
    return dataset


def approve_derived_dataset(
    db: Session,
    project_id: str,
    dataset_id: str,
    approved_by_user_id: str,
) -> DerivedDataset:
    """Approve a derived dataset for downstream production consumption.

    Enforces four-eyes segregation of duties: creator cannot approve their own dataset.
    """
    dataset = get_derived_dataset(db, project_id, dataset_id)

    if dataset.created_by == approved_by_user_id:
        raise bad_request("Segregation of duties violation: Creator cannot approve their own derived dataset.")

    if dataset.status == "approved":
        return dataset

    if dataset.status != "validated":
        # Run validation check first
        validate_derived_dataset(db, project_id, dataset_id, approved_by_user_id)

    dataset.status = "approved"
    dataset.approved_by = approved_by_user_id
    dataset.approved_at = _now()
    dataset.published_by = approved_by_user_id
    dataset.published_at = _now()
    dataset.updated_by = approved_by_user_id
    dataset.updated_at = _now()
    db.commit()
    db.refresh(dataset)
    return dataset


def supersede_derived_dataset(
    db: Session,
    project_id: str,
    dataset_id: str,
    user_id: str,
) -> DerivedDataset:
    """Mark an approved dataset as superseded by a newer version."""
    dataset = get_derived_dataset(db, project_id, dataset_id)
    dataset.status = "superseded"
    dataset.updated_by = user_id
    dataset.updated_at = _now()
    db.commit()
    db.refresh(dataset)
    return dataset


class AmbiguousDerivedOutputError(ServiceError):
    """Raised when dimensional lookup matches multiple rows (Directive 2)."""
    def __init__(self, message: str, **details: Any):
        super().__init__(400, "AMBIGUOUS_DERIVED_OUTPUT", message, details or None)


class DerivedDatasetGovernanceError(ServiceError):
    """Raised when governance state of derived dataset rejects consumption (Directive 1)."""
    def __init__(self, message: str, **details: Any):
        super().__init__(400, "DERIVED_DATASET_GOVERNANCE_ERROR", message, details or None)


def lookup_derived_output_value(
    db: Session,
    dataset_id: str,
    variable_name: str,
    project_id: str | None = None,
    governed_execution: bool = True,
    policy_id: str | None = None,
    coverage_id: str | None = None,
    rider_id: str | None = None,
    account_id: str | None = None,
    segment_id: str | None = None,
    cohort_id: str | None = None,
    projection_month: int | None = None,
    scenario_id: str | None = None,
    **extra_dimensions: Any,
) -> Any:
    """Look up a variable value from an approved derived dataset Parquet artifact.

    Enforces Directive 1 & 2:
    - Same-project isolation: cross-project lookups return HTTP 404 (zero IDOR leakage).
    - Governed execution: dataset must be active, non-deleted, non-superseded, and status='approved'.
    - Storage integrity: SHA-256 fingerprint matches stored fingerprint.
    - Dimensional resolution: filters on policy_id, coverage_id, rider_id, account_id,
      segment_id, cohort_id, scenario_id, projection_month/time_step_month.
    - Ambiguity rejection: matches > 1 row raises AmbiguousDerivedOutputError (NEVER select [0]).
    - Zero matches: returns None (missing input).
    - Exactly 1 match: returns scalar value.
    """
    if project_id is not None:
        dataset = db.query(DerivedDataset).filter(
            DerivedDataset.id == dataset_id,
            DerivedDataset.project_id == project_id,
        ).first()
        if not dataset:
            raise not_found(f"Derived dataset '{dataset_id}' not found.")
    else:
        if governed_execution:
            raise DerivedDatasetGovernanceError(
                "Governed execution requires an active project_id context for derived dataset resolution."
            )
        dataset = db.query(DerivedDataset).filter(DerivedDataset.id == dataset_id).first()
        if not dataset:
            raise not_found(f"Derived dataset '{dataset_id}' not found.")

    if dataset.deleted_at is not None:
        raise not_found(f"Derived dataset '{dataset_id}' not found.")

    if dataset.status == "superseded":
        raise DerivedDatasetGovernanceError(
            f"Derived dataset '{dataset_id}' is superseded and cannot be consumed in governed runs."
        )

    if governed_execution and dataset.status != "approved":
        raise DerivedDatasetGovernanceError(
            f"Derived dataset '{dataset_id}' has governance status '{dataset.status}'. "
            "Governed runs only accept approved derived datasets."
        )

    if not dataset.storage_uri:
        raise DerivedDatasetGovernanceError(f"Derived dataset '{dataset_id}' has no storage URI.")

    store = get_artifact_store()
    path = store.path_for_uri(dataset.storage_uri)
    if not path.exists():
        raise DerivedDatasetGovernanceError(
            f"Artifact storage file for dataset '{dataset_id}' not found at '{dataset.storage_uri}'."
        )

    # Verify fingerprint integrity
    if dataset.fingerprint:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        actual_sha = hasher.hexdigest()
        if actual_sha != dataset.fingerprint:
            raise DerivedDatasetGovernanceError(
                f"Storage fingerprint mismatch for dataset '{dataset_id}'! "
                f"Recorded='{dataset.fingerprint}', actual='{actual_sha}'."
            )

    lf = pl.scan_parquet(path)
    col_names = lf.collect_schema().names()
    if variable_name not in col_names:
        return None

    applied_filters: dict[str, Any] = {}
    dim_candidates = {
        "policy_id": policy_id,
        "coverage_id": coverage_id,
        "rider_id": rider_id,
        "account_id": account_id,
        "segment_id": segment_id,
        "cohort_id": cohort_id,
        "scenario_id": scenario_id,
        **extra_dimensions,
    }
    for dim_key, dim_val in dim_candidates.items():
        if dim_val is not None and dim_key in col_names:
            lf = lf.filter(pl.col(dim_key) == dim_val)
            applied_filters[dim_key] = dim_val

    if projection_month is not None:
        if "projection_month" in col_names:
            lf = lf.filter(pl.col("projection_month") == projection_month)
            applied_filters["projection_month"] = projection_month
        elif "time_step_month" in col_names:
            lf = lf.filter(pl.col("time_step_month") == projection_month)
            applied_filters["time_step_month"] = projection_month

    res = lf.select([variable_name]).collect()
    count = res.height
    if count == 0:
        return None
    if count > 1:
        raise AmbiguousDerivedOutputError(
            f"Ambiguous derived output: variable '{variable_name}' in dataset '{dataset_id}' "
            f"matched {count} rows. Expected exactly 1 row. Applied filters: {applied_filters}"
        )

    val = res[variable_name][0]
    return float(val) if isinstance(val, (int, float)) else val
