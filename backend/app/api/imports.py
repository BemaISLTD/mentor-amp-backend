"""API endpoints for file import and preview."""

import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.config import settings
from app.data.importers.base import detect_format, import_file, preview_file
from app.data.validation.inforce_validator import validate_inforce
from app.data.validation.assumption_validator import validate_assumptions
from app.data.validation.factor_validator import validate_factors
from app.data.validation.scenario_validator import validate_scenarios
from app.db.database import get_db
from app.db.models import InforceFile, InforceRecord
from app.models.schemas import ImportPreviewResponse

router = APIRouter(prefix="/imports", tags=["imports"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


def _save_upload(file: UploadFile) -> str:
    """Save an uploaded file to disk and return the file path."""
    UPLOAD_DIR.mkdir(exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    dest = UPLOAD_DIR / safe_name
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return str(dest)


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

    file_path = _save_upload(file)

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
        session.commit()
        return len(records)

    result = import_file(file_path, db, validate_inforce, store_inforce)
    return result


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

    file_path = _save_upload(file)

    # For now, validate only — storage will be product-specific (Stages 7-9)
    rows = __import__("app.data.importers.base", fromlist=["parse_file"]).parse_file(file_path)
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

    file_path = _save_upload(file)

    from app.data.importers.base import parse_file as pf

    rows = pf(file_path)
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

    file_path = _save_upload(file)

    from app.data.importers.base import parse_file as pf

    rows = pf(file_path)
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
