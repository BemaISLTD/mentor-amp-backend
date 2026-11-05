"""Actuarial Valuation Pipeline Domain Service (M5 Directives & V6 Senior Review).

Orchestration Invariant:
    All actuarial formulas, data persistence, and calculations live in domain services
    and the shared calculation engine, never inside Dagster asset bodies.
    Dagster assets act strictly as orchestrators: validating inputs, invoking this service,
    emitting lineage metadata, and routing failures/retries.

Sequence:
    1. Governed Inforce Import / Ingestion (explicit InforceFile & InforceRecord; no latest-file fallback)
    2. Governed Inforce Validation (via shared inforce validator + integrity checks)
    3. Governed Projection Execution (explicit ModelVersion/ProjectionSet, Scenario, ValuationDate;
       idempotent recovery on same run_id across partial persistence / execution / finalization faults)
    4. Verified Columnar Aggregation (Polars strictly from accepted-attempt canonical RunArtifact Parquet)
    5. Foundation Valuation (Time-value-of-money generic PV discounting; gated VM21/VM22)
    6. Valuation-Linked Rollforward Attribution (Illustrative generic PV separated from regulatory verticals;
       supports explicit prior/current valuation run period comparison; rejects balancing plugs)
    7. Governed Reconciliation (Persisted ValuationRunDetail actuals, four-eyes approved ExpectedResultSet,
       scoped key matching, governed tolerances: PASS, FAIL, NOT_VERIFIABLE)
    8. Reporting & Audit Manifest Export (Technical materialization vs governance acceptance)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
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
    record_rollforward_summary,
    validate_continuity,
)
from app.core.valuation.taxonomy import (
    ValuationTaxonomyError,
    validate_basis,
    validate_basis_methodology_pair,
    validate_methodology,
)
from app.data.validation.inforce_validator import validate_inforce
from app.db.models.assumption import AssumptionSet, AssumptionTable
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.inforce import InforceFile, InforceRecord
from app.db.models.modeling import Model, ModelPublishedOutput, ModelVersion
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet, RunSet
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.rollforward import RollforwardSummary
from app.db.models.run import Run
from app.db.models.run_artifact import RunArtifact
from app.db.models.run_package import RunAttempt, RunPackage
from app.db.models.scenario import ScenarioSet, ScenarioTable
from app.db.models.user import User
from app.db.models.valuation_detail import ValuationRunDetail
from app.services import (
    projection_set_service,
    run_execution_service,
    run_finalization,
    run_package_service,
)
from app.services.common import ServiceError, iso, now_utc
from app.services.run_package_service import freeze_valuation_run_package


FORBIDDEN_DEFAULT_PROJECT_IDS = frozenset({"proj-m5-valuation-default"})
FORBIDDEN_DEFAULT_RUN_IDS = frozenset({"run-m5-dagster-projection-001"})
CANONICAL_OUTPUT_ARTIFACT_TYPES = ("outputs", "cash_flows")
FORBIDDEN_RESIDUAL_MOVEMENT_NAMES = frozenset({
    "unexplained_residual",
    "unexplained_movement",
    "balancing_residual",
    "balancing_plug",
    "plug",
    "residual",
})
GOVERNED_DEFAULT_ABS_TOL = 0.01

# Governed Approved Tolerance Policies (V8-P0-02)
GOVERNED_APPROVED_TOLERANCE_POLICIES: dict[str, dict[str, Any]] = {
    "tol-policy-m5-standard": {
        "policy_id": "tol-policy-m5-standard",
        "description": "Milestone 5 governed standard attribution tolerance (max 0.05)",
        "max_tolerance": Decimal("0.05"),
        "status": "approved",
    },
    "tol-policy-strict-zero": {
        "policy_id": "tol-policy-strict-zero",
        "description": "Milestone 5 zero-tolerance exact attribution (max 0.0001)",
        "max_tolerance": Decimal("0.0001"),
        "status": "approved",
    },
}

GOVERNED_DEFAULT_REL_TOL = 1e-4


def _after_initial_run_commit_hook(db: Session, run_id: str) -> None:
    """Seam invoked after initial Run/RunPackage commit and before engine execution (for partial-write retry tests)."""
    return None


def _normalize_product_family(code: str | None) -> str:
    if not code or not str(code).strip():
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
    return "UNKNOWN"


def _parse_governed_date(raw_date: str | date | None, field_name: str = "valuation_date") -> date:
    if raw_date is None or (isinstance(raw_date, str) and not raw_date.strip()):
        raise ServiceError(
            400,
            "VALUATION_DATE_REQUIRED",
            f"Governed execution requires an explicit '{field_name}' (YYYY-MM-DD); implicit date defaults are forbidden.",
        )
    if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
        return raw_date
    try:
        return date.fromisoformat(str(raw_date).strip())
    except (ValueError, TypeError) as exc:
        raise ServiceError(
            400,
            "INVALID_VALUATION_DATE",
            f"Invalid {field_name} '{raw_date}': expected ISO YYYY-MM-DD date.",
        ) from exc


def execute_inforce_import(
    db: Session,
    project_id: str,
    inforce_file_id: str | None = None,
    policy_block: str | None = None,
) -> dict[str, Any]:
    """Load and verify a governed InforceFile and its InforceRecords from PostgreSQL (V5-P0-B: no implicit file selection)."""
    started_at = datetime.now(timezone.utc).isoformat()

    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed inforce import requires an active database session.")

    if not project_id or not str(project_id).strip() or str(project_id).strip() in FORBIDDEN_DEFAULT_PROJECT_IDS:
        raise ServiceError(
            400,
            "PROJECT_ID_REQUIRED",
            "Governed inforce import requires an explicit project_id; default project identities are forbidden.",
        )

    project = db.get(Project, project_id)
    if not project:
        raise ServiceError(404, "PROJECT_NOT_FOUND", f"Project '{project_id}' not found.")

    if not inforce_file_id or not str(inforce_file_id).strip():
        raise ServiceError(
            400,
            "INFORCE_FILE_REQUIRED",
            "Governed inforce import requires an explicit 'inforce_file_id'; implicit latest-file selection is forbidden.",
        )

    inforce_file = db.get(InforceFile, inforce_file_id)
    if inforce_file is None or inforce_file.project_id != project_id:
        raise ServiceError(
            404, "INFORCE_NOT_FOUND",
            f"Inforce file '{inforce_file_id}' not found in project '{project_id}'.",
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

        fam = _normalize_product_family(p.get("product_type") or p.get("product_code"))
        if fam == "UNKNOWN":
            errors.append(f"Row {idx} ({pid}): Missing or unrecognized product_type/product_code")

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
    valuation_date: str | date | None = None,
) -> dict[str, Any]:
    """Execute governed projection via shared projection engine, ModelVersion, FormulaRegistry, and RunPackage.

    Enforces V5-P0-B & V5-P1-G:
    - Explicit project_id, run_id, product_code, model_version_id/projection_set_id, scenario_id, and valuation_date.
    - Idempotent retry/recovery on the same run_id without minting duplicate canonical Run rows.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed projection execution requires an active database session.")

    if not project_id or not str(project_id).strip() or str(project_id).strip() in FORBIDDEN_DEFAULT_PROJECT_IDS:
        raise ServiceError(
            400,
            "PROJECT_ID_REQUIRED",
            "Governed projection requires an explicit project_id; default project identities are forbidden.",
        )

    if not run_id or not str(run_id).strip() or str(run_id).strip() in FORBIDDEN_DEFAULT_RUN_IDS:
        raise ServiceError(
            400,
            "RUN_ID_REQUIRED",
            "Governed projection requires an explicit run_id; default run identities are forbidden.",
        )
    effective_run_id = str(run_id).strip()

    project = db.get(Project, project_id)
    if not project:
        raise ServiceError(404, "PROJECT_NOT_FOUND", f"Project '{project_id}' not found.")

    policies = (
        validated_inforce.get("validated_policies")
        or validated_inforce.get("policies")
        or validated_inforce.get("records")
        or []
    )
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

    # V5-P0-B: Strictly require explicit product code on every policy record (zero MYGA fallback)
    raw_families = [
        _normalize_product_family(p.get("product_type") or p.get("product_code"))
        for p in policies
    ]
    if any(fam == "UNKNOWN" for fam in raw_families):
        raise ServiceError(
            400,
            "PRODUCT_CODE_REQUIRED",
            "All inforce records must specify a recognized product_type/product_code; implicit product family fallback is forbidden.",
        )
    policy_families = set(raw_families)
    if len(policy_families) > 1:
        raise ServiceError(
            400, "HETEROGENEOUS_INFORCE_BLOCK",
            f"Inforce block contains mixed product families: {sorted(policy_families)}.",
        )
    inforce_family = next(iter(policy_families))

    # V5-P0-B: Require explicit projection_set_id or explicit model_version_id (no silent scan for first model)
    if not projection_set_id and not model_version_id:
        raise ServiceError(
            400,
            "MODEL_IDENTITY_REQUIRED",
            "Governed projection execution requires an explicit 'projection_set_id' or 'model_version_id'; implicit model selection is forbidden.",
        )

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
        if inforce_file.id not in (ps.inforce_file_ids or []):
            raise ServiceError(
                400, "PROJECTION_SET_INFORCE_MISMATCH",
                f"ProjectionSet '{projection_set_id}' is not bound to inforce_file_id '{inforce_file.id}'.",
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
        raise ServiceError(
            404, "MODEL_VERSION_NOT_FOUND",
            f"ModelVersion not found in project '{project_id}'.",
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
    if model.status not in ("active", "validated", "approved", "published"):
        raise ServiceError(
            400, "MODEL_NOT_APPROVED",
            f"Model '{model.id}' (status: '{model.status}') is not in a runnable governance state.",
        )

    # Require approved / published ModelVersion with independent approval evidence (V6-P1-05)
    if (
        mv.status not in ("approved", "published")
        or mv.approved_at is None
        or not mv.approved_by
        or not mv.created_by
    ):
        raise ServiceError(
            400, "MODEL_VERSION_NOT_APPROVED",
            f"ModelVersion '{mv.id}' (status: '{mv.status}') must be approved with recorded approver and creator for governed execution.",
        )
    if mv.approved_by == mv.created_by:
        raise ServiceError(
            400, "SELF_APPROVED_MODEL_FORBIDDEN",
            f"ModelVersion '{mv.id}' violates four-eyes governance: created_by ('{mv.created_by}') cannot match approved_by ('{mv.approved_by}').",
        )

    # Enforce strict product family match between inforce policies and Model.product_code (P0-02)
    model_family = _normalize_product_family(model.product_code)
    if inforce_family != model_family:
        raise ServiceError(
            400, "PRODUCT_MODEL_MISMATCH",
            f"Inforce product family '{inforce_family}' mismatches Model '{model.id}' product_code '{model.product_code}' ({model_family}).",
        )

    # Resolve scenario & valuation_date explicitly (V5-P0-B: no silent first-scenario or hardcoded 2026-12-31)
    effective_scenario_id: str | None = None
    if scenario_id and str(scenario_id).strip():
        effective_scenario_id = str(scenario_id).strip()
        scen_row = db.get(ScenarioTable, effective_scenario_id)
        scen_set = db.get(ScenarioSet, scen_row.set_id) if scen_row else None
        if scen_row is None or scen_set is None or scen_set.project_id != project_id:
            raise ServiceError(
                404, "SCENARIO_NOT_FOUND",
                f"Scenario '{effective_scenario_id}' not found in project '{project_id}'.",
            )
        if scen_row.status not in lifecycle.RUNNABLE_INPUT_STATUSES:
            raise ServiceError(
                400, "SCENARIO_NOT_APPROVED",
                f"Scenario '{effective_scenario_id}' has status '{scen_row.status}'; must be validated/approved.",
            )
        if ps is not None and ps.scenario_ids and effective_scenario_id not in ps.scenario_ids:
            raise ServiceError(
                400, "SCENARIO_MISMATCH",
                f"Scenario '{effective_scenario_id}' is not configured on ProjectionSet '{ps.id}'.",
            )
    elif ps is not None:
        ps_scens = list(ps.scenario_ids or [])
        if len(ps_scens) == 0:
            raise ServiceError(400, "SCENARIO_REQUIRED", f"ProjectionSet '{ps.id}' has no scenarios configured.")
        if len(ps_scens) > 1:
            raise ServiceError(
                400,
                "AMBIGUOUS_SCENARIO_SELECTION",
                f"ProjectionSet '{ps.id}' has {len(ps_scens)} scenarios; an explicit 'scenario_id' is required.",
            )
        effective_scenario_id = ps_scens[0]
    else:
        raise ServiceError(
            400,
            "SCENARIO_REQUIRED",
            "Governed projection without projection_set_id requires an explicit 'scenario_id'; implicit scenario selection is forbidden.",
        )

    if valuation_date is not None and str(valuation_date).strip() != "":
        parsed_val_date = _parse_governed_date(valuation_date, "valuation_date")
        if ps is not None and ps.valuation_date and ps.valuation_date != parsed_val_date:
            raise ServiceError(
                400,
                "VALUATION_DATE_MISMATCH",
                f"Requested valuation_date '{parsed_val_date}' conflicts with ProjectionSet valuation_date '{ps.valuation_date}'.",
            )
    elif ps is not None and ps.valuation_date is not None:
        parsed_val_date = ps.valuation_date
    else:
        raise ServiceError(
            400,
            "VALUATION_DATE_REQUIRED",
            "Governed projection requires an explicit 'valuation_date' (YYYY-MM-DD); implicit date defaults are forbidden.",
        )

    actor = db.get(User, project.created_by) if project.created_by else None
    if actor is None:
        actor = db.query(User).first()

    # V6-P0-01: Check if effective_run_id already exists from an interrupted/retried execution
    existing_run = db.get(Run, effective_run_id)
    if existing_run is not None:
        if (
            existing_run.project_id != project_id
            or existing_run.run_type != "projection"
            or existing_run.model_version_id != mv.id
            or existing_run.scenario_id != effective_scenario_id
            or existing_run.valuation_date != parsed_val_date
            or existing_run.horizon_months != projection_months
        ):
            raise ServiceError(
                409,
                "RUN_ID_CONFLICT",
                f"Run '{effective_run_id}' already exists with different governed parameters.",
            )

        existing_ps = db.get(ProjectionSet, existing_run.projection_set_id) if existing_run.projection_set_id else None
        if existing_ps is not None:
            if existing_ps.inforce_file_ids != [inforce_file.id]:
                raise ServiceError(
                    409,
                    "RUN_RETRY_LINEAGE_MISMATCH",
                    f"Run '{effective_run_id}' was frozen with inforce files {existing_ps.inforce_file_ids}, "
                    f"but retry requested inforce file '{inforce_file.id}'. Cannot replay existing run under different inforce lineage.",
                )
            ps = existing_ps

        pkg_row_check = db.query(RunPackage).filter(RunPackage.run_id == existing_run.id).first()
        if pkg_row_check is not None and pkg_row_check.package:
            inforce_list = (pkg_row_check.package.get("configuration", {}) or {}).get("datasets", {}).get("inforce", [])
            if inforce_list:
                frozen_ids = [f.get("id") for f in inforce_list if isinstance(f, dict)]
                frozen_fps = [f.get("fingerprint") for f in inforce_list if isinstance(f, dict)]
                if inforce_file.id not in frozen_ids or inforce_file.fingerprint not in frozen_fps:
                    raise ServiceError(
                        409,
                        "RUN_RETRY_LINEAGE_MISMATCH",
                        f"Run '{effective_run_id}' package is frozen with inforce {frozen_ids} ({frozen_fps}), "
                        f"but retry requested '{inforce_file.id}' ({inforce_file.fingerprint}). Cannot replay existing run under different inforce lineage.",
                    )

    if existing_run is None and (ps is None or ps.inforce_file_ids != [inforce_file.id] or ps.horizon_months != projection_months or ps.valuation_date != parsed_val_date):
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
            scenario_ids=[effective_scenario_id],
            valuation_date=parsed_val_date,
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

    pkg_row: RunPackage | None = None
    if existing_run is None:
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
    else:
        # V5-P1-G: Recover or resume existing_run on the SAME immutable run_id without creating duplicates
        run_obj = existing_run
        pkg_row = db.query(RunPackage).filter(RunPackage.run_id == run_obj.id).first()
        if pkg_row is None:
            frozen_pkg = run_package_service.freeze(
                db=db,
                project=project,
                projection_set=ps,
                scenario_id=effective_scenario_id,
                user=actor,
                run_id=run_obj.id,
                run_set_id=run_obj.run_set_id,
            )
            run_obj.run_package_fingerprint = frozen_pkg.fingerprint
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

        if run_obj.status == run_state.RUNNING:
            rec_res = run_finalization.recover_run_finalization(db, run_obj.id)
            if rec_res.state in ("finalized", "already_finalized"):
                db.refresh(run_obj)
            else:
                # Interrupted mid-calculation before intended_outcome was recorded: mark attempt failed and reopen run as PENDING
                running_attempts = (
                    db.query(RunAttempt)
                    .filter(RunAttempt.run_id == run_obj.id, RunAttempt.status == run_state.RUNNING)
                    .all()
                )
                for att in running_attempts:
                    att.status = run_state.FAILED
                    att.completed_at = now_utc()
                    att.cleanup_status = "retained_noncanonical"
                    att.error_type = "TRANSIENT_INTERRUPTION"
                    att.error_message = "Attempt interrupted prior to terminal outcome; superseded by retry."
                run_obj.status = run_state.PENDING
                run_obj.accepted_attempt_number = None
                run_obj.completed_at = None
                db.commit()
        elif run_obj.status == run_state.FAILED:
            # Preserve failed RunAttempt rows as noncanonical audit evidence and reopen run for retry attempt
            run_obj.status = run_state.PENDING
            run_obj.accepted_attempt_number = None
            run_obj.completed_at = None
            db.commit()

    pkg_id = pkg_row.id
    pkg_fp = pkg_row.fingerprint

    # Seam for testing partial-write fault after initial Run/RunPackage commit
    _after_initial_run_commit_hook(db, effective_run_id)

    run_obj = db.get(Run, effective_run_id)
    if run_obj.status not in ("completed", "success") or not run_finalization.evidence_consistent(db, run_obj):
        db.expunge_all()
        final_status = run_execution_service.execute_run(db, effective_run_id)
        run_obj = db.get(Run, effective_run_id)
        if run_obj is None or (final_status not in ("completed", "success") and run_obj.status not in ("completed", "success")):
            status_str = getattr(run_obj, "status", "missing")
            raise ServiceError(
                500, "PROJECTION_EXECUTION_FAILED",
                f"Shared projection engine failed for run '{effective_run_id}' (status: {status_str}, final_status: {final_status}).",
            )

    if run_obj.accepted_attempt_number is None or run_obj.accepted_attempt_number < 1:
        raise ServiceError(
            500, "ACCEPTED_ATTEMPT_MISSING",
            f"Projection run '{run_obj.id}' completed without setting accepted_attempt_number.",
        )

    art_obj = (
        db.query(RunArtifact)
        .filter(
            RunArtifact.run_id == run_obj.id,
            RunArtifact.artifact_type == "outputs",
            RunArtifact.attempt_number == run_obj.accepted_attempt_number,
        )
        .first()
    )
    if art_obj is None:
        raise ServiceError(
            500, "PROJECTION_ARTIFACT_MISSING",
            f"Projection run '{run_obj.id}' completed without persisting an 'outputs' RunArtifact for accepted attempt {run_obj.accepted_attempt_number}.",
        )

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": run_obj.id,
        "artifact_id": art_obj.id,
        "accepted_attempt_number": run_obj.accepted_attempt_number,
        "attempt_count": run_obj.attempt_count,
        "run_package_id": pkg_id,
        "run_package_fingerprint": pkg_fp,
        "model_id": model.id,
        "model_version_id": mv.id,
        "product_code": model.product_code,
        "scenario_id": effective_scenario_id,
        "projection_set_id": ps.id,
        "inforce_file_id": inforce_file.id,
        "inforce_fingerprint": inforce_file.fingerprint,
        "valuation_date": str(run_obj.valuation_date),
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
    """Aggregate flows and stocks using Polars strictly from the accepted-attempt canonical RunArtifact (V5-P0-D)."""
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

    # V5-P0-D: Require explicit accepted_attempt_number on the completed Run (never guess attempt_count or 1)
    if proj_run.accepted_attempt_number is None or proj_run.accepted_attempt_number < 1:
        raise ServiceError(
            400, "ACCEPTED_ATTEMPT_MISSING",
            f"Projection run '{projection_run_id}' does not have a verified accepted_attempt_number.",
        )
    expected_attempt = proj_run.accepted_attempt_number

    # Resolve persisted RunArtifact from PostgreSQL strictly for the accepted attempt and canonical output type
    if artifact_id:
        art = db.get(RunArtifact, artifact_id)
        if art is None:
            raise ServiceError(404, "ARTIFACT_NOT_FOUND", f"RunArtifact '{artifact_id}' not found.")
        if art.run_id != proj_run.id:
            raise ServiceError(
                400, "ARTIFACT_RUN_MISMATCH",
                f"RunArtifact '{artifact_id}' belongs to run '{art.run_id}', not '{proj_run.id}'.",
            )
        if art.attempt_number != expected_attempt:
            raise ServiceError(
                400, "ARTIFACT_ATTEMPT_MISMATCH",
                f"RunArtifact '{artifact_id}' belongs to attempt {art.attempt_number}, not accepted attempt {expected_attempt}.",
            )
        if art.artifact_type not in CANONICAL_OUTPUT_ARTIFACT_TYPES:
            raise ServiceError(
                400, "INVALID_ARTIFACT_TYPE",
                f"RunArtifact '{artifact_id}' has type '{art.artifact_type}'; expected one of {CANONICAL_OUTPUT_ARTIFACT_TYPES}.",
            )
    else:
        art = (
            db.query(RunArtifact)
            .filter(
                RunArtifact.run_id == proj_run.id,
                RunArtifact.attempt_number == expected_attempt,
                RunArtifact.artifact_type.in_(CANONICAL_OUTPUT_ARTIFACT_TYPES),
            )
            .first()
        )

    if art is None:
        raise ServiceError(
            404, "ACCEPTED_ARTIFACT_NOT_FOUND",
            f"No canonical output RunArtifact found in database for run '{projection_run_id}' accepted attempt {expected_attempt}.",
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
        "accepted_attempt_number": expected_attempt,
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


def _build_illustrative_generic_pv_movements(
    discount_unwind: Decimal,
    cash_flow_release: Decimal,
) -> dict[str, Decimal]:
    """Return explicitly labeled non-regulatory illustrative generic PV rollforward components (V5-P0-E).

    Never fabricates VM21, VM22, LDTI, DAC, MRB, or ASC815 accounting movement names from generic PV math.
    """
    return {
        "discount_unwind_illustrative": discount_unwind,
        "period_cash_flow_release_illustrative": cash_flow_release,
    }


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
    """Calculate foundation present value reserve and persist canonical Valuation Run & RunPackage (V5-P0-E, V5-P1-H)."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed valuation execution requires an active database session.")

    if str(basis or "").strip().upper() == "TAX":
        raise ServiceError(400, "UNSUPPORTED_VALUATION_BASIS", "TAX basis is explicitly not supported at launch.")

    try:
        clean_basis = validate_basis(basis)
        clean_method = validate_methodology(methodology)
        validate_basis_methodology_pair(clean_basis, clean_method)
    except ValuationTaxonomyError as exc:
        raise ServiceError(400, "INVALID_VALUATION_TAXONOMY", str(exc)) from exc

    val_date_obj = _parse_governed_date(valuation_date, "valuation_date")

    upstream_run = db.get(Run, upstream_run_id)
    if upstream_run is None or upstream_run.project_id != project_id:
        raise ServiceError(404, "RUN_NOT_FOUND", f"Upstream projection run '{upstream_run_id}' not found in project '{project_id}'.")

    if not aggregated_cash_flows:
        raise ServiceError(422, "EMPTY_CASH_FLOWS", "Cannot execute valuation on empty aggregated cash flows.")

    # Compute opening reserve V_0 (at t=0 across m=1..T) and closing reserve V_1 (at t=1 across remaining m=2..T)
    # V5-P0-E: Both V_0, V_1, discount_unwind (V_0 * r_m), and cash_flow_release (-CF_1) are computed INDEPENDENTLY
    # from their formulas — NEVER as a balancing residual `closing - opening - cf_release`.
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
    # Independent discount unwind from V_0 * r_monthly (not a balancing plug!)
    discount_unwind_dec = Decimal(str(round(v0_float * r_monthly, 4)))

    movements_dec = _build_illustrative_generic_pv_movements(discount_unwind_dec, cf_release_dec)
    pv_res = float(closing_dec)

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

    val_run_id = f"run-val-{uuid.uuid4().hex[:12]}"
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
        scenario_id=upstream_run.scenario_id,
        valuation_date=val_date_obj,
        horizon_months=upstream_run.horizon_months or n_months,
        accepted_attempt_number=1,
        attempt_count=1,
    )
    db.add(val_run)
    db.flush()

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
        "illustrative_generic_pv_movements": {k: float(v) for k, v in movements_dec.items()},
        "attributed_movements": {k: float(v) for k, v in movements_dec.items()},
        "attribution_mode": "ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY",
        "regulatory_accounting_asserted": False,
        "cohort_id": str(resolved_cohort),
        "basis": clean_basis,
        "methodology": clean_method,
        "annual_discount_rate": annual_discount_rate,
    }

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
        "attribution_mode": "ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY",
        "regulatory_accounting_asserted": False,
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
    prior_valuation_run_id: str | None = None,
    tolerance_policy_id: str | None = None,
) -> dict[str, Any]:
    """Execute valuation-linked attribution framework and persist RollforwardSummary rows (V5-P0-E, V5-P1-H).

    Supports:
    1. Multi-period valuation comparison when `prior_valuation_run_id` is supplied:
       - Independently loads opening measure from `prior_valuation_run_id`'s persisted ValuationRunDetail.
       - Independently loads closing measure from `valuation_run_id`'s persisted ValuationRunDetail.
       - Verifies project, basis, methodology, cohort, and period date ordering across both runs.
       - Evaluates configured movements without ever computing a balancing residual to force continuity.
    2. Intra-run illustrative generic PV continuity when `prior_valuation_run_id` is None:
       - Uses independently calculated opening PV, illustrative generic PV movements, and closing PV.
       - Explicitly labels attribution_mode as 'ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY'.
    """
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
    # V6-P0-03 & V8-P0-02: Governed, bounded rollforward continuity tolerances & policy approval
    effective_policy_id = tolerance_policy_id or "tol-policy-m5-standard"
    if effective_policy_id not in GOVERNED_APPROVED_TOLERANCE_POLICIES:
        raise ServiceError(
            400, "TOLERANCE_POLICY_NOT_APPROVED",
            f"Tolerance policy '{effective_policy_id}' is not an approved governed tolerance policy.",
        )
    governed_policy = GOVERNED_APPROVED_TOLERANCE_POLICIES[effective_policy_id]
    max_policy_tol = governed_policy["max_tolerance"]

    try:
        f_tol = float(tolerance)
        if not math.isfinite(f_tol) or f_tol < 0.0:
            raise ServiceError(
                400, "INVALID_ROLLFORWARD_TOLERANCE",
                f"Rollforward tolerance must be a finite, non-negative number; got '{tolerance}'.",
            )
        tol_dec = Decimal(str(tolerance))
        if not tol_dec.is_finite() or tol_dec < Decimal("0"):
            raise ServiceError(
                400, "INVALID_ROLLFORWARD_TOLERANCE",
                f"Rollforward tolerance must be a finite, non-negative number; got '{tolerance}'.",
            )
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ServiceError(
            400, "INVALID_ROLLFORWARD_TOLERANCE",
            f"Rollforward tolerance must be a finite, non-negative number; got '{tolerance}'.",
        ) from exc

    if tol_dec > max_policy_tol:
        raise ServiceError(
            400, "GOVERNED_TOLERANCE_EXCEEDED",
            f"Requested rollforward tolerance {tol_dec} exceeds maximum governed policy threshold of {max_policy_tol} for policy '{effective_policy_id}'.",
        )
    attribution_mode = persisted_summary.get("attribution_mode", "ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY")

    if prior_valuation_run_id:
        if prior_valuation_run_id == valuation_run_id:
            raise ServiceError(
                400, "INVALID_PRIOR_VALUATION_RUN",
                "prior_valuation_run_id must reference a distinct prior-period valuation run.",
            )
        prior_run = db.get(Run, prior_valuation_run_id)
        if prior_run is None or prior_run.project_id != project_id:
            raise ServiceError(
                404, "RUN_NOT_FOUND",
                f"Prior valuation run '{prior_valuation_run_id}' not found in project '{project_id}'.",
            )
        if prior_run.run_type != "valuation" or prior_run.status not in ("completed", "success"):
            raise ServiceError(
                400, "INVALID_PRIOR_RUN_STATUS",
                f"Prior valuation run '{prior_valuation_run_id}' must be a completed valuation run.",
            )
        if prior_run.basis != clean_b:
            raise ServiceError(
                400, "ROLLFORWARD_BASIS_MISMATCH",
                f"Prior valuation run basis '{prior_run.basis}' mismatches current valuation run basis '{clean_b}'.",
            )
        if prior_run.methodology != clean_m:
            raise ServiceError(
                400, "ROLLFORWARD_METHODOLOGY_MISMATCH",
                f"Prior valuation run methodology '{prior_run.methodology}' mismatches current valuation run methodology '{clean_m}'.",
            )
        prior_detail = db.get(ValuationRunDetail, prior_valuation_run_id)
        if prior_detail is None or not prior_detail.summary_results:
            raise ServiceError(
                400, "PRIOR_VALUATION_DETAIL_MISSING",
                f"Prior valuation run '{prior_valuation_run_id}' has no persisted ValuationRunDetail.",
            )
        prior_summary = prior_detail.summary_results or {}
        prior_cohort = prior_summary.get("cohort_id") or prior_run.projection_key
        if prior_cohort and str(prior_cohort) != str(effective_cohort):
            raise ServiceError(
                400, "ROLLFORWARD_COHORT_MISMATCH",
                f"Prior valuation cohort '{prior_cohort}' mismatches current valuation cohort '{effective_cohort}'.",
            )
        if val_detail and prior_detail.valuation_date and val_detail.valuation_date:
            if prior_detail.valuation_date > val_detail.valuation_date:
                raise ServiceError(
                    400, "ROLLFORWARD_PERIOD_ORDER_INVALID",
                    f"Prior valuation_date ({prior_detail.valuation_date}) cannot be after current valuation_date ({val_detail.valuation_date}).",
                )

        prior_closing = prior_summary.get("closing_reserve", prior_summary.get("present_value_reserve"))
        current_closing = persisted_summary.get("closing_reserve", persisted_summary.get("present_value_reserve"))
        if prior_closing is None or current_closing is None:
            raise ServiceError(
                400, "ROLLFORWARD_INPUTS_MISSING",
                "Prior or current valuation run is missing persisted reserve balance.",
            )
        independent_opening_dec = Decimal(str(prior_closing))
        independent_ending_dec = Decimal(str(current_closing))

        if opening is not None and abs(Decimal(str(opening)) - independent_opening_dec) > tol_dec:
            raise ServiceError(
                422, "ROLLFORWARD_OPENING_MISMATCH",
                f"Supplied opening balance ({opening}) disagrees with prior valuation run reserve ({independent_opening_dec}).",
            )
        if ending is not None and abs(Decimal(str(ending)) - independent_ending_dec) > tol_dec:
            raise ServiceError(
                422, "ROLLFORWARD_DISCONTINUITY",
                f"Supplied closing balance ({ending}) disagrees with current valuation run reserve ({independent_ending_dec}).",
            )

        opening_dec = independent_opening_dec
        ending_dec = independent_ending_dec
        resolved_movements = movements if movements is not None else persisted_summary.get("period_movements")
        if resolved_movements is None:
            raise ServiceError(
                400, "ROLLFORWARD_MOVEMENTS_REQUIRED",
                "Cross-period valuation rollforward requires explicit observed period movements; automatic residual balancing is forbidden.",
            )
        attribution_mode = "MULTI_PERIOD_PERSISTED_VALUATION_COMPARISON"
    else:
        resolved_opening = opening if opening is not None else persisted_summary.get("opening_reserve")
        resolved_movements = (
            movements
            if movements is not None
            else persisted_summary.get("illustrative_generic_pv_movements", persisted_summary.get("attributed_movements"))
        )
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
        ending_dec = Decimal(str(resolved_ending))

        persisted_opening = persisted_summary.get("opening_reserve")
        if persisted_opening is not None and abs(opening_dec - Decimal(str(persisted_opening))) > tol_dec:
            raise ServiceError(
                422, "ROLLFORWARD_DISCONTINUITY",
                f"Rollforward opening balance ({opening_dec}) disagrees with persisted opening reserve ({persisted_opening}).",
            )

        persisted_closing = persisted_summary.get("closing_reserve", persisted_summary.get("present_value_reserve"))
        if persisted_closing is not None:
            persisted_closing_dec = Decimal(str(persisted_closing))
            if abs(ending_dec - persisted_closing_dec) > tol_dec:
                raise ServiceError(
                    422, "ROLLFORWARD_DISCONTINUITY",
                    f"Rollforward closing balance ({ending_dec}) disagrees with persisted valuation reserve ({persisted_closing_dec}) beyond tolerance ({tol_dec}).",
                )

    # V5-P0-E, V6-P0-03 & V8-P0-02: Validate source attribution of movements, amounts, and policy
    movements_dec: dict[str, Decimal] = {}
    has_unverified_movement_source = False

    if prior_valuation_run_id:
        persisted_movements = (persisted_summary.get("period_movements") or {})
        if movements is not None:
            if set(resolved_movements.keys()) != set(persisted_movements.keys()):
                has_unverified_movement_source = True
            else:
                for k, v in resolved_movements.items():
                    persisted_v = Decimal(str(persisted_movements[k]))
                    if abs(Decimal(str(v)) - persisted_v) > tol_dec:
                        has_unverified_movement_source = True
    else:
        # Intra-run path:
        persisted_movements = (
            persisted_summary.get("illustrative_generic_pv_movements")
            or persisted_summary.get("attributed_movements")
            or {}
        )
        if movements is not None:
            if set(resolved_movements.keys()) != set(persisted_movements.keys()):
                has_unverified_movement_source = True
            else:
                for k, v in resolved_movements.items():
                    persisted_v = Decimal(str(persisted_movements[k]))
                    if abs(Decimal(str(v)) - persisted_v) > tol_dec:
                        has_unverified_movement_source = True

    for k, v in resolved_movements.items():
        norm_key = str(k).strip().lower()
        if norm_key in FORBIDDEN_RESIDUAL_MOVEMENT_NAMES:
            raise ServiceError(
                422,
                "UNEXPLAINED_MOVEMENT_FORBIDDEN",
                f"Movement component '{k}' is a forbidden balancing/residual plug; unexplained movements cannot masquerade as attributed movements.",
            )
        try:
            f_v = float(v)
            if not math.isfinite(f_v):
                raise ServiceError(400, "INVALID_ROLLFORWARD_MOVEMENT", f"Movement component '{k}' has non-finite value '{v}'.")
            dec_v = Decimal(str(v))
            if not dec_v.is_finite():
                raise ServiceError(400, "INVALID_ROLLFORWARD_MOVEMENT", f"Movement component '{k}' has non-finite value '{v}'.")
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise ServiceError(400, "INVALID_ROLLFORWARD_MOVEMENT", f"Movement component '{k}' has invalid numeric value '{v}'.") from exc

        movements_dec[str(k)] = dec_v

    continuity = validate_continuity(opening_dec, movements_dec, ending_dec, tolerance=tol_dec)
    unexplained_residual = ending_dec - (opening_dec + continuity.sum_of_movements)

    if not continuity.is_continuous:
        raise ServiceError(
            422,
            "ROLLFORWARD_DISCONTINUITY",
            f"Actuarial rollforward assertion failure (unexplained_residual={unexplained_residual}): {continuity.error_message}",
        )

    # Encode verified vs unverified provenance and tolerance policy into trace_identifier (V8-P0-02)
    trace_token = uuid.uuid4().hex[:12]
    if has_unverified_movement_source:
        trace_id = f"UNVERIFIED_SOURCE:{trace_token}"
    else:
        trace_id = f"VERIFIED:{effective_policy_id}:{trace_token}"

    records = record_rollforward_summary(
        db=db,
        run_id=valuation_run_id,
        project_id=project_id,
        cohort_id=str(effective_cohort),
        methodology=clean_m,
        basis=clean_b,
        continuity_result=continuity,
        trace_id=trace_id,
    )

    # Persist immutable rollforward summary RunArtifact (V8-P0-02)
    rf_doc = {
        "valuation_run_id": valuation_run_id,
        "project_id": project_id,
        "cohort_id": str(effective_cohort),
        "is_continuous": continuity.is_continuous,
        "has_unverified_movement_source": has_unverified_movement_source,
        "tolerance_policy_id": effective_policy_id,
        "trace_identifier": trace_id,
        "opening_balance": float(opening_dec),
        "ending_balance": float(ending_dec),
        "movements": {k: float(v) for k, v in movements_dec.items()},
        "difference": float(continuity.difference),
        "tolerance": float(tol_dec),
    }
    rf_bytes = json.dumps(rf_doc, sort_keys=True).encode("utf-8")
    store = get_artifact_store()
    stored_rf = store.write_bytes(
        namespace=valuation_run_id,
        artifact_type="rollforward_summary",
        filename="rollforward_summary.json",
        content=rf_bytes,
    )
    rf_art = RunArtifact(
        id=f"art-rf-{uuid.uuid4().hex[:12]}",
        run_id=valuation_run_id,
        attempt_number=val_run.accepted_attempt_number or 1,
        artifact_type="rollforward_summary",
        storage_uri=stored_rf.uri,
        storage_backend="local",
        file_format="json",
        schema_version="m2-v1",
        row_count=len(records),
        checksum_sha256=hashlib.sha256(rf_bytes).hexdigest(),
        partition={
            "summary_id": records[0].id if records else "",
            "has_unverified_movement_source": has_unverified_movement_source,
            "tolerance_policy_id": effective_policy_id,
            "trace_identifier": trace_id,
        },
    )
    db.add(rf_art)
    db.commit()

    completed_at = datetime.now(timezone.utc).isoformat()
    return {
        "artifact_id": rf_art.id,
        "summary_id": records[0].id if records else "",
        "component_ids": [r.id for r in records],
        "component_count": len(records),
        "valuation_run_id": valuation_run_id,
        "prior_valuation_run_id": prior_valuation_run_id,
        "project_id": project_id,
        "cohort_id": str(effective_cohort),
        "basis": clean_b,
        "methodology": clean_m,
        "attribution_mode": attribution_mode,
        "regulatory_accounting_asserted": False,
        "is_continuous": continuity.is_continuous,
        "opening_balance": float(opening_dec),
        "sum_of_movements": float(continuity.sum_of_movements),
        "ending_balance": float(ending_dec),
        "difference": float(continuity.difference),
        "unexplained_residual": float(unexplained_residual),
        "tolerance": float(tol_dec),
        "tolerance_policy_id": effective_policy_id,
        "has_unverified_movement_source": has_unverified_movement_source,
        "trace_identifier": trace_id,
        "movements": {k: float(v) for k, v in movements_dec.items()},
        "started_at": started_at,
        "completed_at": completed_at,
    }


def evaluate_benchmark_matching(
    val_run: Run,
    val_detail: ValuationRunDetail,
    val_pkg: RunPackage | None,
    ers: ExpectedResultSet,
    target_metric: str = "present_value_reserve",
    tolerance: float | None = None,
    rel_tol: float | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    """Pure evaluation of valuation run results against an approved ExpectedResultSet (V8-P0-01).

    Revalidates:
    1. Active independent approval & Four-Eyes governance on ExpectedResultSet.
    2. ExpectedResultSet rows fingerprint integrity.
    3. Product family agreement between run and benchmark.
    4. Dimensional scoping: basis, methodology, valuation_date, cohort_id, scenario_id.
    5. Exact metric matching without arbitrary fallback.
    6. Governed tolerances and tolerance override boundaries.
    """
    persisted_summary = (val_detail.summary_results if val_detail and isinstance(val_detail.summary_results, dict) else {}) or {}
    if not target_metric or target_metric not in persisted_summary or persisted_summary[target_metric] is None:
        raise ServiceError(
            400, "INVALID_TARGET_METRIC",
            f"Persisted ValuationRunDetail for run '{val_run.id}' does not contain target_metric '{target_metric}'.",
        )
    persisted_actual = float(persisted_summary[target_metric])

    if ers.status != "approved" or not ers.approved_by or not ers.approved_at or ers.superseded_by_id is not None:
        raise ServiceError(
            400, "BENCHMARK_NOT_APPROVED",
            f"ExpectedResultSet '{ers.id}' must be in active 'approved' status with independent approver (status: {ers.status}).",
        )
    if not ers.created_by or ers.approved_by == ers.created_by:
        raise ServiceError(
            400, "SELF_APPROVED_BENCHMARK_FORBIDDEN",
            f"ExpectedResultSet '{ers.id}' violates four-eyes governance: created_by ('{ers.created_by}') must differ from approved_by ('{ers.approved_by}').",
        )

    computed_ers_fp = compute_fp(ers.rows_data)
    if ers.fingerprint != computed_ers_fp:
        raise ServiceError(
            400, "BENCHMARK_FINGERPRINT_MISMATCH",
            f"ExpectedResultSet '{ers.id}' rows do not match recorded fingerprint.",
        )

    pkg_cfg = (val_pkg.package.get("configuration", {}) if val_pkg and isinstance(val_pkg.package, dict) else {}) or {}
    db_model_prod = None
    if val_run.model_version_id and db is not None:
        val_mv = db.get(ModelVersion, val_run.model_version_id)
        if val_mv is not None:
            val_mod = db.get(Model, val_mv.model_id)
            if val_mod is not None:
                db_model_prod = val_mod.product_code

    run_product = _normalize_product_family(
        pkg_cfg.get("model", {}).get("product_code")
        or db_model_prod
        or persisted_summary.get("product_code")
    )
    ers_product = _normalize_product_family(ers.product_code)
    if ers_product == "UNKNOWN" or run_product == "UNKNOWN":
        raise ServiceError(
            400, "UNKNOWN_PRODUCT_FOR_BENCHMARK",
            f"Governed reconciliation requires recognized product codes (benchmark: '{ers.product_code}', run: '{run_product}').",
        )
    if ers_product != run_product:
        raise ServiceError(
            400, "BENCHMARK_PRODUCT_MISMATCH",
            f"ExpectedResultSet product '{ers.product_code}' ({ers_product}) mismatches valuation run product '{run_product}'.",
        )

    run_basis = str(val_run.basis or persisted_summary.get("basis") or "").strip().upper()
    run_method = str(val_run.methodology or persisted_summary.get("methodology") or "").strip().upper()
    run_cohort = str(persisted_summary.get("cohort_id") or val_run.projection_key or "").strip()
    run_val_date = str(val_detail.valuation_date) if val_detail.valuation_date else str(val_run.valuation_date or "")
    run_scenario_id = str(pkg_cfg.get("scenario", {}).get("id") or val_run.scenario_id or "").strip()

    if not run_basis or not run_method or not run_val_date:
        raise ServiceError(
            400, "REQUIRED_DIMENSION_MISSING",
            "Valuation run is missing required reconciliation dimension (basis, methodology, or valuation_date).",
        )

    if tolerance is not None and float(tolerance) < 0:
        raise ServiceError(400, "UNGOVERNED_TOLERANCE_OVERRIDE_FORBIDDEN", "Negative tolerance is invalid.")
    if rel_tol is not None and float(rel_tol) < 0:
        raise ServiceError(400, "UNGOVERNED_TOLERANCE_OVERRIDE_FORBIDDEN", "Negative relative tolerance is invalid.")

    rows = list(ers.rows_data or [])
    matching_rows: list[dict[str, Any]] = []
    scope_keys = ("basis", "methodology", "cohort_id", "valuation_date", "scenario_id")

    for row in rows:
        if not isinstance(row, dict):
            continue
        if len(rows) > 1 and not any(k in row and row[k] is not None for k in scope_keys):
            raise ServiceError(
                400, "AMBIGUOUS_BENCHMARK_ROWS",
                f"Multi-row ExpectedResultSet '{ers.id}' contains unscoped rows without basis/methodology/cohort_id/valuation_date/scenario_id.",
            )
        row_basis = row.get("basis")
        row_method = row.get("methodology")
        row_val_date = row.get("valuation_date")
        if not row_basis or not row_method or not row_val_date:
            continue

        if str(row_basis).strip().upper() != run_basis:
            continue
        if str(row_method).strip().upper() != run_method:
            continue
        if str(row_val_date).strip() != run_val_date:
            continue

        is_aggregate_contract = (
            str(row.get("scope_level", "")).upper() in ("PORTFOLIO", "AGGREGATE")
            or str(row.get("benchmark_scope", "")).upper() in ("PORTFOLIO", "AGGREGATE")
            or str(getattr(ers, "scope_level", "")).upper() in ("PORTFOLIO", "AGGREGATE")
        )
        if not is_aggregate_contract:
            if run_cohort:
                row_cohort = row.get("cohort_id")
                if not row_cohort or str(row_cohort).strip() != run_cohort:
                    continue
            elif row.get("cohort_id") is not None and str(row.get("cohort_id")).strip() != "":
                continue

            if run_scenario_id:
                row_scen = row.get("scenario_id")
                if not row_scen or str(row_scen).strip() != run_scenario_id:
                    continue
            elif row.get("scenario_id") is not None and str(row.get("scenario_id")).strip() != "":
                continue

        # Exact metric matching
        if "metric_name" in row:
            if str(row.get("metric_name")) != target_metric or row.get("expected_value") is None:
                continue
        else:
            if target_metric not in row or row[target_metric] is None:
                continue

        matching_rows.append(row)

    if len(matching_rows) > 1:
        raise ServiceError(
            400, "AMBIGUOUS_BENCHMARK_ROWS",
            f"ExpectedResultSet '{ers.id}' matched {len(matching_rows)} ambiguous rows for metric '{target_metric}'.",
        )

    if len(matching_rows) == 0:
        return {
            "status": "NOT_VERIFIABLE",
            "persisted_actual": persisted_actual,
            "expected_benchmark": None,
            "difference": 0.0,
            "allowed_tolerance": 0.0,
            "effective_abs_tol": GOVERNED_DEFAULT_ABS_TOL,
            "effective_rel_tol": GOVERNED_DEFAULT_REL_TOL,
            "reason": (
                f"Approved ExpectedResultSet '{ers.id}' contains no scoped row matching "
                f"(metric={target_metric}, basis={run_basis}, methodology={run_method}, cohort={run_cohort}, valuation_date={run_val_date})."
            ),
            "failures": [],
            "ers_fingerprint": ers.fingerprint,
            "ers_version": ers.version_label,
        }

    matched_row = matching_rows[0]
    raw_expected = matched_row["expected_value"] if "metric_name" in matched_row else matched_row[target_metric]
    benchmark_val = float(raw_expected)

    row_abs_tol = matched_row.get("tolerance_abs", matched_row.get("abs_tol"))
    row_rel_tol = matched_row.get("tolerance_rel", matched_row.get("rel_tol"))
    governed_abs_tol = float(row_abs_tol) if row_abs_tol is not None else GOVERNED_DEFAULT_ABS_TOL
    governed_rel_tol = float(row_rel_tol) if row_rel_tol is not None else GOVERNED_DEFAULT_REL_TOL

    if tolerance is not None and float(tolerance) > governed_abs_tol + 1e-12:
        raise ServiceError(
            400, "UNGOVERNED_TOLERANCE_OVERRIDE_FORBIDDEN",
            f"Caller absolute tolerance ({tolerance}) exceeds governed benchmark tolerance ({governed_abs_tol}).",
        )
    if rel_tol is not None and float(rel_tol) > governed_rel_tol + 1e-12:
        raise ServiceError(
            400, "UNGOVERNED_TOLERANCE_OVERRIDE_FORBIDDEN",
            f"Caller relative tolerance ({rel_tol}) exceeds governed benchmark relative tolerance ({governed_rel_tol}).",
        )

    effective_abs_tol = float(tolerance) if tolerance is not None else governed_abs_tol
    effective_rel_tol = float(rel_tol) if rel_tol is not None else governed_rel_tol

    diff = abs(persisted_actual - benchmark_val)
    allowed = effective_abs_tol + effective_rel_tol * abs(benchmark_val)
    failures = []
    if diff <= allowed:
        status = "PASS"
        reason = None
    else:
        status = "FAIL"
        reason = f"Actual ({persisted_actual}) differs from expected ({benchmark_val}) by {diff:.6f} exceeding allowed tolerance {allowed:.6f}."
        failures.append({
            "metric": target_metric,
            "actual": persisted_actual,
            "expected": benchmark_val,
            "difference": diff,
            "allowed_tolerance": allowed,
        })

    return {
        "status": status,
        "persisted_actual": persisted_actual,
        "expected_benchmark": benchmark_val,
        "difference": diff,
        "allowed_tolerance": allowed,
        "effective_abs_tol": effective_abs_tol,
        "effective_rel_tol": effective_rel_tol,
        "reason": reason,
        "failures": failures,
        "ers_fingerprint": ers.fingerprint,
        "ers_version": ers.version_label,
    }


def execute_governed_reconciliation(
    db: Session,
    valuation_run_id: str,
    project_id: str,
    actual_reserve: float | None = None,
    expected_result_set_id: str | None = None,
    expected_benchmark: float | None = None,
    tolerance: float | None = None,
    rel_tol: float | None = None,
    target_metric: str = "present_value_reserve",
) -> dict[str, Any]:
    """Execute three-state reconciliation strictly against an independently approved ExpectedResultSet (V5-P0-F, V8-P0-01).

    Enforces:
    1. Raw `expected_benchmark` is unconditionally forbidden (even if `expected_result_set_id` is passed).
    2. Authoritative `actual_value` is loaded directly from persisted `ValuationRunDetail.summary_results[target_metric]`;
       caller spoofing of `actual_reserve` is rejected (`400 ACTUAL_VALUE_SPOOF_FORBIDDEN`).
    3. `ExpectedResultSet` must be in active `approved` status with Four-Eyes separation (`created_by != approved_by`).
    4. Benchmark rows are matched strictly by scoped keys (`product_code`, `basis`, `methodology`, `cohort_id`,
       `valuation_date`, `scenario_id`, and exact `target_metric`) with zero fallback to unrelated keys or blind `rows_data[-1]`.
    5. Governed tolerances come from the approved benchmark row (or platform governed maximum `0.01` / `1e-4`);
       inflated caller tolerances are rejected (`400 UNGOVERNED_TOLERANCE_OVERRIDE_FORBIDDEN`).
    """
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed reconciliation execution requires an active database session.")

    val_run = db.get(Run, valuation_run_id)
    if val_run is None or val_run.project_id != project_id:
        raise ServiceError(404, "RUN_NOT_FOUND", f"Valuation run '{valuation_run_id}' not found in project '{project_id}'.")

    # 1. Unconditionally reject raw caller benchmark overrides
    if expected_benchmark is not None:
        raise ServiceError(
            400, "UNGOVERNED_BENCHMARK_FORBIDDEN",
            "Raw caller-supplied 'expected_benchmark' is unconditionally forbidden; governed reconciliation must use an approved ExpectedResultSet.",
        )

    # 2. Load authoritative actual value from persisted ValuationRunDetail
    val_detail = db.get(ValuationRunDetail, valuation_run_id)
    if val_detail is None or not isinstance(val_detail.summary_results, dict):
        raise ServiceError(
            400, "VALUATION_DETAIL_MISSING",
            f"Valuation run '{valuation_run_id}' has no persisted ValuationRunDetail summary_results.",
        )

    persisted_summary = val_detail.summary_results
    if not target_metric or target_metric not in persisted_summary or persisted_summary[target_metric] is None:
        raise ServiceError(
            400, "INVALID_TARGET_METRIC",
            f"Persisted ValuationRunDetail for run '{valuation_run_id}' does not contain target_metric '{target_metric}'.",
        )

    persisted_actual = float(persisted_summary[target_metric])
    if actual_reserve is not None and abs(float(actual_reserve) - persisted_actual) > 1e-6:
        raise ServiceError(
            400, "ACTUAL_VALUE_SPOOF_FORBIDDEN",
            f"Caller-supplied actual_reserve ({actual_reserve}) disagrees with persisted ValuationRunDetail.{target_metric} ({persisted_actual}).",
        )

    val_pkg = db.query(RunPackage).filter(RunPackage.run_id == val_run.id).first()

    if not expected_result_set_id:
        status = "NOT_VERIFIABLE"
        diff = 0.0
        reason = "No approved ExpectedResultSet supplied for governed reconciliation."
        benchmark_val = None
        effective_abs_tol = GOVERNED_DEFAULT_ABS_TOL
        effective_rel_tol = GOVERNED_DEFAULT_REL_TOL
        ers_fp = ""
        ers_version = "v1"
        failures = []
    else:
        ers = db.get(ExpectedResultSet, expected_result_set_id)
        if not ers or ers.project_id != project_id:
            raise ServiceError(
                404, "BENCHMARK_NOT_FOUND",
                f"ExpectedResultSet '{expected_result_set_id}' not found in project '{project_id}'.",
            )

        eval_res = evaluate_benchmark_matching(
            val_run=val_run,
            val_detail=val_detail,
            val_pkg=val_pkg,
            ers=ers,
            target_metric=target_metric,
            tolerance=tolerance,
            rel_tol=rel_tol,
            db=db,
        )
        status = eval_res["status"]
        diff = eval_res["difference"]
        reason = eval_res["reason"]
        benchmark_val = eval_res["expected_benchmark"]
        effective_abs_tol = eval_res["effective_abs_tol"]
        effective_rel_tol = eval_res["effective_rel_tol"]
        ers_fp = eval_res["ers_fingerprint"]
        ers_version = eval_res["ers_version"]
        failures = eval_res["failures"]

    rec_report = ReconciliationReport(
        id=f"rec-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        run_id=valuation_run_id,
        expected_result_set_id=expected_result_set_id,
        variables_requested=[target_metric],
        expected_result_fingerprint=ers_fp,
        expected_result_version=ers_version,
        status=status,
        abs_tol=effective_abs_tol,
        rel_tol=effective_rel_tol,
        disposition_status="auto_evaluated",
        summary={
            "actual": persisted_actual,
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
        "actual_value": persisted_actual,
        "expected_benchmark": benchmark_val,
        "difference": diff,
        "tolerance": effective_abs_tol,
        "rel_tol": effective_rel_tol,
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
    require_approved_reconciliation: bool = False,
) -> dict[str, Any]:
    """Assemble reporting manifest, strictly deriving release approval from verified persisted evidence (V8-P0-01, V8-P0-02, V8-P1-03)."""
    started_at = datetime.now(timezone.utc).isoformat()
    if db is None:
        raise ServiceError(500, "DATABASE_REQUIRED", "Governed reporting export requires an active database session.")

    val_run = db.get(Run, valuation_run_id)
    if val_run is None or val_run.project_id != project_id:
        raise ServiceError(404, "RUN_NOT_FOUND", f"Valuation run '{valuation_run_id}' not found in project '{project_id}'.")

    # 1. Valuation RunDetail integrity
    val_detail = db.get(ValuationRunDetail, valuation_run_id)
    val_pkg = db.query(RunPackage).filter(RunPackage.run_id == valuation_run_id).first()

    persisted_reserve = None
    val_detail_consistent = True
    if val_detail is None or not val_detail.summary_results:
        val_detail_consistent = False
    else:
        persisted_reserve = val_detail.summary_results.get("present_value_reserve")
        if persisted_reserve is None:
            val_detail_consistent = False
        elif valuation_result.get("present_value_reserve") is not None:
            try:
                if abs(float(valuation_result["present_value_reserve"]) - float(persisted_reserve)) > 1e-4:
                    val_detail_consistent = False
            except (ValueError, TypeError):
                val_detail_consistent = False

    # 2. V8-P1-03: Frozen RunPackage binding to canonical valuation run and project
    pkg_consistent = True
    if val_pkg is None:
        pkg_consistent = False
    else:
        if val_pkg.project_id != project_id or val_pkg.run_id != valuation_run_id:
            pkg_consistent = False
        if val_run.run_package_fingerprint and val_pkg.fingerprint != val_run.run_package_fingerprint:
            pkg_consistent = False

        doc = val_pkg.package if isinstance(val_pkg.package, dict) else {}
        identity = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
        config = doc.get("configuration") if isinstance(doc.get("configuration"), dict) else {}

        if identity.get("package_id") and identity["package_id"] != val_pkg.id:
            pkg_consistent = False
        if identity.get("frozen_for_run_id") and identity["frozen_for_run_id"] != valuation_run_id:
            pkg_consistent = False
        if identity.get("project_id") and identity["project_id"] != project_id:
            pkg_consistent = False
        if doc.get("fingerprint") and doc["fingerprint"] != val_pkg.fingerprint:
            pkg_consistent = False

        val_block = config.get("valuation") or {}
        if val_block:
            if val_run.basis and val_block.get("basis") and str(val_block.get("basis")).strip().upper() != str(val_run.basis).strip().upper():
                pkg_consistent = False
            if val_run.methodology and val_block.get("methodology") and str(val_block.get("methodology")).strip().upper() != str(val_run.methodology).strip().upper():
                pkg_consistent = False

        # Strict valuation envelope identity binding (V8-P1-03)
        if identity.get("package_id") != val_pkg.id:
            pkg_consistent = False
        if identity.get("frozen_for_run_id") != valuation_run_id or val_pkg.run_id != valuation_run_id or val_run.id != valuation_run_id:
            pkg_consistent = False
        if identity.get("project_id") != project_id or val_pkg.project_id != project_id or val_run.project_id != project_id:
            pkg_consistent = False
        if (config.get("project") or {}).get("id") and (config.get("project") or {}).get("id") != project_id:
            pkg_consistent = False
        if not val_run.run_package_fingerprint or val_run.run_package_fingerprint != val_pkg.fingerprint or doc.get("fingerprint") != val_pkg.fingerprint:
            pkg_consistent = False

        pkg_basis = (config.get("valuation") or {}).get("basis") or config.get("basis")
        if val_run.basis and pkg_basis and str(pkg_basis).strip().upper() != str(val_run.basis).strip().upper():
            pkg_consistent = False
        pkg_method = (config.get("valuation") or {}).get("methodology") or config.get("methodology")
        if val_run.methodology and pkg_method and str(pkg_method).strip().upper() != str(val_run.methodology).strip().upper():
            pkg_consistent = False
        pkg_vdate = (config.get("valuation") or {}).get("valuation_date") or config.get("valuation_date")
        if val_run.valuation_date and pkg_vdate and str(pkg_vdate).strip() != str(val_run.valuation_date).strip():
            pkg_consistent = False
        pkg_mv = (config.get("model_version") or {}).get("id")
        if val_run.model_version_id and pkg_mv and pkg_mv != val_run.model_version_id:
            pkg_consistent = False

        try:
            core_run_pkg.verify(doc, val_pkg.fingerprint)
        except Exception:
            pkg_consistent = False

        if valuation_result.get("run_package_id") and valuation_result["run_package_id"] != val_pkg.id:
            pkg_consistent = False
        if valuation_result.get("run_package_fingerprint") and valuation_result["run_package_fingerprint"] != val_pkg.fingerprint:
            pkg_consistent = False

    # 3. V8-P0-01: Reconciliation approval integrity
    rec_report_id = reconciliation_result.get("report_id")
    rec_row = db.get(ReconciliationReport, rec_report_id) if rec_report_id else None
    if rec_row is None or rec_row.project_id != project_id or rec_row.run_id != valuation_run_id:
        persisted_rec_status = "NOT_VERIFIABLE"
        rec_verified = False
        actual_rec_diff = float(reconciliation_result.get("difference", 0.0))
        actual_rec_reason = reconciliation_result.get("reason") or "Persisted ReconciliationReport missing or inconsistent with project/run."
    else:
        persisted_rec_status = rec_row.status
        actual_rec_diff = float((rec_row.summary or {}).get("difference") or 0.0)
        actual_rec_reason = (rec_row.summary or {}).get("reason") or rec_row.disposition_notes or ""

        # Prescribed required metric for foundation package: present_value_reserve MUST be requested
        req_metric = "present_value_reserve"
        has_required_metric = (
            rec_row.variables_requested is not None
            and req_metric in rec_row.variables_requested
        )

        ers_match_ok = False
        eval_status_ok = False
        if rec_row.expected_result_set_id and has_required_metric:
            ers = db.get(ExpectedResultSet, rec_row.expected_result_set_id)
            if (
                ers is not None
                and ers.project_id == project_id
                and ers.status == "approved"
                and ers.approved_by is not None
                and ers.created_by is not None
                and ers.approved_by != ers.created_by
                and ers.fingerprint == rec_row.expected_result_fingerprint
                and ers.version_label == rec_row.expected_result_version
            ):
                try:
                    eval_res = evaluate_benchmark_matching(
                        val_run=val_run,
                        val_detail=val_detail,
                        val_pkg=val_pkg,
                        ers=ers,
                        target_metric=req_metric,
                        tolerance=rec_row.abs_tol,
                        rel_tol=rec_row.rel_tol,
                        db=db,
                    )
                    rec_actual = (rec_row.summary or {}).get("actual")
                    rec_expected = (rec_row.summary or {}).get("expected")

                    actual_matches = (
                        rec_actual is not None
                        and abs(float(rec_actual) - eval_res["persisted_actual"]) < 1e-6
                    )
                    expected_matches = (
                        rec_expected is not None
                        and eval_res["expected_benchmark"] is not None
                        and abs(float(rec_expected) - eval_res["expected_benchmark"]) < 1e-6
                    )

                    if eval_res["status"] == "PASS" and actual_matches and expected_matches:
                        eval_status_ok = True
                        ers_match_ok = True
                except Exception:
                    eval_status_ok = False
                    ers_match_ok = False

        all_rec_reports = db.query(ReconciliationReport).filter(
            ReconciliationReport.run_id == valuation_run_id,
            ReconciliationReport.project_id == project_id,
        ).all()

        has_failing_rec = any(r.status in ("FAIL", "ERROR") for r in all_rec_reports)
        has_contradictory_for_ers = any(
            r.expected_result_set_id == rec_row.expected_result_set_id
            and r.status in ("FAIL", "ERROR", "NOT_VERIFIABLE")
            and r.id != rec_row.id
            for r in all_rec_reports
        )
        pv_reports = [r for r in all_rec_reports if r.variables_requested and req_metric in r.variables_requested]
        has_passing_required_report = any(r.status == "PASS" for r in pv_reports)

        rec_verified = (
            rec_row.status == "PASS"
            and has_required_metric
            and ers_match_ok
            and eval_status_ok
            and not has_failing_rec
            and not has_contradictory_for_ers
            and has_passing_required_report
            and reconciliation_result.get("status") == rec_row.status
        )

    # 4. V8-P0-02: Authenticate persisted rollforward RunArtifact, physical file, checksum, and DB rows
    rf_arts = db.query(RunArtifact).filter(
        RunArtifact.run_id == valuation_run_id,
        RunArtifact.artifact_type == "rollforward_summary",
    ).all()

    selected_rf_art = None
    explicit_rf_art_id = rollforward_result.get("artifact_id")
    explicit_rf_trace = rollforward_result.get("trace_identifier")
    explicit_rf_sum_id = rollforward_result.get("summary_id")

    if explicit_rf_art_id:
        for art in rf_arts:
            if art.id == explicit_rf_art_id:
                selected_rf_art = art
                break
    elif explicit_rf_trace:
        for art in rf_arts:
            part = art.partition if isinstance(art.partition, dict) else {}
            if part.get("trace_identifier") == explicit_rf_trace:
                selected_rf_art = art
                break
    elif explicit_rf_sum_id:
        for art in rf_arts:
            part = art.partition if isinstance(art.partition, dict) else {}
            if part.get("summary_id") == explicit_rf_sum_id:
                selected_rf_art = art
                break
    elif len(rf_arts) == 1:
        selected_rf_art = rf_arts[0]

    persisted_rf_ok = False
    rf_verified = False
    canonical_rf_rows: list[RollforwardSummary] = []
    rf_persisted_movements: dict[str, float] = {}
    rf_primary: RollforwardSummary | None = None

    if selected_rf_art is not None:
        try:
            store = get_artifact_store()
            rf_path = store.path_for_uri(selected_rf_art.storage_uri)
            if rf_path.exists():
                raw_rf_bytes = rf_path.read_bytes()
                actual_rf_sha = hashlib.sha256(raw_rf_bytes).hexdigest()
                if actual_rf_sha == selected_rf_art.checksum_sha256:
                    rf_doc = json.loads(raw_rf_bytes.decode("utf-8"))
                    if (
                        rf_doc.get("valuation_run_id") == valuation_run_id
                        and rf_doc.get("project_id") == project_id
                        and rf_doc.get("is_continuous") is True
                        and not rf_doc.get("has_unverified_movement_source")
                        and rf_doc.get("tolerance_policy_id") in GOVERNED_APPROVED_TOLERANCE_POLICIES
                    ):
                        trace_id = rf_doc.get("trace_identifier", "")
                        pol_id = rf_doc.get("tolerance_policy_id")
                        if trace_id.startswith(f"VERIFIED:{pol_id}:"):
                            canonical_rf_rows = db.query(RollforwardSummary).filter(
                                RollforwardSummary.run_id == valuation_run_id,
                                RollforwardSummary.project_id == project_id,
                                RollforwardSummary.trace_identifier == trace_id,
                            ).all()
                            if canonical_rf_rows:
                                all_continuous = all(bool(r.is_continuity_verified) for r in canonical_rf_rows)
                                primary_candidates = [r for r in canonical_rf_rows if not r.is_movement]
                                rf_primary = primary_candidates[0] if primary_candidates else canonical_rf_rows[0]

                                db_movements = {
                                    r.component_name: float(r.movement_amount)
                                    for r in canonical_rf_rows if r.is_movement
                                }
                                art_movements = {
                                    str(k): float(v)
                                    for k, v in (rf_doc.get("movements") or {}).items()
                                }

                                movements_match = (
                                    set(db_movements.keys()) == set(art_movements.keys())
                                    and all(abs(db_movements[k] - art_movements[k]) < 1e-4 for k in db_movements)
                                )

                                balance_match = (
                                    abs(float(rf_primary.opening_balance) - float(rf_doc.get("opening_balance", 0.0))) < 1e-4
                                    and abs(float(rf_primary.closing_balance) - float(rf_doc.get("ending_balance", 0.0))) < 1e-4
                                )

                                if all_continuous and movements_match and balance_match:
                                    persisted_rf_ok = True
                                    rf_verified = True
                                    rf_persisted_movements = db_movements
                                    if bool(rollforward_result.get("is_continuous")) != all_continuous:
                                        rf_verified = False
        except Exception:
            rf_verified = False
            persisted_rf_ok = False

    if not val_detail_consistent or not pkg_consistent:
        governance_status = "REJECTED_NON_APPROVED"
        is_approved_for_release = False
        artifact_type = "reporting_quarantined"
        filename = "quarantined_diagnostic_manifest.json"
        package_id = f"QUARANTINED-{valuation_run_id}"
    elif persisted_rec_status == "FAIL" or not persisted_rf_ok or not rf_verified:
        governance_status = "REJECTED_NON_APPROVED"
        is_approved_for_release = False
        artifact_type = "reporting_quarantined"
        filename = "quarantined_diagnostic_manifest.json"
        package_id = f"QUARANTINED-{valuation_run_id}"
    elif persisted_rec_status != "PASS" or not rec_verified:
        governance_status = "TECHNICAL_MATERIALIZATION_UNVERIFIED_NON_APPROVED"
        is_approved_for_release = False
        artifact_type = "reporting_unverified"
        filename = "unverified_technical_manifest.json"
        package_id = f"UNVERIFIED-{valuation_run_id}"
    else:
        governance_status = "GOVERNED_RECONCILED_FOUNDATION_PACKAGE"
        is_approved_for_release = True
        artifact_type = "reporting"
        filename = "disclosure_package.json"
        package_id = f"DISCLOSURE-{valuation_run_id}"

    if require_approved_reconciliation and not is_approved_for_release:
        raise ServiceError(
            422,
            "REPORTING_APPROVAL_GATE_FAILED",
            f"Cannot emit approved disclosure package: reconciliation_status='{persisted_rec_status}', "
            f"rollforward_verified={persisted_rf_ok} (governance_status='{governance_status}').",
        )

    val_date_str = (
        val_run.valuation_date.isoformat()
        if val_run and val_run.valuation_date is not None
        else valuation_result.get("valuation_date")
    )

    rf_primary_item = rf_primary
    summary_package = {
        "valuation_run_id": valuation_run_id,
        "upstream_projection_run_id": val_run.source_projection_run_id if val_run else valuation_result.get("upstream_run_id"),
        "project_id": project_id,
        "basis": val_run.basis if val_run and val_run.basis else valuation_result.get("basis"),
        "methodology": val_run.methodology if val_run and val_run.methodology else valuation_result.get("methodology"),
        "cohort_id": (rf_primary_item.cohort_id if rf_primary_item else val_run.projection_key if val_run else valuation_result.get("cohort_id")),
        "valuation_date": val_date_str,
        "run_package_id": val_pkg.id if val_pkg else None,
        "run_package_fingerprint": val_pkg.fingerprint if val_pkg else None,
        "reserve": persisted_reserve if persisted_reserve is not None else valuation_result.get("present_value_reserve"),
        "governance_status": governance_status,
        "is_approved_for_release": is_approved_for_release,
        "regulatory_compliance_claimed": False,
        "rollforward": {
            "summary_id": rf_primary_item.id if rf_primary_item else None,
            "artifact_id": selected_rf_art.id if selected_rf_art else None,
            "is_continuous": persisted_rf_ok,
            "attribution_mode": (
                "MULTI_PERIOD_PERSISTED_VALUATION_COMPARISON"
                if val_run and getattr(val_run, "prior_valuation_run_id", None)
                else "ILLUSTRATIVE_GENERIC_PV_NON_REGULATORY"
            ),
            "opening": float(rf_primary_item.opening_balance) if rf_primary_item else None,
            "movements": rf_persisted_movements if rf_persisted_movements else rollforward_result.get("movements", {}),
            "ending": float(rf_primary_item.closing_balance) if rf_primary_item else None,
            "difference": float(rf_primary_item.continuity_difference) if rf_primary_item else 0.0,
        },
        "reconciliation": {
            "report_id": rec_row.id if rec_row else None,
            "expected_result_set_id": getattr(rec_row, "expected_result_set_id", None),
            "status": persisted_rec_status,
            "difference": actual_rec_diff,
            "reason": actual_rec_reason,
        },
    }

    manifest_bytes = json.dumps(summary_package, sort_keys=True).encode("utf-8")
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    store = get_artifact_store()
    stored = store.write_bytes(
        namespace=valuation_run_id,
        artifact_type=artifact_type,
        filename=filename,
        content=manifest_bytes,
    )

    rep_art = RunArtifact(
        id=f"art-rep-{uuid.uuid4().hex[:12]}",
        run_id=valuation_run_id,
        attempt_number=1,
        artifact_type=artifact_type,
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
        "package_id": package_id,
        "artifact_id": rep_art.id,
        "artifact_type": artifact_type,
        "valuation_run_id": valuation_run_id,
        "governance_status": governance_status,
        "is_approved_for_release": is_approved_for_release,
        "regulatory_compliance_claimed": False,
        "rollforward_verified": persisted_rf_ok,
        "reconciliation_status": persisted_rec_status,
        "manifest_sha256": actual_manifest_sha256,
        "storage_uri": stored.uri,
        "summary": summary_package,
        "started_at": started_at,
        "completed_at": completed_at,
    }
