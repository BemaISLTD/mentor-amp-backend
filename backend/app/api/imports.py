"""API endpoints for file import and preview."""

import shutil
import uuid
from contextlib import contextmanager
from math import ceil
from pathlib import Path
from typing import Annotated, Iterator, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import Float, cast
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.core.project_lifecycle import require_active_project
from app.data.importers.base import detect_format, import_file, parse_file, preview_file
from app.core import lifecycle
from app.core.execution.fingerprints import inforce_fingerprint
from app.data.validation.inforce_validator import validate_inforce
from app.data.validation.assumption_validator import validate_assumptions
from app.data.validation.factor_validator import validate_factors
from app.data.validation.scenario_validator import validate_scenarios
from app.db.database import get_db
from app.db.models import InforceFile, InforceRecord
from app.db.models.user import User
from app.models.schemas import ImportPreviewResponse, InforceRecordListResponse
from app.services import access

router = APIRouter(prefix="/imports", tags=["imports"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
FilterOperator = Literal["eq", "ne", "contains", "starts_with", "gt", "gte", "lt", "lte"]
SortColumn = Literal["policy_id", "created_at"]
SortOrder = Literal["asc", "desc"]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _safe_filename(filename: str | None) -> str:
    """Keep an upload name informational without accepting path components."""
    normalized = (filename or "upload").replace("\\", "/")
    return Path(normalized).name or "upload"


def _save_upload(file: UploadFile) -> Path:
    """Atomically stage an upload, removing partial files when copying fails."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{_safe_filename(file.filename)}"
    dest = UPLOAD_DIR / safe_name
    partial = UPLOAD_DIR / f".{safe_name}.part"
    try:
        with partial.open("wb") as target:
            shutil.copyfileobj(file.file, target)
        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        raise
    return dest


@contextmanager
def _staged_upload(file: UploadFile) -> Iterator[Path]:
    """Yield a temporary upload and guarantee cleanup on every exit path."""
    path = _save_upload(file)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@router.post(
    "/inforce",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("imports:write"))],
)
def upload_inforce(
    user: CurrentUser,
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload an inforce file, validate, and store."""
    access.require_project_access(db, user, project_id, write=True)
    require_active_project(db, project_id)
    try:
        fmt = detect_format(file.filename or "unknown.tsv")
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported file format. Use .tsv, .csv, or .xlsx.")

    validation_errors: list[dict] = []

    def validate(rows: list[dict]) -> list[dict]:
        validation_errors.extend(validate_inforce(rows))
        return validation_errors
    def store_inforce(session: Session, rows: list[dict]) -> int:
        # Stored only when validation found no blocking error (import_file); warnings leave the
        # file in needs_review. The fingerprint uses the same order the run loader reads.
        ordered = sorted(enumerate(rows), key=lambda item: (str(item[1].get("policy_id", f"row_{item[0]}")), item[0]))
        infile = InforceFile(
            project_id=project_id,
            filename=file.filename or "unknown",
            file_type=fmt,
            row_count=len(rows),
            columns_detected=list(rows[0].keys()) if rows else [],
            status=lifecycle.NEEDS_REVIEW if validation_errors else lifecycle.VALIDATED,
            fingerprint=inforce_fingerprint(row for _index, row in ordered),
        )
        session.add(infile)
        session.flush()

        records = [
            InforceRecord(
                file_id=infile.id,
                policy_id=row.get("policy_id", f"row_{i}"),
                data=row,
            )
            for i, row in enumerate(rows)
        ]
        session.add_all(records)
        session.flush()
        return len(records)

    try:
        # Remove the staging file before commit. If cleanup fails, the database
        # work is rolled back instead of returning a misleading success.
        with _staged_upload(file) as file_path:
            result = import_file(str(file_path), db, validate, store_inforce)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@router.post(
    "/assumptions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("imports:write"))],
)
def upload_assumptions(
    user: CurrentUser,
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload an assumption file, validate, and store."""
    access.require_project_access(db, user, project_id, write=True)
    require_active_project(db, project_id)
    try:
        detect_format(file.filename or "unknown.tsv")
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported file format.")

    # For now, validate only — storage will be product-specific (Stages 7-9)
    with _staged_upload(file) as file_path:
        rows = parse_file(str(file_path))
        errors = validate_assumptions(rows)
    return {
        "row_count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "errors": errors,
        "preview": rows[:20],
    }


@router.post(
    "/factors",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("imports:write"))],
)
def upload_factors(
    user: CurrentUser,
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload a factor file, validate, and store."""
    access.require_project_access(db, user, project_id, write=True)
    require_active_project(db, project_id)
    try:
        detect_format(file.filename or "unknown.tsv")
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported file format.")

    with _staged_upload(file) as file_path:
        rows = parse_file(str(file_path))
        errors = validate_factors(rows)
    return {
        "row_count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "errors": errors,
        "preview": rows[:20],
    }


@router.post(
    "/scenarios",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("imports:write"))],
)
def upload_scenarios(
    user: CurrentUser,
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload a scenario file, validate, and store."""
    access.require_project_access(db, user, project_id, write=True)
    require_active_project(db, project_id)
    try:
        detect_format(file.filename or "unknown.tsv")
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported file format.")

    with _staged_upload(file) as file_path:
        rows = parse_file(str(file_path))
        errors = validate_scenarios(rows)
    return {
        "row_count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "errors": errors,
        "preview": rows[:20],
    }


def _file_columns(infile: InforceFile) -> list[str]:
    columns = infile.columns_detected or []
    if isinstance(columns, dict):
        return list(columns)
    return list(columns)


def _filter_records(query, infile: InforceFile, column: str, operator: FilterOperator, value: str):
    allowed_columns = set(_file_columns(infile)) | {"policy_id"}
    if column not in allowed_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported filter column '{column}'.",
        )

    field = InforceRecord.policy_id if column == "policy_id" else InforceRecord.data[column].as_string()
    if operator == "eq":
        return query.filter(field == value)
    if operator == "ne":
        return query.filter(field != value)
    if operator == "contains":
        return query.filter(field.contains(value, autoescape=True))
    if operator == "starts_with":
        return query.filter(field.startswith(value, autoescape=True))

    try:
        number = float(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Filter operator '{operator}' requires a numeric value.",
        ) from exc

    numeric_field = cast(field, Float)
    comparisons = {
        "gt": numeric_field > number,
        "gte": numeric_field >= number,
        "lt": numeric_field < number,
        "lte": numeric_field <= number,
    }
    return query.filter(comparisons[operator])


@router.get("/inforce/{file_id}/records", response_model=InforceRecordListResponse)
def list_inforce_records(
    file_id: str,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    filter_column: str | None = Query(None, min_length=1, max_length=255),
    filter_operator: FilterOperator = Query("eq"),
    filter_value: str | None = Query(None, max_length=500),
    sort_by: SortColumn = Query("policy_id"),
    sort_order: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
):
    """Return a bounded, filterable page of records for one in-force file."""
    access.require_object_access(db, user, "inforce_file", file_id)
    infile = db.get(InforceFile, file_id)
    if infile is None:
        raise HTTPException(status_code=404, detail="In-force file not found.")
    if (filter_column is None) != (filter_value is None):
        raise HTTPException(
            status_code=400,
            detail="filter_column and filter_value must be provided together.",
        )

    query = db.query(InforceRecord).filter(InforceRecord.file_id == file_id)
    if filter_column is not None and filter_value is not None:
        query = _filter_records(query, infile, filter_column, filter_operator, filter_value)

    total = query.order_by(None).count()
    sort_field = getattr(InforceRecord, sort_by)
    order = sort_field.desc() if sort_order == "desc" else sort_field.asc()
    records = (
        query.order_by(order, InforceRecord.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return InforceRecordListResponse(
        file_id=infile.id,
        filename=infile.filename,
        columns=_file_columns(infile),
        records=records,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@router.get("/preview", response_model=ImportPreviewResponse)
def preview_upload(file_path: str = Query(...)):
    """Preview an uploaded file (first 20 rows) without storing anything.

    Only files inside the upload directory can be previewed (no arbitrary server paths).
    """
    candidate = Path(file_path).resolve()
    if not candidate.is_relative_to(UPLOAD_DIR.resolve()) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    data = preview_file(str(candidate), max_rows=20)
    return ImportPreviewResponse(
        columns=data["columns"],
        row_count=data["row_count"],
        sample_rows=data["sample_rows"],
    )
