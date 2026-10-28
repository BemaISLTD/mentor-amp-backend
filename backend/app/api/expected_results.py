"""Expected-result benchmark and governed reconciliation APIs."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path, get_current_user
from app.db.database import get_db
from app.db.models.reconciliation import ExpectedResultSet, ReconciliationReport
from app.db.models.user import User
from app.services import access, reconciliation_service
from app.services.common import ServiceError, not_found

router = APIRouter(tags=["reconciliation"])
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
RunReader = Annotated[User, Depends(authorize_path("run", "run_id", "projects:read"))]

EXPECTED_RESULT_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["name", "product_code", "rows"],
                    "properties": {
                        "name": {"type": "string"},
                        "product_code": {"type": "string"},
                        "version_label": {"type": "string", "default": "v1"},
                        "source_reference": {"type": "string"},
                        "timing_convention": {"type": "string", "nullable": True},
                        "granularity": {
                            "type": "string", "enum": ["policy_month", "aggregate_year"],
                            "default": "policy_month",
                        },
                        "rows": {
                            "type": "array", "items": {"type": "object", "additionalProperties": True},
                        },
                    },
                }
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "name", "product_code"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "name": {"type": "string"},
                        "product_code": {"type": "string"},
                        "version_label": {"type": "string", "default": "v1"},
                        "timing_convention": {"type": "string"},
                        "granularity": {
                            "type": "string", "enum": ["policy_month", "aggregate_year"],
                        },
                    },
                }
            },
        },
    }
}


class ReconcileRequest(BaseModel):
    expected_result_set_id: str
    policy_id: str | None = None
    level: Literal["policy_month", "aggregate_year", "both"] = "policy_month"
    abs_tol: float = Field(0.01, ge=0)
    rel_tol: float = Field(0.0001, ge=0)
    variables: list[str] | None = None


class DispositionRequest(BaseModel):
    disposition_status: Literal["approved", "approved_with_exception", "exception_noted", "rejected"]
    disposition_notes: str | None = None


def _permissions(user: User) -> set[str]:
    return {permission.name for role in user.roles for permission in role.permissions}


def _require_expected_writer(db: Session, user: User, project_id: str) -> None:
    if not ({"imports:write", "registries:write"} & _permissions(user)):
        raise ServiceError(403, "EXPECTED_RESULTS_WRITE_FORBIDDEN", "Expected-result write permission is required.")
    access.require_project_access(db, user, project_id, write=True)


@router.post(
    "/projects/{project_id}/expected-results",
    status_code=201,
    openapi_extra=EXPECTED_RESULT_UPLOAD_OPENAPI,
)
async def upload_expected_results(
    project_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    _require_expected_writer(db, user, project_id)
    cl = request.headers.get("content-length")
    if cl and int(cl) > 10 * 1024 * 1024:
        raise ServiceError(422, "PAYLOAD_TOO_LARGE", "Expected results upload exceeds maximum 10 MB limit.")
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        uploaded = form.get("file")
        if uploaded is None or not hasattr(uploaded, "read"):
            raise ServiceError(422, "EXPECTED_RESULTS_FILE_REQUIRED", "Multipart upload requires a CSV file.")
        rows, _ = reconciliation_service.parse_csv(await uploaded.read())
        payload: dict[str, Any] = {
            "rows": rows,
            "name": str(form.get("name") or uploaded.filename or "Expected results"),
            "product_code": str(form.get("product_code") or "unknown"),
            "version_label": str(form.get("version_label") or "v1"),
            "timing_convention": form.get("timing_convention"),
            "granularity": str(form.get("granularity") or "policy_month"),
            "source_reference": uploaded.filename or "csv-upload",
        }
    else:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise ServiceError(
                422, "EXPECTED_RESULTS_JSON", "Request body must contain valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise ServiceError(
                422, "EXPECTED_RESULTS_JSON", "Request body must be a JSON object."
            )
    for field in ("name", "product_code"):
        if not str(payload.get(field) or "").strip():
            raise ServiceError(422, "EXPECTED_RESULTS_METADATA", f"'{field}' is required.")
    return reconciliation_service.create_expected_set(db, project_id, payload, user.id)


@router.get("/projects/{project_id}/expected-results")
def list_expected_results(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    rows = db.query(ExpectedResultSet).filter(
        ExpectedResultSet.project_id == project_id
    ).order_by(ExpectedResultSet.created_at.desc()).all()
    return {"expected_result_sets": [reconciliation_service.expected_view(row) for row in rows]}


@router.get("/expected-results/{expected_result_id}")
def get_expected_results(
    expected_result_id: str,
    user: Annotated[User, Depends(get_current_user)],
    include_rows: bool = Query(True),
    db: Session = Depends(get_db),
):
    access.require_object_access(db, user, "expected_result_set", expected_result_id)
    row = db.get(ExpectedResultSet, expected_result_id)
    return reconciliation_service.expected_view(row, include_rows=include_rows)


@router.post("/runs/{run_id}/reconcile", status_code=201)
def reconcile_run(
    run_id: str, payload: ReconcileRequest, user: RunReader, db: Session = Depends(get_db)
):
    return reconciliation_service.reconcile(db, run_id, payload.model_dump(), user.id)


@router.get("/runs/{run_id}/reconciliations")
def list_reconciliations(run_id: str, user: RunReader, db: Session = Depends(get_db)):
    del user
    rows = db.query(ReconciliationReport).filter(
        ReconciliationReport.run_id == run_id
    ).order_by(ReconciliationReport.created_at.desc()).all()
    return {"reconciliations": [reconciliation_service.report_view(row) for row in rows]}


@router.get("/reconciliations/{reconciliation_id}")
def get_reconciliation(
    reconciliation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    access.require_object_access(db, user, "reconciliation_report", reconciliation_id)
    row = db.get(ReconciliationReport, reconciliation_id)
    return reconciliation_service.report_view(row)


@router.post("/reconciliations/{reconciliation_id}/disposition")
def set_reconciliation_disposition(
    reconciliation_id: str,
    payload: DispositionRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    access.require_object_access(db, user, "reconciliation_report", reconciliation_id)
    row = db.get(ReconciliationReport, reconciliation_id)
    roles = {role.name for role in user.roles}
    if "models:approve" not in _permissions(user) and "admin" not in roles:
        raise ServiceError(403, "RECONCILIATION_REVIEW_FORBIDDEN", "Reviewer permission is required.")
    return reconciliation_service.disposition(
        db, row, payload.disposition_status, payload.disposition_notes, user.id
    )
