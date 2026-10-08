"""Persistent Data Manager lifecycle endpoints."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.db.database import get_db
from app.db.models.user import User
from app.services import access, data_manager_service as service

router = APIRouter(tags=["data manager"])
ImportReader = Annotated[User, Depends(require_permissions("imports:read"))]
ImportWriter = Annotated[User, Depends(require_permissions("imports:write"))]
ImportApprover = Annotated[User, Depends(require_permissions("imports:approve"))]
Category = Literal["liability_inforce", "assumption_table", "factor_table", "scenario"]
DatasetKind = Literal["inforce", "assumption", "factor", "scenario"]


class ImportSessionCreate(BaseModel):
    project_id: str
    category: Category
    name: str = Field(min_length=1, max_length=255)
    version_label: str | None = Field(None, min_length=1, max_length=30)
    replaces_dataset_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class MappingField(BaseModel):
    source_column: str = Field(min_length=1, max_length=255)
    target_field: str = Field(min_length=1, max_length=255)
    transform: Literal["strip", "upper", "lower", "integer", "number", "date_iso"] | None = None


class MappingUpdate(BaseModel):
    profile_id: str | None = None
    fields: list[MappingField] | None = None


class MappingProfileCreate(BaseModel):
    project_id: str
    category: Category
    name: str = Field(min_length=1, max_length=255)
    fields: list[MappingField] = Field(min_length=1)


class ReviewRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/import-sessions", status_code=status.HTTP_201_CREATED)
def create_import_session(payload: ImportSessionCreate, user: ImportWriter,
                          db: Session = Depends(get_db)):
    return service.create_session(db, payload.model_dump(), user)


@router.get("/projects/{project_id}/import-sessions")
def list_import_sessions(project_id: str, user: ImportReader,
                         category: Category | None = Query(None), db: Session = Depends(get_db)):
    access.require_project_access(db, user, project_id)
    return {"import_sessions": service.list_sessions(db, project_id, category)}


@router.get("/import-sessions/{session_id}")
def get_import_session(session_id: str, user: ImportReader, db: Session = Depends(get_db)):
    row = service.require_session_access(db, user, session_id)
    return service.serialize_session(row)


@router.post("/import-sessions/{session_id}/files")
def upload_import_file(session_id: str, user: ImportWriter, file: UploadFile = File(...),
                       db: Session = Depends(get_db)):
    row = service.require_session_access(db, user, session_id, write=True)
    return service.upload_file(db, row, file.filename or "upload.csv", file.file.read(), user)


@router.get("/import-sessions/{session_id}/preview")
def preview_import(session_id: str, user: ImportReader, rows: int = Query(20, ge=1, le=200),
                   db: Session = Depends(get_db)):
    row = service.require_session_access(db, user, session_id)
    return service.preview(row, rows)


@router.get("/import-sessions/{session_id}/file")
def download_import_file(session_id: str, user: ImportReader, db: Session = Depends(get_db)):
    row = service.require_session_access(db, user, session_id)
    content, filename, media_type = service.raw_file(row)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": service.content_disposition(filename)},
    )


@router.put("/import-sessions/{session_id}/mapping")
def map_import(session_id: str, payload: MappingUpdate, user: ImportWriter,
               db: Session = Depends(get_db)):
    row = service.require_session_access(db, user, session_id, write=True)
    return service.set_mapping(db, row, payload.model_dump(exclude_none=True), user)


@router.post("/import-sessions/{session_id}/validate", status_code=status.HTTP_201_CREATED)
def validate_import(session_id: str, user: ImportWriter, db: Session = Depends(get_db)):
    row = service.require_session_access(db, user, session_id, write=True)
    return service.validate_session(db, row, user)


@router.post("/import-sessions/{session_id}/commit", status_code=status.HTTP_201_CREATED)
def commit_import(session_id: str, user: ImportWriter, db: Session = Depends(get_db)):
    row = service.require_session_access(db, user, session_id, write=True)
    return service.commit_session(db, row, user)


@router.get("/import-sessions/{session_id}/log")
def get_import_log(session_id: str, user: ImportReader, db: Session = Depends(get_db)):
    service.require_session_access(db, user, session_id)
    return {"events": service.import_log(db, session_id)}


@router.get("/mapping-profiles")
def list_mapping_profiles(user: ImportReader, project_id: str = Query(...),
                          category: Category | None = Query(None), db: Session = Depends(get_db)):
    access.require_project_access(db, user, project_id)
    return {"mapping_profiles": service.list_mapping_profiles(db, project_id, category)}


@router.post("/mapping-profiles", status_code=status.HTTP_201_CREATED)
def create_mapping_profile(payload: MappingProfileCreate, user: ImportWriter,
                           db: Session = Depends(get_db)):
    data = payload.model_dump()
    data["fields"] = [field.model_dump(exclude_none=True) for field in payload.fields]
    return service.create_mapping_profile(db, data, user)


@router.get("/validation-runs/{validation_id}/issues")
def get_validation_issues(validation_id: str, user: ImportReader, db: Session = Depends(get_db)):
    validation = service.require_validation_access(db, user, validation_id)
    return {"validation": service.serialize_validation(validation),
            "issues": service.validation_issues(db, validation_id)}


@router.get("/validation-runs/{validation_id}/rejected-records")
def get_rejected_records(validation_id: str, user: ImportReader, db: Session = Depends(get_db)):
    service.require_validation_access(db, user, validation_id)
    return {"rejected_records": service.rejected_records(db, validation_id)}


def _review(db: Session, kind: DatasetKind, dataset_id: str, payload: ReviewRequest,
            user: User, approve: bool):
    row = service._dataset(db, kind, dataset_id)  # authorization is deliberately project-derived
    access.require_project_access(db, user, service._dataset_project(db, kind, row))
    return service.review_dataset(db, kind, dataset_id, approve, payload.reason, user)


@router.post("/datasets/{kind}/{dataset_id}/approve")
def approve_dataset(kind: DatasetKind, dataset_id: str, payload: ReviewRequest,
                    user: ImportApprover, db: Session = Depends(get_db)):
    return _review(db, kind, dataset_id, payload, user, True)


@router.post("/datasets/{kind}/{dataset_id}/reject")
def reject_dataset(kind: DatasetKind, dataset_id: str, payload: ReviewRequest,
                   user: ImportApprover, db: Session = Depends(get_db)):
    return _review(db, kind, dataset_id, payload, user, False)


@router.get("/datasets/{kind}/{dataset_id}/versions")
def dataset_versions(kind: DatasetKind, dataset_id: str, user: ImportReader,
                     db: Session = Depends(get_db)):
    row = service._dataset(db, kind, dataset_id)
    access.require_project_access(db, user, service._dataset_project(db, kind, row))
    return {"versions": service.dataset_versions(db, kind, dataset_id)}


@router.get("/datasets/{kind}/{dataset_id}/compare")
def compare_datasets(kind: DatasetKind, dataset_id: str, user: ImportReader,
                     other_id: str = Query(...), limit: int = Query(50, ge=1, le=200),
                     db: Session = Depends(get_db)):
    row = service._dataset(db, kind, dataset_id)
    access.require_project_access(db, user, service._dataset_project(db, kind, row))
    return service.compare_datasets(db, kind, dataset_id, other_id, limit)
