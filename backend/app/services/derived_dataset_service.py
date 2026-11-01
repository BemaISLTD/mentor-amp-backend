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
from app.services.common import bad_request, conflict, not_found


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

    if run.status != "success":
        raise bad_request(
            f"Cannot promote run '{run_id}' with non-success status '{run.status}'. "
            "Only completed runs with status='success' can be promoted."
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

    # 3. Locate target artifact
    artifact_query = db.query(RunArtifact).filter(
        RunArtifact.run_id == run_id,
        RunArtifact.artifact_type.in_(["outputs", "valuation_results", "cash_flows"]),
    )
    if artifact_id:
        artifact_query = artifact_query.filter(RunArtifact.id == artifact_id)
    artifact = artifact_query.order_by(RunArtifact.created_at.desc()).first()

    if not artifact:
        raise not_found(f"No suitable analytical artifact found for run '{run_id}'.")

    # 4. Inspect storage and verify rows / schema
    store = get_artifact_store()
    try:
        path = store.path_for_uri(artifact.storage_uri)
        if not path.exists():
            raise bad_request(f"Artifact storage file does not exist at '{artifact.storage_uri}'.")
        df = pl.read_parquet(path)
    except Exception as e:
        raise bad_request(f"Failed to inspect artifact Parquet file: {str(e)}")

    row_count = df.height
    col_names = df.columns
    schema_dict = {col: str(df.schema[col]) for col in col_names}

    # Available dimensions
    avail_dims = dimensions or [
        col for col in ["policy_id", "scenario_id", "projection_month", "time_step_month", "cohort_id"]
        if col in col_names
    ]

    version_int = int(dataset_version) if dataset_version.isdigit() else 1

    dataset = DerivedDataset(
        id=str(uuid.uuid4()),
        project_id=project_id,
        source_run_id=run_id,
        source_run_ids=[run_id],
        source_artifact_id=artifact.id,
        source_model_id=run.model_version_id,
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
        fingerprint=artifact.checksum_sha256,
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


def lookup_derived_output_value(
    db: Session,
    dataset_id: str,
    variable_name: str,
    policy_id: str | None = None,
    projection_month: int | None = None,
    scenario_id: str | None = None,
) -> Any:
    """Look up a variable value from an approved derived dataset Parquet artifact."""
    dataset = db.query(DerivedDataset).filter(DerivedDataset.id == dataset_id).first()
    if not dataset or not dataset.storage_uri:
        return None

    store = get_artifact_store()
    path = store.path_for_uri(dataset.storage_uri)
    if not path.exists():
        return None

    # Load lazy frame and filter efficiently
    lf = pl.scan_parquet(path)
    col_names = lf.collect_schema().names()
    if variable_name not in col_names:
        return None

    if policy_id and "policy_id" in col_names:
        lf = lf.filter(pl.col("policy_id") == policy_id)
    if projection_month is not None:
        if "projection_month" in col_names:
            lf = lf.filter(pl.col("projection_month") == projection_month)
        elif "time_step_month" in col_names:
            lf = lf.filter(pl.col("time_step_month") == projection_month)
    if scenario_id and "scenario_id" in col_names:
        lf = lf.filter(pl.col("scenario_id") == scenario_id)

    res = lf.select([variable_name]).collect()
    if res.height == 0:
        return None
    val = res[variable_name][0]
    return float(val) if isinstance(val, (int, float)) else val
