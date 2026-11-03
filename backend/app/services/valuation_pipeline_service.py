"""Actuarial Valuation Pipeline Domain Service (M5 Directives & V5 Senior Review).

Orchestration Invariant:
    All actuarial formulas, data persistence, and calculations live in domain services
    and the shared calculation engine, never inside Dagster asset bodies.
    Dagster assets act strictly as orchestrators: validating inputs, invoking this service,
    emitting lineage metadata, and routing failures/retries.

Sequence:
    1. Governed Inforce Import / Ingestion (from validated/approved InforceFile & InforceRecord)
    2. Governed Inforce Validation (via shared inforce validator + integrity checks)
    3. Governed Projection Execution (via shared projection engine, ProjectionSet, ModelVersion, FormulaRegistry)
    4. Verified Columnar Aggregation (Polars from verified RunArtifact Parquet; flows sum, stocks end-of-period)
    5. Foundation Valuation (Time-value-of-money generic PV discounting; gated VM21/VM22)
    6. Valuation-Linked Rollforward Attribution (Decimal continuity validation & component persistence)
    7. Governed Reconciliation (Approved ExpectedResultSet only: PASS, FAIL, NOT_VERIFIABLE)
    8. Reporting & Audit Manifest Export
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
import uuid

import polars as pl
from sqlalchemy.orm import Session

from app.core import lifecycle
from app.core.artifacts import get_artifact_store
from app.core.execution import run_package as core_run_pkg
from app.core.execution import run_state
from app.core.execution.fingerprints import (
    INFORCE_FINGERPRINT_SCHEME,
    SUPPORTED_INFORCE_FINGERPRINT_SCHEMES,
    fingerprint as compute_fp,
    inforce_fingerprint,
)
from app.core.valuation.primitives import (
    annual_to_monthly_discount_factor,
    present_value,
)
from app.core.valuation.rollforward import (
    get_methodology_template,
    record_rollforward_summary,
    validate_continuity,
)
from app.core.valuation.taxonomy import (
    validate_basis,
    validate_basis_methodology_pair,
    validate_methodology,
)
from app.data.validation.inforce_validator import validate_inforce
from app.db.models.assumption import AssumptionSet, AssumptionTable
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.modeling import Model, ModelVersion
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet, RunSet
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunPackage
from app.db.models.scenario import ScenarioSet, ScenarioTable
from app.db.models.user import User
from app.db.models.valuation_detail import ValuationRunDetail
from app.services import (
    projection_set_service,
    run_execution_service,
    run_package_service,
)
from app.services.common import ServiceError, iso, now_utc
from app.services.run_package_service import freeze_valuation_run_package


def _normalize_product_family(code: str | None) -> str:
    if not code:
        return "UNKNOWN"
    low = str(code).strip().lower()
    if "myga" in low:
        return "MYGA"
    if "spia" in low:
        return "SPIA"
    if "fia" in low:
        return "FIA"
    if "rila" in low:
        return "RILA"
    return str(code).strip().upper()


def execute_inforce_import(
    db: Session,
    project_id: str,
    inforce_file_id: str | None = None,
    policy_block: str = "BLOCK_MYGA_2026",
) -> dict[str, Any]:
    """Load and verify a governed InforceFile and its InforceRecords from PostgreSQL."""
    started_at = datetime.now(timezone.utc).isoformat()

    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed inforce import requires an active database session.")

    project = db.get(Project, project_id)
    if not project:
        raise ServiceError(404, "PROJECT_NOT_FOUND", f"Project '{project_id}' not found.")

    inforce_file: InforceFile | None = None
    if inforce_file_id:
        inforce_file = db.get(InforceFile, inforce_file_id)
        if inforce_file is None or inforce_file.project_id != project_id:
            raise ServiceError(
                404, "INFORCE_NOT_FOUND",
                f"Inforce file '{inforce_file_id}' not found in project '{project_id}'.",
            )
    else:
        # Resolve by filename/version_label matching policy_block, or active governed file in project
        q = db.query(InforceFile).filter(InforceFile.project_id == project_id)
        if policy_block and policy_block != "BLOCK_MYGA_2026":
            inforce_file = (
                q.filter(
                    (InforceFile.id == policy_block)
                    | (InforceFile.filename == policy_block)
                    | (InforceFile.version_label == policy_block)
                )
                .order_by(InforceFile.uploaded_at.desc())
                .first()
            )
            if inforce_file is None:
                raise ServiceError(
                    404, "INFORCE_NOT_FOUND",
                    f"Inforce file matching '{policy_block}' not found in project '{project_id}'.",
                )
        else:
            inforce_file = (
                q.filter(InforceFile.status.in_(lifecycle.RUNNABLE_INPUT_STATUSES))
                .order_by(InforceFile.uploaded_at.desc())
                .first()
            )
            if inforce_file is None:
                inforce_file = q.order_by(InforceFile.uploaded_at.desc()).first()

        if inforce_file is None:
            raise ServiceError(
                404, "INFORCE_NOT_FOUND",
                f"No governed InforceFile found in project '{project_id}'. Import and approve an inforce dataset first.",
            )

    if inforce_file.status not in lifecycle.RUNNABLE_INPUT_STATUSES:
        raise ServiceError(
            400, "INFORCE_NOT_APPROVED",
            f"Inforce file '{inforce_file.id}' has status '{inforce_file.status}'; governed execution requires validated/approved inforce.",
        )

    if not inforce_file.fingerprint or inforce_file.fingerprint_scheme not in SUPPORTED_INFORCE_FINGERPRINT_SCHEMES:
        raise ServiceError(
            400, "INFORCE_FINGERPRINT_INVALID",
            f"Inforce file '{inforce_file.id}' does not have a valid '{INFORCE_FINGERPRINT_SCHEME}' fingerprint.",
        )

    records = (
        db.query(InforceRecord)
        .filter(InforceRecord.file_id == inforce_file.id)
        .order_by(InforceRecord.policy_id, InforceRecord.id)
        .all()
    )
    if not records:
        raise ServiceError(
            422, "INFORCE_VALIDATION_EMPTY",
            f"Inforce file '{inforce_file.id}' contains 0 policy records.",
        )

    computed_fp = inforce_fingerprint((rec.policy_id, rec.data) for rec in records)
    if computed_fp != inforce_file.fingerprint:
        raise ServiceError(
            400, "INFORCE_FINGERPRINT_MISMATCH",
            f"Inforce file '{inforce_file.id}' records do not match recorded fingerprint.",
        )

    policies: list[dict[str, Any]] = []
    for rec in records:
        row_data = dict(rec.data)
        row_data.setdefault("policy_id", rec.policy_id)
        policies.append(row_data)

    store = get_artifact_store()
    stored = store.write_rows(
        run_id=f"inforce-{inforce_file.id}",
        artifact_type="inforce",
        rows=policies,
        attempt_number=1,
        schema_version="m2-v1",
    )
    completed_at = datetime.now(timezone.utc).isoformat()

    return {
        "status": "ingested",
        "project_id": project_id,
        "inforce_file_id": inforce_file.id,
        "filename": inforce_file.filename,
        "inforce_status": inforce_file.status,
        "policy_block": policy_block or inforce_file.filename,
        "record_count": len(policies),
        "fingerprint": computed_fp,
        "fingerprint_scheme": inforce_file.fingerprint_scheme,
        "policies": policies,
        "storage_uri": stored.uri,
        "artifact_checksum_sha256": stored.checksum_sha256,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def execute_inforce_validation(
    inforce_payload: dict[str, Any],
    db: Session | None = None,
) -> dict[str, Any]:
    """Validate policy records against governed actuarial acceptance rules."""
    started_at = datetime.now(timezone.utc).isoformat()
    policies = inforce_payload.get("policies", [])

    if not policies or len(policies) == 0:
        raise ServiceError(
            422,
            "INFORCE_VALIDATION_EMPTY",
            "Inforce validation failed: Block contains 0 records.",
        )

    seen_ids: set[str] = set()
    errors: list[str] = []

    for idx, p in enumerate(policies):
        pid = str(p.get("policy_id") or "").strip()
        if not pid or pid in seen_ids:
            errors.append(f"Row {idx}: Invalid or duplicate policy_id '{pid}'")
        seen_ids.add(pid)

        age_raw = p.get("issue_age")
        try:
            age = int(float(age_raw)) if age_raw is not None else None
        except (ValueError, TypeError):
            age = None
        if age is None or not (0 <= age <= 120):
            errors.append(f"Row {idx} ({pid}): Invalid issue_age {age_raw}")

        for num_field in ("premium", "account_value", "modal_payment", "mgsv_value"):
            if num_field in p and p[num_field] is not None and str(p[num_field]).strip() != "":
                try:
                    val = float(p[num_field])
                    if val < 0:
                        errors.append(f"Row {idx} ({pid}): Negative {num_field} {val}")
                except (ValueError, TypeError):
                    errors.append(f"Row {idx} ({pid}): Non-numeric {num_field} {p[num_field]}")

    # Also invoke the canonical inforce validator when standard columns are present
    validator_errors = validate_inforce(policies)
    for verr in validator_errors:
        errors.append(str(verr))

    if errors:
        raise ServiceError(
            422,
            "INFORCE_VALIDATION_CORRUPT",
            f"Inforce validation failed with {len(errors)} errors: {'; '.join(errors[:3])}",
        )

    inforce_file_id = inforce_payload.get("inforce_file_id")
    project_id = inforce_payload.get("project_id")
    if db is not None and inforce_file_id:
        inforce_file = db.get(InforceFile, inforce_file_id)
        if inforce_file is None or (project_id and inforce_file.project_id != project_id):
            raise ServiceError(404, "INFORCE_NOT_FOUND", f"Inforce file '{inforce_file_id}' not found in project.")
        if inforce_file.status not in lifecycle.RUNNABLE_INPUT_STATUSES:
            raise ServiceError(400, "INFORCE_NOT_APPROVED", f"Inforce file '{inforce_file_id}' is not validated/approved.")
        if inforce_file.fingerprint != inforce_payload.get("fingerprint"):
            raise ServiceError(400, "INFORCE_FINGERPRINT_MISMATCH", "Inforce payload fingerprint disagrees with database.")

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "validation_status": "clean",
        "error_count": 0,
        "warning_count": 0,
        "record_count": len(policies),
        "fingerprint": inforce_payload["fingerprint"],
        "inforce_file_id": inforce_file_id,
        "project_id": project_id,
        "policy_block": inforce_payload.get("policy_block"),
        "storage_uri": inforce_payload.get("storage_uri"),
        "validated_policies": policies,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def execute_projection_simulation(
    db: Session,
    project_id: str,
    validated_inforce: dict[str, Any],
    projection_months: int = 12,
    annual_credited_rate: float = 0.045,
    annual_surrender_rate: float = 0.02,
    run_id: str | None = None,
    projection_set_id: str | None = None,
    model_version_id: str | None = None,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """Execute governed projection via shared projection engine, ModelVersion, FormulaRegistry, and RunPackage."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed projection execution requires an active database session.")

    project = db.get(Project, project_id)
    if not project:
        raise ServiceError(404, "PROJECT_NOT_FOUND", f"Project '{project_id}' not found.")

    policies = validated_inforce.get("validated_policies") or validated_inforce.get("policies") or []
    if not policies:
        raise ServiceError(422, "INFORCE_VALIDATION_EMPTY", "Cannot execute projection on empty inforce block.")

    inforce_file_id = validated_inforce.get("inforce_file_id")
    if not inforce_file_id:
        raise ServiceError(
            400, "INFORCE_FILE_REQUIRED",
            "Governed projection requires a persisted, fingerprinted inforce_file_id from Data Manager.",
        )
    inforce_file = db.get(InforceFile, inforce_file_id)
    if inforce_file is None or inforce_file.project_id != project_id:
        raise ServiceError(404, "INFORCE_NOT_FOUND", f"Inforce file '{inforce_file_id}' not found in project '{project_id}'.")
    if inforce_file.status not in lifecycle.RUNNABLE_INPUT_STATUSES:
        raise ServiceError(400, "INFORCE_NOT_APPROVED", f"Inforce file '{inforce_file_id}' is not validated/approved.")

    # Determine product family from validated inforce records
    policy_families = {
        _normalize_product_family(p.get("product_type") or p.get("product_code"))
        for p in policies
    }
    policy_families.discard("UNKNOWN")
    if len(policy_families) > 1:
        raise ServiceError(
            400, "HETEROGENEOUS_INFORCE_BLOCK",
            f"Inforce block contains mixed product families: {sorted(policy_families)}.",
        )
    inforce_family = next(iter(policy_families)) if policy_families else "MYGA"

    # Resolve ProjectionSet / ModelVersion / Model from PostgreSQL
    ps: ProjectionSet | None = None
    mv: ModelVersion | None = None
    model: Model | None = None

    if projection_set_id:
        ps = db.get(ProjectionSet, projection_set_id)
        if ps is None or ps.project_id != project_id:
            raise ServiceError(
                404, "PROJECTION_SET_NOT_FOUND",
                f"ProjectionSet '{projection_set_id}' not found in project '{project_id}'.",
            )
        if ps.status not in lifecycle.RUNNABLE_PROJECTION_SET_STATUSES:
            raise ServiceError(
                400, "PROJECTION_SET_NOT_VALIDATED",
                f"ProjectionSet '{projection_set_id}' is not validated (status: {ps.status}).",
            )
        if model_version_id and ps.model_version_id != model_version_id:
            raise ServiceError(
                400, "MODEL_VERSION_MISMATCH",
                f"ProjectionSet model_version_id '{ps.model_version_id}' conflicts with requested '{model_version_id}'.",
            )
        mv = db.get(ModelVersion, ps.model_version_id)

    if model_version_id and mv is None:
        mv = db.get(ModelVersion, model_version_id)
        if mv is None:
            raise ServiceError(404, "MODEL_VERSION_NOT_FOUND", f"ModelVersion '{model_version_id}' not found.")
        model = db.get(Model, mv.model_id)
        if model is None or model.project_id != project_id:
            raise ServiceError(
                404, "MODEL_VERSION_NOT_FOUND",
                f"ModelVersion '{model_version_id}' not found in project '{project_id}'.",
            )

    if mv is None:
        # Find active governed Model + ModelVersion in project matching the inforce product family
        project_models = (
            db.query(Model)
            .filter(Model.project_id == project_id, Model.deleted_at.is_(None))
            .all()
        )
        matching_models = [
            m for m in project_models
            if _normalize_product_family(m.product_code) == inforce_family
        ]
        candidate_models = matching_models or project_models
        for cand_m in candidate_models:
            cand_mv = (
                db.query(ModelVersion)
                .filter(
                    ModelVersion.model_id == cand_m.id,
                    ModelVersion.deleted_at.is_(None),
                )
                .order_by(ModelVersion.is_current.desc(), ModelVersion.version_number.desc())
                .first()
            )
            if cand_mv is not None:
                mv = cand_mv
                model = cand_m
                break

    if mv is None:
        raise ServiceError(
            404, "MODEL_VERSION_NOT_FOUND",
            f"No governed ModelVersion found in project '{project_id}'.",
        )
    if model is None:
        model = db.get(Model, mv.model_id)
    if model is None or model.project_id != project_id:
        raise ServiceError(
            404, "MODEL_NOT_FOUND",
            f"Model for version '{mv.id}' not found in project '{project_id}'.",
        )
    if model.deleted_at is not None or mv.deleted_at is not None:
        raise ServiceError(400, "MODEL_DELETED", f"Model '{model.id}' or version '{mv.id}' is deleted.")

    # Require approved / published (or validated with approval evidence) ModelVersion
    if mv.status not in ("approved", "published") and mv.approved_at is None:
        raise ServiceError(
            400, "MODEL_VERSION_NOT_APPROVED",
            f"ModelVersion '{mv.id}' (status: '{mv.status}') must be approved for governed execution.",
        )

    # Enforce strict product family match between inforce policies and Model.product_code (P0-02)
    model_family = _normalize_product_family(model.product_code)
    if inforce_family != model_family:
        raise ServiceError(
            400, "PRODUCT_MODEL_MISMATCH",
            f"Inforce product family '{inforce_family}' mismatches Model '{model.id}' product_code '{model.product_code}' ({model_family}).",
        )

    # Resolve actor
    actor = db.get(User, project.created_by) if project.created_by else None
    if actor is None:
        actor = db.query(User).first()

    # Resolve or build governed ProjectionSet bound to the verified inforce file and model version
    if ps is None:
        ps = (
            db.query(ProjectionSet)
            .filter(
                ProjectionSet.project_id == project_id,
                ProjectionSet.model_version_id == mv.id,
            )
            .order_by(ProjectionSet.created_at.desc())
            .first()
        )

    # Ensure assumption tables, factor tables, and scenarios are resolved from the project
    if ps is None or ps.inforce_file_ids != [inforce_file.id] or ps.horizon_months != projection_months:
        assumption_tables = (
            db.query(AssumptionTable)
            .join(AssumptionSet, AssumptionTable.set_id == AssumptionSet.id)
            .filter(
                AssumptionSet.project_id == project_id,
                AssumptionTable.status.in_(lifecycle.RUNNABLE_INPUT_STATUSES),
            )
            .all()
        )
        factor_tables = (
            db.query(FactorTable)
            .join(FactorSet, FactorTable.set_id == FactorSet.id)
            .filter(
                FactorSet.project_id == project_id,
                FactorTable.status.in_(lifecycle.RUNNABLE_INPUT_STATUSES),
            )
            .all()
        )
        scenarios = (
            db.query(ScenarioTable)
            .join(ScenarioSet, ScenarioTable.set_id == ScenarioSet.id)
            .filter(
                ScenarioSet.project_id == project_id,
                ScenarioTable.status.in_(lifecycle.RUNNABLE_INPUT_STATUSES),
            )
            .order_by(ScenarioTable.created_at.asc())
            .all()
        )
        if not scenarios:
            raise ServiceError(
                400, "SCENARIO_NOT_FOUND",
                f"No runnable ScenarioTable found in project '{project_id}'.",
            )
        from app.db.models.modeling import ModelPublishedOutput
        pub_outputs = [
            r.variable_name
            for r in db.query(ModelPublishedOutput)
            .filter(ModelPublishedOutput.model_version_id == mv.id)
            .order_by(ModelPublishedOutput.sort_order)
            .all()
        ]
        ps = ProjectionSet(
            id=f"ps-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            name=f"Pipeline Projection Set {model.product_code} ({inforce_file.filename})",
            version_label=f"v-{uuid.uuid4().hex[:6]}",
            model_version_id=mv.id,
            inforce_file_ids=[inforce_file.id],
            assumption_table_ids=[t.id for t in assumption_tables],
            factor_table_ids=[t.id for t in factor_tables],
            scenario_ids=[s.id for s in scenarios],
            valuation_date=date(2026, 12, 31),
            horizon_months=projection_months,
            time_step="monthly",
            output_variables=pub_outputs,
            trace_scope={"mode": "selected_policies", "policy_ids": [policies[0]["policy_id"]]},
            parameters={},
            status=lifecycle.DRAFT,
            created_by=actor.id if actor else None,
        )
        db.add(ps)
        db.commit()
        val_res = projection_set_service.validate(db, ps.id, actor)
        if val_res.get("status") != "validated":
            raise ServiceError(
                422, "PROJECTION_SET_INVALID",
                f"ProjectionSet validation failed: {val_res.get('validation')}",
            )
        db.refresh(ps)

    effective_scenario_id = scenario_id or (ps.scenario_ids[0] if ps.scenario_ids else None)
    if not effective_scenario_id:
        raise ServiceError(400, "SCENARIO_REQUIRED", "ProjectionSet has no scenarios configured.")

    effective_run_id = run_id or f"run-proj-{uuid.uuid4().hex[:12]}"
    # If a run with effective_run_id already exists from a previous distinct execution, ensure unique ID
    existing_run = db.get(Run, effective_run_id)
    if existing_run is not None:
        effective_run_id = f"{effective_run_id}-{uuid.uuid4().hex[:6]}"

    run_set_id = f"rs-{uuid.uuid4().hex[:12]}"
    frozen_pkg = run_package_service.freeze(
        db=db,
        project=project,
        projection_set=ps,
        scenario_id=effective_scenario_id,
        user=actor,
        run_id=effective_run_id,
        run_set_id=run_set_id,
    )

    submitted_at = now_utc()
    run_set = RunSet(
        id=run_set_id,
        project_id=project_id,
        name=f"Pipeline Run Set {effective_run_id}",
        projection_set_ids=[ps.id],
        scenario_ids=[effective_scenario_id],
        resolution={
            "requested_scenario_ids": [effective_scenario_id],
            "projection_sets": [
                {
                    "projection_set_id": ps.id,
                    "scenario_source": "pipeline",
                    "scenario_ids": [effective_scenario_id],
                    "runs": [{"run_id": effective_run_id, "scenario_id": effective_scenario_id}],
                }
            ],
        },
        status=run_state.RUN_SET_QUEUED,
        created_by=actor.id if actor else None,
        submitted_at=submitted_at,
    )
    db.add(run_set)
    db.flush()

    conf = frozen_pkg.configuration
    run_obj = Run(
        id=effective_run_id,
        project_id=project_id,
        name=f"{ps.name} · {conf['scenario']['name']}",
        projection_key=f"{ps.name}@{ps.version_label}",
        status=run_state.PENDING,
        run_type="projection",
        run_set_id=run_set.id,
        projection_set_id=ps.id,
        model_version_id=mv.id,
        scenario_id=effective_scenario_id,
        valuation_date=date.fromisoformat(conf["valuation_date"]),
        horizon_months=conf["horizon_months"],
        illustrative=bool(conf["model_version"].get("illustrative", False)),
        triggered_by=actor.id if actor else None,
        run_package_fingerprint=frozen_pkg.fingerprint,
        attempt_count=0,
    )
    db.add(run_obj)
    db.flush()

    pkg_row = RunPackage(
        id=frozen_pkg.document["identity"]["package_id"],
        run_id=run_obj.id,
        project_id=project_id,
        schema_version=frozen_pkg.document["schema_version"],
        fingerprint_algorithm=frozen_pkg.document["fingerprint_algorithm"],
        fingerprint=frozen_pkg.fingerprint,
        package=frozen_pkg.document,
        created_by=actor.id if actor else None,
    )
    db.add(pkg_row)
    db.commit()
    db.expunge_all()

    # Execute via the shared projection engine
    final_status = run_execution_service.execute_run(db, effective_run_id)
    run_obj = db.get(Run, effective_run_id)
    if run_obj is None or (final_status not in ("completed", "success") and run_obj.status not in ("completed", "success")):
        status_str = getattr(run_obj, "status", "missing")
        raise ServiceError(
            500, "PROJECTION_EXECUTION_FAILED",
            f"Shared projection engine failed for run '{effective_run_id}' (status: {status_str}, final_status: {final_status}).",
        )

    art_obj = (
        db.query(RunArtifact)
        .filter(
            RunArtifact.run_id == run_obj.id,
            RunArtifact.artifact_type == "outputs",
            RunArtifact.attempt_number == (run_obj.accepted_attempt_number or 1),
        )
        .first()
    )
    if art_obj is None:
        raise ServiceError(
            500, "PROJECTION_ARTIFACT_MISSING",
            f"Projection run '{run_obj.id}' completed without persisting an 'outputs' RunArtifact.",
        )

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": run_obj.id,
        "artifact_id": art_obj.id,
        "run_package_id": pkg_row.id,
        "run_package_fingerprint": frozen_pkg.fingerprint,
        "model_id": model.id,
        "model_version_id": mv.id,
        "product_code": model.product_code,
        "scenario_id": effective_scenario_id,
        "projection_set_id": ps.id,
        "inforce_file_id": inforce_file.id,
        "inforce_fingerprint": inforce_file.fingerprint,
        "time_steps": run_obj.horizon_months,
        "row_count": art_obj.row_count,
        "artifact_fingerprint": art_obj.checksum_sha256,
        "storage_uri": art_obj.storage_uri,
        "project_id": project_id,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def execute_cash_flow_aggregation(
    db: Session,
    projection_run_id: str,
    storage_uri: str | None = None,
    artifact_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate flows and stocks using Polars strictly from verified persisted Parquet RunArtifact (P0-04)."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed aggregation execution requires an active database session.")

    proj_run = db.get(Run, projection_run_id)
    if not proj_run or (project_id and proj_run.project_id != project_id):
        raise ServiceError(404, "RUN_NOT_FOUND", f"Projection run '{projection_run_id}' not found.")

    if proj_run.status not in ("completed", "success"):
        raise ServiceError(
            400, "INVALID_SOURCE_RUN_STATUS",
            f"Projection run '{projection_run_id}' must be in completed/success status (got '{proj_run.status}').",
        )

    expected_attempt = proj_run.accepted_attempt_number or proj_run.attempt_count or 1

    # Resolve persisted RunArtifact from PostgreSQL
    if artifact_id:
        art = db.get(RunArtifact, artifact_id)
        if art is None:
            raise ServiceError(404, "ARTIFACT_NOT_FOUND", f"RunArtifact '{artifact_id}' not found.")
        if art.run_id != proj_run.id:
            raise ServiceError(
                400, "ARTIFACT_RUN_MISMATCH",
                f"RunArtifact '{artifact_id}' belongs to run '{art.run_id}', not '{proj_run.id}'.",
            )
    else:
        art = (
            db.query(RunArtifact)
            .filter(
                RunArtifact.run_id == proj_run.id,
                RunArtifact.attempt_number == expected_attempt,
                RunArtifact.artifact_type.in_(("outputs", "cash_flows")),
            )
            .first()
        )
        if art is None:
            art = (
                db.query(RunArtifact)
                .filter(
                    RunArtifact.run_id == proj_run.id,
                    RunArtifact.artifact_type.in_(("outputs", "cash_flows")),
                )
                .first()
            )

    if art is None:
        raise ServiceError(
            404, "ARTIFACT_NOT_FOUND",
            f"No persisted cash-flow/outputs RunArtifact found in database for run '{projection_run_id}'.",
        )

    if storage_uri and storage_uri != art.storage_uri:
        raise ServiceError(
            400, "ARTIFACT_URI_MISMATCH",
            f"Supplied storage_uri '{storage_uri}' does not match persisted RunArtifact URI '{art.storage_uri}'.",
        )

    store = get_artifact_store()
    try:
        path = store.path_for_uri(art.storage_uri)
    except Exception as exc:
        raise ServiceError(400, "PHYSICAL_ARTIFACT_MISSING", f"Invalid artifact URI '{art.storage_uri}': {exc}") from exc

    if not path.exists():
        raise ServiceError(
            400, "PHYSICAL_ARTIFACT_MISSING",
            f"Physical Parquet file does not exist on disk at '{art.storage_uri}'.",
        )

    raw_bytes = path.read_bytes()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha != art.checksum_sha256:
        raise ServiceError(
            400, "ARTIFACT_CHECKSUM_MISMATCH",
            f"Physical Parquet SHA-256 ({actual_sha}) does not match RunArtifact checksum ({art.checksum_sha256}).",
        )

    try:
        df = pl.read_parquet(path)
    except Exception as exc:
        raise ServiceError(422, "INVALID_ARTIFACT_SCHEMA", f"Corrupt Parquet file at '{art.storage_uri}': {exc}") from exc

    if df.height == 0 or (art.row_count and df.height != art.row_count):
        raise ServiceError(
            422, "ARTIFACT_ROW_COUNT_MISMATCH",
            f"Parquet row count ({df.height}) disagrees with RunArtifact metadata ({art.row_count}).",
        )

    cols = set(df.columns)
    narrow_required = {"policy_id", "projection_month", "variable_name", "value_json"}
    wide_required = {"policy_id", "projection_month", "cash_flow", "ending_av"}

    if narrow_required.issubset(cols):
        # Canonical projection engine output schema
        records = df.to_dicts()
        by_month_var: dict[tuple[int, str], float] = {}
        months_seen: set[int] = set()
        variables_seen: set[str] = set()
        for r in records:
            m = int(r["projection_month"])
            vname = str(r["variable_name"])
            raw_val = json.loads(r["value_json"])
            val = float(raw_val["value"] if isinstance(raw_val, dict) and "value" in raw_val else raw_val)
            if m >= 1:
                months_seen.add(m)
            variables_seen.add(vname)
            by_month_var[(m, vname)] = by_month_var.get((m, vname), 0.0) + val

        sorted_months = sorted(months_seen)
        if not sorted_months:
            raise ServiceError(422, "INVALID_ARTIFACT_SCHEMA", "Parquet outputs contain no projection months >= 1.")

        max_m = sorted_months[-1]
        if "liability_outgo" in variables_seen or "account_value_close" in variables_seen:
            # MYGA / accumulation product outputs
            flow_var = "liability_outgo" if "liability_outgo" in variables_seen else "net_cash_flow"
            monthly_cfs = []
            for m in sorted_months:
                cf_m = float(by_month_var.get((m, flow_var), 0.0))
                if m == max_m and "account_value_close" in variables_seen:
                    cf_m += float(by_month_var.get((m, "account_value_close"), 0.0))
                monthly_cfs.append(cf_m)
            opening_stock = float(by_month_var.get((1, "account_value_open"), 0.0))
            ending_stock = float(by_month_var.get((max_m, "account_value_close"), 0.0))
        elif "expected_payment" in variables_seen:
            # SPIA / payout product outputs
            monthly_cfs = [float(by_month_var.get((m, "expected_payment"), 0.0)) for m in sorted_months]
            opening_stock = float(by_month_var.get((0, "reserve"), by_month_var.get((1, "reserve"), 0.0)))
            ending_stock = float(by_month_var.get((max_m, "reserve"), 0.0))
        else:
            raise ServiceError(
                422, "INVALID_ARTIFACT_SCHEMA",
                f"Parquet outputs missing required cash-flow/stock variables (found {sorted(variables_seen)}).",
            )
    elif wide_required.issubset(cols):
        monthly_agg = (
            df.group_by("projection_month")
            .agg(
                pl.col("cash_flow").sum().alias("monthly_cash_flow"),
                pl.col("ending_av").sum().alias("monthly_ending_av"),
            )
            .sort("projection_month")
        )
        monthly_cfs = [float(x) for x in monthly_agg["monthly_cash_flow"].to_list()]
        monthly_avs = [float(x) for x in monthly_agg["monthly_ending_av"].to_list()]
        opening_stock = float(df.filter(pl.col("projection_month") == 1)["beginning_av"].sum()) if "beginning_av" in cols else (monthly_avs[0] if monthly_avs else 0.0)
        ending_stock = float(monthly_avs[-1]) if monthly_avs else 0.0
    else:
        raise ServiceError(
            422, "INVALID_ARTIFACT_SCHEMA",
            f"Parquet artifact schema does not match governed output contract: columns={sorted(cols)}",
        )

    total_flow_sum = float(sum(monthly_cfs))

    # Derive cohort_id from the persisted projection RunPackage
    cohort_id = f"COHORT_{proj_run.id[:12]}"
    proj_pkg = db.query(RunPackage).filter(RunPackage.run_id == proj_run.id).first()
    if proj_pkg and isinstance(proj_pkg.package, dict):
        cfg = proj_pkg.package.get("configuration", {})
        mv_cfg = cfg.get("model_version", {})
        mod_cfg = cfg.get("model", {})
        cohort_id = (
            mv_cfg.get("block_name")
            or mod_cfg.get("product_code")
            or proj_run.projection_key
            or cohort_id
        )

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "upstream_run_id": projection_run_id,
        "artifact_id": art.id,
        "verified_artifact_id": art.id,
        "artifact_fingerprint": actual_sha,
        "project_id": proj_run.project_id,
        "total_cash_flow_sum": total_flow_sum,
        "opening_account_value_stock": opening_stock,
        "ending_account_value_stock": ending_stock,
        "cohort_id": str(cohort_id),
        "aggregated_cash_flows": monthly_cfs,
        "time_steps_aggregated": len(monthly_cfs),
        "started_at": started_at,
        "completed_at": completed_at,
    }


def _build_methodology_movements(
    methodology: str,
    interest_unwind: Decimal,
    cash_flow_release: Decimal,
) -> dict[str, Decimal]:
    """Map valuation reserve movements onto the governed methodology template's movement components."""
    template = get_methodology_template(methodology)
    movement_specs = [c for c in template.components if c.is_movement]
    movements: dict[str, Decimal] = {c.name: Decimal("0.0000") for c in movement_specs}

    if methodology == "VM22":
        movements["contractual_cash_flows"] = cash_flow_release
        movements["economic_revaluation"] = interest_unwind
    elif methodology == "LDTI":
        movements["expected_change"] = interest_unwind
        movements["experience_variance"] = cash_flow_release
    elif methodology == "VM21":
        movements["inforce_cash_flows"] = cash_flow_release
        movements["market_movement_impact"] = interest_unwind
    elif methodology == "DAC":
        movements["amortization_expense"] = cash_flow_release
        movements["terminations_adjustments"] = interest_unwind
    elif methodology == "MRB":
        movements["benefit_payments"] = cash_flow_release
        movements["change_in_fair_value_ni"] = interest_unwind
    elif methodology == "ASC815":
        movements["host_debt_carrying_value"] = cash_flow_release
        movements["embedded_derivative_fair_value"] = interest_unwind
    elif len(movement_specs) >= 2:
        movements[movement_specs[0].name] = cash_flow_release
        movements[movement_specs[1].name] = interest_unwind
    elif len(movement_specs) == 1:
        movements[movement_specs[0].name] = cash_flow_release + interest_unwind

    return movements


def execute_foundation_valuation(
    db: Session,
    upstream_run_id: str,
    basis: str,
    methodology: str,
    valuation_date: str,
    annual_discount_rate: float,
    aggregated_cash_flows: list[float],
    project_id: str,
    cohort_id: str | None = None,
) -> dict[str, Any]:
    """Calculate foundation present value reserve and persist canonical Valuation Run & RunPackage."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed valuation execution requires an active database session.")

    clean_basis = validate_basis(basis)
    clean_method = validate_methodology(methodology)
    validate_basis_methodology_pair(clean_basis, clean_method)

    if clean_basis == "TAX":
        raise ServiceError(400, "UNSUPPORTED_VALUATION_BASIS", "TAX basis is explicitly not supported at launch.")

    upstream_run = db.get(Run, upstream_run_id)
    if upstream_run is None or upstream_run.project_id != project_id:
        raise ServiceError(404, "RUN_NOT_FOUND", f"Upstream projection run '{upstream_run_id}' not found in project '{project_id}'.")

    if not aggregated_cash_flows:
        raise ServiceError(422, "EMPTY_CASH_FLOWS", "Cannot execute valuation on empty aggregated cash flows.")

    # Compute opening reserve V_0 (at t=0 across m=1..T) and closing reserve V_1 (at t=1 across remaining m=2..T)
    # By exact discounting identity: V_1 = V_0 * (1 + r_m) - CF_1  =>  V_0 + (V_0 * r_m) + (-CF_1) = V_1
    n_months = len(aggregated_cash_flows)
    factors_t0 = [
        annual_to_monthly_discount_factor(annual_discount_rate, m)
        for m in range(1, n_months + 1)
    ]
    v0_float = present_value(aggregated_cash_flows, factors_t0)

    r_monthly = (1.0 + float(annual_discount_rate)) ** (1.0 / 12.0) - 1.0
    cf_1 = float(aggregated_cash_flows[0])
    if n_months > 1:
        factors_t1 = [
            annual_to_monthly_discount_factor(annual_discount_rate, m)
            for m in range(1, n_months)
        ]
        v1_float = present_value(aggregated_cash_flows[1:], factors_t1)
    else:
        v1_float = 0.0

    opening_dec = Decimal(str(round(v0_float, 4)))
    closing_dec = Decimal(str(round(v1_float, 4)))
    cf_release_dec = Decimal(str(round(-cf_1, 4)))
    # Interest accretion / discount unwind over the period
    interest_unwind_dec = closing_dec - opening_dec - cf_release_dec

    movements_dec = _build_methodology_movements(clean_method, interest_unwind_dec, cf_release_dec)
    pv_res = float(closing_dec)

    # Resolve cohort_id from upstream RunPackage if not passed
    resolved_cohort = cohort_id
    if not resolved_cohort:
        src_pkg = db.query(RunPackage).filter(RunPackage.run_id == upstream_run.id).first()
        if src_pkg and isinstance(src_pkg.package, dict):
            cfg = src_pkg.package.get("configuration", {})
            resolved_cohort = (
                cfg.get("model_version", {}).get("block_name")
                or cfg.get("model", {}).get("product_code")
            )
        resolved_cohort = resolved_cohort or upstream_run.projection_key or f"COHORT_{upstream_run.id[:8]}"

    # 1. Create canonical valuation Run
    val_run_id = f"run-val-{uuid.uuid4().hex[:12]}"
    val_date_obj = date.fromisoformat(str(valuation_date)) if isinstance(valuation_date, str) else valuation_date
    val_run = Run(
        id=val_run_id,
        project_id=project_id,
        name=f"Valuation {clean_basis} {clean_method} {val_run_id}",
        projection_key=str(resolved_cohort),
        status="success",
        run_type="valuation",
        basis=clean_basis,
        methodology=clean_method,
        methodology_version="v1.0",
        source_projection_run_id=upstream_run_id,
        valuation_date=val_date_obj,
        horizon_months=upstream_run.horizon_months or n_months,
    )
    db.add(val_run)
    db.flush()

    # 2. Freeze and persist RunPackage using trusted PostgreSQL source Run lineage
    pkg = freeze_valuation_run_package(
        db=db,
        project=project_id,
        run=val_run,
        user=None,
        basis=clean_basis,
        methodology=clean_method,
        methodology_version="v1.0",
        source_projection_run_id=upstream_run_id,
        valuation_date=str(val_date_obj),
        valuation_parameters={"annual_discount_rate": annual_discount_rate},
    )
    val_run.run_package_fingerprint = pkg.fingerprint
    val_run.model_version_id = pkg.configuration["model_version"]["id"]

    val_pkg = RunPackage(
        id=pkg.document["identity"]["package_id"],
        run_id=val_run.id,
        project_id=project_id,
        schema_version=pkg.document["schema_version"],
        fingerprint_algorithm=pkg.document["fingerprint_algorithm"],
        fingerprint=pkg.fingerprint,
        package=pkg.document,
    )
    db.add(val_pkg)

    summary_results = {
        "present_value_reserve": pv_res,
        "opening_reserve": float(opening_dec),
        "closing_reserve": float(closing_dec),
        "attributed_movements": {k: float(v) for k, v in movements_dec.items()},
        "cohort_id": str(resolved_cohort),
        "basis": clean_basis,
        "methodology": clean_method,
        "annual_discount_rate": annual_discount_rate,
    }

    # 3. Persist ValuationRunDetail
    val_detail = ValuationRunDetail(
        run_id=val_run.id,
        valuation_date=val_date_obj,
        configuration=pkg.configuration.get("valuation", {}),
        summary_results=summary_results,
    )
    db.add(val_detail)
    db.commit()

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "upstream_run_id": upstream_run_id,
        "valuation_run_id": val_run.id,
        "project_id": project_id,
        "basis": clean_basis,
        "methodology": clean_method,
        "cohort_id": str(resolved_cohort),
        "valuation_date": str(val_date_obj),
        "present_value_reserve": pv_res,
        "opening_reserve": float(opening_dec),
        "closing_reserve": float(closing_dec),
        "attributed_movements": {k: float(v) for k, v in movements_dec.items()},
        "discount_rate": annual_discount_rate,
        "run_package_id": val_pkg.id,
        "run_package_fingerprint": pkg.fingerprint,
        "compliance_status": "FOUNDATION_GENERIC_PV",
        "started_at": started_at,
        "completed_at": completed_at,
    }


def execute_rollforward_attribution(
    db: Session,
    valuation_run_id: str,
    project_id: str,
    opening: Decimal | float | None = None,
    movements: dict[str, Decimal | float] | None = None,
    ending: Decimal | float | None = None,
    basis: str | None = None,
    methodology: str | None = None,
    cohort_id: str | None = None,
    tolerance: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """Execute valuation-linked attribution framework and persist component-by-component RollforwardSummary rows (P0-05)."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed rollforward execution requires an active database session.")

    val_run = db.get(Run, valuation_run_id)
    if val_run is None or val_run.project_id != project_id:
        raise ServiceError(404, "RUN_NOT_FOUND", f"Valuation run '{valuation_run_id}' not found in project '{project_id}'.")

    val_detail = db.get(ValuationRunDetail, valuation_run_id)
    persisted_summary = (val_detail.summary_results if val_detail and val_detail.summary_results else {}) or {}

    effective_basis = basis or val_run.basis or persisted_summary.get("basis")
    effective_method = methodology or val_run.methodology or persisted_summary.get("methodology")
    if not effective_basis or not effective_method:
        raise ServiceError(
            400, "VALUATION_METADATA_MISSING",
            f"Parent valuation run '{valuation_run_id}' is missing basis or methodology.",
        )

    try:
        clean_b = validate_basis(effective_basis)
        clean_m = validate_methodology(effective_method)
    except ValuationTaxonomyError as exc:
        raise ServiceError(400, "INVALID_VALUATION_TAXONOMY", str(exc)) from exc

    if val_run.basis and val_run.basis != clean_b:
        raise ServiceError(
            400, "ROLLFORWARD_BASIS_MISMATCH",
            f"Rollforward basis '{clean_b}' contradicts parent valuation run basis '{val_run.basis}'.",
        )
    if val_run.methodology and val_run.methodology != clean_m:
        raise ServiceError(
            400, "ROLLFORWARD_METHODOLOGY_MISMATCH",
            f"Rollforward methodology '{clean_m}' contradicts parent valuation run methodology '{val_run.methodology}'.",
        )

    try:
        validate_basis_methodology_pair(clean_b, clean_m)
    except ValuationTaxonomyError as exc:
        raise ServiceError(400, "INVALID_VALUATION_TAXONOMY", str(exc)) from exc

    effective_cohort = (
        cohort_id
        or persisted_summary.get("cohort_id")
        or val_run.projection_key
        or f"COHORT_{val_run.id[:8]}"
    )

    resolved_opening = opening if opening is not None else persisted_summary.get("opening_reserve")
    resolved_movements = movements if movements is not None else persisted_summary.get("attributed_movements")
    resolved_ending = (
        ending
        if ending is not None
        else persisted_summary.get("closing_reserve", persisted_summary.get("present_value_reserve"))
    )

    if resolved_opening is None or resolved_movements is None or resolved_ending is None:
        raise ServiceError(
            400, "ROLLFORWARD_INPUTS_MISSING",
            f"Valuation run '{valuation_run_id}' does not have complete opening, movements, and closing balances.",
        )

    opening_dec = Decimal(str(resolved_opening))
    movements_dec = {k: Decimal(str(v)) for k, v in resolved_movements.items()}
    ending_dec = Decimal(str(resolved_ending))
    tol_dec = Decimal(str(tolerance))

    # Verify against persisted valuation closing reserve if present
    persisted_closing = persisted_summary.get("closing_reserve", persisted_summary.get("present_value_reserve"))
    if persisted_closing is not None:
        persisted_closing_dec = Decimal(str(persisted_closing))
        if abs(ending_dec - persisted_closing_dec) > tol_dec:
            raise ServiceError(
                422, "ROLLFORWARD_DISCONTINUITY",
                f"Rollforward closing balance ({ending_dec}) disagrees with persisted valuation reserve ({persisted_closing_dec}) beyond tolerance ({tol_dec}).",
            )

    continuity = validate_continuity(opening_dec, movements_dec, ending_dec, tolerance=tol_dec)
    if not continuity.is_continuous:
        raise ServiceError(
            422,
            "ROLLFORWARD_DISCONTINUITY",
            f"Actuarial rollforward assertion failure: {continuity.error_message}",
        )

    records = record_rollforward_summary(
        db=db,
        run_id=valuation_run_id,
        project_id=project_id,
        cohort_id=str(effective_cohort),
        methodology=clean_m,
        basis=clean_b,
        continuity_result=continuity,
    )

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "summary_id": records[0].id if records else "",
        "component_ids": [r.id for r in records],
        "component_count": len(records),
        "valuation_run_id": valuation_run_id,
        "project_id": project_id,
        "cohort_id": str(effective_cohort),
        "basis": clean_b,
        "methodology": clean_m,
        "is_continuous": continuity.is_continuous,
        "opening_balance": float(opening_dec),
        "sum_of_movements": float(continuity.sum_of_movements),
        "ending_balance": float(ending_dec),
        "difference": float(continuity.difference),
        "movements": {k: float(v) for k, v in movements_dec.items()},
        "started_at": started_at,
        "completed_at": completed_at,
    }


def execute_governed_reconciliation(
    db: Session,
    valuation_run_id: str,
    project_id: str,
    actual_reserve: float,
    expected_result_set_id: str | None = None,
    expected_benchmark: float | None = None,
    tolerance: float = 0.01,
    rel_tol: float = 1e-4,
    target_metric: str = "present_value_reserve",
) -> dict[str, Any]:
    """Execute three-state reconciliation strictly against an approved ExpectedResultSet (P0-06)."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed reconciliation execution requires an active database session.")

    val_run = db.get(Run, valuation_run_id)
    if val_run is None or val_run.project_id != project_id:
        raise ServiceError(404, "RUN_NOT_FOUND", f"Valuation run '{valuation_run_id}' not found in project '{project_id}'.")

    # P0-06: Ungoverned caller benchmark float cannot be used to force PASS
    if expected_benchmark is not None and not expected_result_set_id:
        raise ServiceError(
            400, "UNGOVERNED_BENCHMARK_FORBIDDEN",
            "Raw caller-supplied 'expected_benchmark' is forbidden for governed reconciliation; supply an approved 'expected_result_set_id'.",
        )

    benchmark_val: float | None = None
    ers_fp = ""
    ers_version = "v1"
    reason: str | None = None
    failures: list[dict[str, Any]] = []

    if not expected_result_set_id:
        status = "NOT_VERIFIABLE"
        diff = 0.0
        reason = "No approved ExpectedResultSet supplied for governed reconciliation."
    else:
        ers = db.get(ExpectedResultSet, expected_result_set_id)
        if not ers or ers.project_id != project_id:
            raise ServiceError(
                404, "BENCHMARK_NOT_FOUND",
                f"ExpectedResultSet '{expected_result_set_id}' not found in project '{project_id}'.",
            )
        if ers.status != "approved" or not ers.approved_by or ers.superseded_by_id is not None:
            raise ServiceError(
                400, "BENCHMARK_NOT_APPROVED",
                f"ExpectedResultSet '{expected_result_set_id}' must be in active 'approved' status with independent approver (status: {ers.status}).",
            )

        computed_ers_fp = compute_fp(ers.rows_data)
        if ers.fingerprint != computed_ers_fp:
            raise ServiceError(
                400, "BENCHMARK_FINGERPRINT_MISMATCH",
                f"ExpectedResultSet '{expected_result_set_id}' rows do not match recorded fingerprint.",
            )

        ers_fp = ers.fingerprint
        ers_version = ers.version_label

        if ers.rows_data and len(ers.rows_data) > 0:
            # Extract scoped benchmark metric from approved rows
            row0 = ers.rows_data[-1]
            for candidate_key in (target_metric, "present_value_reserve", "reserve", "expected_value", "account_value_close"):
                if candidate_key in row0 and row0[candidate_key] is not None:
                    benchmark_val = float(row0[candidate_key])
                    break

        if benchmark_val is None:
            status = "NOT_VERIFIABLE"
            diff = 0.0
            reason = f"Approved ExpectedResultSet '{ers.id}' does not contain target metric '{target_metric}'."
        else:
            diff = abs(float(actual_reserve) - benchmark_val)
            allowed = float(tolerance) + float(rel_tol) * abs(benchmark_val)
            if diff <= allowed:
                status = "PASS"
            else:
                status = "FAIL"
                failures.append({
                    "metric": target_metric,
                    "actual": float(actual_reserve),
                    "expected": benchmark_val,
                    "difference": diff,
                    "allowed_tolerance": allowed,
                })

    rec_report = ReconciliationReport(
        id=f"rec-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        run_id=valuation_run_id,
        expected_result_set_id=expected_result_set_id,
        variables_requested=[target_metric],
        expected_result_fingerprint=ers_fp,
        expected_result_version=ers_version,
        status=status,
        abs_tol=tolerance,
        rel_tol=rel_tol,
        disposition_status="auto_evaluated",
        summary={
            "actual": float(actual_reserve),
            "expected": benchmark_val,
            "difference": diff,
            "reason": reason,
        },
        failures=failures,
    )
    db.add(rec_report)
    db.commit()

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "report_id": rec_report.id,
        "valuation_run_id": valuation_run_id,
        "project_id": project_id,
        "expected_result_set_id": expected_result_set_id,
        "expected_result_fingerprint": ers_fp,
        "status": status,
        "actual_value": float(actual_reserve),
        "expected_benchmark": benchmark_val,
        "difference": diff,
        "tolerance": tolerance,
        "reason": reason,
        "not_verifiable_reason": reason,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def execute_reporting_manifest(
    db: Session,
    valuation_run_id: str,
    project_id: str,
    valuation_result: dict[str, Any],
    rollforward_result: dict[str, Any],
    reconciliation_result: dict[str, Any],
) -> dict[str, Any]:
    """Assemble final disclosure package, persist export artifact to disk and RunArtifact to DB."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed reporting export requires an active database session.")

    val_run = db.get(Run, valuation_run_id)
    if val_run is None or val_run.project_id != project_id:
        raise ServiceError(404, "RUN_NOT_FOUND", f"Valuation run '{valuation_run_id}' not found in project '{project_id}'.")

    summary_package = {
        "valuation_run_id": valuation_run_id,
        "upstream_projection_run_id": valuation_result.get("upstream_run_id"),
        "project_id": project_id,
        "basis": valuation_result["basis"],
        "methodology": valuation_result["methodology"],
        "cohort_id": valuation_result.get("cohort_id"),
        "valuation_date": valuation_result["valuation_date"],
        "run_package_id": valuation_result.get("run_package_id"),
        "run_package_fingerprint": valuation_result.get("run_package_fingerprint"),
        "reserve": valuation_result["present_value_reserve"],
        "rollforward": {
            "summary_id": rollforward_result.get("summary_id"),
            "is_continuous": rollforward_result["is_continuous"],
            "opening": rollforward_result["opening_balance"],
            "movements": rollforward_result.get("movements", {}),
            "ending": rollforward_result["ending_balance"],
            "difference": rollforward_result["difference"],
        },
        "reconciliation": {
            "report_id": reconciliation_result.get("report_id"),
            "expected_result_set_id": reconciliation_result.get("expected_result_set_id"),
            "status": reconciliation_result["status"],
            "difference": reconciliation_result["difference"],
            "reason": reconciliation_result.get("reason"),
        },
    }

    manifest_bytes = json.dumps(summary_package, sort_keys=True).encode("utf-8")
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    # 1. Persist physical reporting artifact to disk
    store = get_artifact_store()
    stored = store.write_bytes(
        namespace=valuation_run_id,
        artifact_type="reporting",
        filename="disclosure_package.json",
        content=manifest_bytes,
    )

    # 2. Persist RunArtifact in database
    rep_art = RunArtifact(
        id=f"art-rep-{uuid.uuid4().hex[:12]}",
        run_id=valuation_run_id,
        attempt_number=1,
        artifact_type="reporting",
        storage_uri=stored.uri,
        storage_backend="local",
        file_format="json",
        schema_version="m2-v1",
        row_count=1,
        checksum_sha256=actual_manifest_sha256,
    )
    db.add(rep_art)
    db.commit()

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "package_id": f"DISCLOSURE-{valuation_run_id}",
        "artifact_id": rep_art.id,
        "valuation_run_id": valuation_run_id,
        "rollforward_verified": rollforward_result["is_continuous"],
        "reconciliation_status": reconciliation_result["status"],
        "manifest_sha256": actual_manifest_sha256,
        "storage_uri": stored.uri,
        "summary": summary_package,
        "started_at": started_at,
        "completed_at": completed_at,
    }
