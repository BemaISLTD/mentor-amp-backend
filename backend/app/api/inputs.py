"""Input Library, dataset details, mappings and validation issues (contract §E.3)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path
from app.db.database import get_db
from app.db.models.user import User
from app.services import catalog_service

router = APIRouter(tags=["inputs"])
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
InforceReader = Annotated[User, Depends(authorize_path("inforce_file", "file_id", "projects:read"))]
AssumptionReader = Annotated[
    User, Depends(authorize_path("assumption_table", "table_id", "projects:read"))
]
FactorReader = Annotated[User, Depends(authorize_path("factor_table", "table_id", "projects:read"))]


@router.get("/projects/{project_id}/inputs")
def list_inputs(
    project_id: str,
    user: ProjectReader,
    category: str | None = Query(None),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    del user
    return catalog_service.list_inputs(db, project_id, category=category, search=search)


@router.get("/projects/{project_id}/input-mappings")
def input_mappings(
    project_id: str,
    user: ProjectReader,
    inforce_file_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    del user
    # The service filters by project_id, so a file ID from another project matches nothing.
    return catalog_service.input_mappings(db, project_id, inforce_file_id)


@router.get("/projects/{project_id}/validation-issues")
def validation_issues(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    return catalog_service.validation_issues(db, project_id)


@router.get("/inputs/inforce/{file_id}")
def inforce_detail(file_id: str, user: InforceReader, db: Session = Depends(get_db)):
    del user
    return catalog_service.inforce_detail(db, file_id)


@router.get("/inputs/inforce/{file_id}/records")
def inforce_records(
    file_id: str,
    user: InforceReader,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    del user
    return catalog_service.inforce_records(db, file_id, limit, offset)


@router.get("/inputs/assumption-tables/{table_id}")
def assumption_table(
    table_id: str,
    user: AssumptionReader,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    del user
    return catalog_service.table_detail(db, table_id, "assumption", limit, offset)


@router.get("/inputs/factor-tables/{table_id}")
def factor_table(
    table_id: str,
    user: FactorReader,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    del user
    return catalog_service.table_detail(db, table_id, "factor", limit, offset)
