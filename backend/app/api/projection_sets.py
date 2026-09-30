"""Projection Sets: list, create, edit, validate, duplicate, attach scenario (contract §E.6)."""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path
from app.db.database import get_db
from app.db.models.user import User
from app.services import projection_set_service

router = APIRouter(tags=["projection-sets"])
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
ProjectWriter = Annotated[
    User, Depends(authorize_path("project", "project_id", "registries:write", write=True))
]
SetReader = Annotated[
    User, Depends(authorize_path("projection_set", "projection_set_id", "projects:read"))
]
SetWriter = Annotated[
    User, Depends(authorize_path("projection_set", "projection_set_id", "registries:write", write=True))
]


class TraceScope(BaseModel):
    mode: Literal["none", "selected_policies", "all"] = "none"
    policy_ids: list[str] = Field(default_factory=list)


class ProjectionSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    version_label: str | None = Field(None, max_length=30)
    model_version_id: str
    inforce_file_ids: list[str] = Field(default_factory=list)
    assumption_table_ids: list[str] = Field(default_factory=list)
    factor_table_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    valuation_date: date
    horizon_months: int = Field(ge=1, le=1200)
    time_step: Literal["monthly"] = "monthly"
    output_variables: list[str] = Field(default_factory=list)
    trace_scope: TraceScope = Field(default_factory=TraceScope)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProjectionSetUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    model_version_id: str | None = None
    inforce_file_ids: list[str] | None = None
    assumption_table_ids: list[str] | None = None
    factor_table_ids: list[str] | None = None
    scenario_ids: list[str] | None = None
    valuation_date: date | None = None
    horizon_months: int | None = Field(None, ge=1, le=1200)
    time_step: Literal["monthly"] | None = None
    output_variables: list[str] | None = None
    trace_scope: TraceScope | None = None
    parameters: dict[str, Any] | None = None


class DuplicateRequest(BaseModel):
    version_label: str | None = Field(None, max_length=30)


class AttachScenario(BaseModel):
    scenario_id: str


@router.get("/projects/{project_id}/projection-sets")
def list_projection_sets(
    project_id: str,
    user: ProjectReader,
    search: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
):
    del user
    return projection_set_service.list_projection_sets(db, project_id, search=search, status=status_filter)


@router.post("/projects/{project_id}/projection-sets", status_code=status.HTTP_201_CREATED)
def create_projection_set(
    project_id: str, payload: ProjectionSetCreate, user: ProjectWriter, db: Session = Depends(get_db)
):
    return projection_set_service.create(db, project_id, payload.model_dump(), user)


@router.get("/projection-sets/{projection_set_id}")
def get_projection_set(projection_set_id: str, user: SetReader, db: Session = Depends(get_db)):
    del user
    return projection_set_service.serialize(db, projection_set_service.get(db, projection_set_id))


@router.patch("/projection-sets/{projection_set_id}")
def update_projection_set(
    projection_set_id: str, payload: ProjectionSetUpdate, user: SetWriter, db: Session = Depends(get_db)
):
    del user
    return projection_set_service.update(db, projection_set_id, payload.model_dump(exclude_unset=True))


@router.post("/projection-sets/{projection_set_id}/validate")
def validate_projection_set(projection_set_id: str, user: SetWriter, db: Session = Depends(get_db)):
    del user
    return projection_set_service.validate(db, projection_set_id)


@router.post("/projection-sets/{projection_set_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_projection_set(
    projection_set_id: str,
    user: SetWriter,
    payload: DuplicateRequest | None = None,
    db: Session = Depends(get_db),
):
    return projection_set_service.duplicate(
        db, projection_set_id, payload.version_label if payload else None, user
    )


@router.post("/projection-sets/{projection_set_id}/scenarios")
def attach_scenario(
    projection_set_id: str, payload: AttachScenario, user: SetWriter, db: Session = Depends(get_db)
):
    del user
    return projection_set_service.attach_scenario(db, projection_set_id, payload.scenario_id)
