"""API endpoints for file import and preview."""

import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.data.importers.base import detect_format, import_file, parse_file, preview_file
from app.data.validation.inforce_validator import validate_inforce
from app.data.validation.assumption_validator import validate_assumptions
from app.data.validation.factor_validator import validate_factors
from app.data.validation.scenario_validator import validate_scenarios
from app.db.database import get_db
from app.db.models import InforceFile, InforceRecord
from app.models.schemas import ImportPreviewResponse

router = APIRouter(prefix="/imports", tags=["imports"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


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


@router.post("/inforce", status_code=status.HTTP_201_CREATED)
def upload_inforce(
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload an inforce file, validate, and store."""
    try:
        fmt = detect_format(file.filename or "unknown.tsv")
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported file format. Use .tsv, .csv, or .xlsx.")

    def store_inforce(session: Session, rows: list[dict]) -> int:
        infile = InforceFile(
            project_id=project_id,
            filename=file.filename or "unknown",
            file_type=fmt,
            row_count=len(rows),
            columns_detected=list(rows[0].keys()) if rows else [],
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
            result = import_file(str(file_path), db, validate_inforce, store_inforce)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@router.post("/assumptions", status_code=status.HTTP_201_CREATED)
def upload_assumptions(
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload an assumption file, validate, and store."""
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


@router.post("/factors", status_code=status.HTTP_201_CREATED)
def upload_factors(
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload a factor file, validate, and store."""
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


@router.post("/scenarios", status_code=status.HTTP_201_CREATED)
def upload_scenarios(
    file: UploadFile = File(...),
    project_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Upload a scenario file, validate, and store."""
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


@router.get("/preview", response_model=ImportPreviewResponse)
def preview_upload(file_path: str = Query(...)):
    """Preview an uploaded file (first 20 rows) without storing."""
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found.")

    data = preview_file(file_path, max_rows=20)
    return ImportPreviewResponse(
        columns=data["columns"],
        row_count=data["row_count"],
        sample_rows=data["sample_rows"],
    )
