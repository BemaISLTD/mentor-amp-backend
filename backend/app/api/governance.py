"""Governed rollforward, reporting, derived-dataset, and run-step APIs."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path
from app.db.database import get_db
from app.db.models.governance import DerivedDataset, Report
from app.db.models.user import User
from app.services import governance_service

router = APIRouter(tags=["actuarial governance"])
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
ProjectWriter = Annotated[
    User, Depends(authorize_path("project", "project_id", "registries:write", write=True))
]
RunReader = Annotated[User, Depends(authorize_path("run", "run_id", "runs:read"))]


class RollforwardStepDefinition(BaseModel):
    step_key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    step_type: str = Field(min_length=1, max_length=50)
    sequence: int | None = Field(None, ge=1)
    configuration: dict[str, Any] = Field(default_factory=dict)


class RollforwardTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    model_version_id: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    steps: list[RollforwardStepDefinition] = Field(default_factory=list)


class RollforwardTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    configuration: dict[str, Any] | None = None
    steps: list[RollforwardStepDefinition] | None = None


class RollforwardJobCreate(BaseModel):
    template_id: str
    name: str = Field(min_length=1, max_length=255)
    from_date: date
    to_date: date
    parameters: dict[str, Any] = Field(default_factory=dict)


class RollforwardJobUpdate(BaseModel):
    status: str | None = None
    run_set_id: str | None = None


class ReportCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    report_type: str = Field(min_length=1, max_length=100)
    description: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)


class ReportUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    report_type: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    definition: dict[str, Any] | None = None
    artifact_uri: str | None = None
    artifact_fingerprint: str | None = Field(None, max_length=64)


class DerivedDatasetCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(min_length=1, max_length=255)
    dataset_type: str = Field(min_length=1, max_length=100)
    description: str | None = None
    source_run_ids: list[str] = Field(default_factory=list)
    dataset_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    storage_uri: str | None = None
    storage_backend: str | None = Field(None, max_length=50)
    file_format: str | None = Field(None, max_length=30)
    row_count: int | None = Field(None, ge=0)
    fingerprint: str | None = Field(None, max_length=64)


class DerivedDatasetUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str | None = Field(None, min_length=1, max_length=255)
    dataset_type: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    source_run_ids: list[str] | None = None
    dataset_schema: dict[str, Any] | None = Field(None, alias="schema")
    storage_uri: str | None = None
    storage_backend: str | None = Field(None, max_length=50)
    file_format: str | None = Field(None, max_length=30)
    row_count: int | None = Field(None, ge=0)
    fingerprint: str | None = Field(None, max_length=64)


@router.get("/projects/{project_id}/rollforward-templates")
def list_templates(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    return {"templates": governance_service.list_templates(db, project_id)}


@router.post("/projects/{project_id}/rollforward-templates", status_code=status.HTTP_201_CREATED)
def create_template(project_id: str, payload: RollforwardTemplateCreate, user: ProjectWriter,
                    db: Session = Depends(get_db)):
    return governance_service.create_template(db, project_id, payload.model_dump(), user)


@router.get("/rollforward-templates/{template_id}")
def get_template(template_id: str, user: Annotated[User, Depends(authorize_path(
    "rollforward_template", "template_id", "projects:read"
))], db: Session = Depends(get_db)):
    del user
    return governance_service.template_detail(db, template_id)


@router.patch("/rollforward-templates/{template_id}")
def update_template(template_id: str, payload: RollforwardTemplateUpdate,
                    user: Annotated[User, Depends(authorize_path(
                        "rollforward_template", "template_id", "registries:write", write=True
                    ))], db: Session = Depends(get_db)):
    return governance_service.update_template(
        db, template_id, payload.model_dump(exclude_unset=True), user
    )


@router.post("/rollforward-templates/{template_id}/versions", status_code=status.HTTP_201_CREATED)
def version_template(template_id: str, user: Annotated[User, Depends(authorize_path(
    "rollforward_template", "template_id", "registries:write", write=True
))], db: Session = Depends(get_db)):
    return governance_service.version_template(db, template_id, user)


@router.post("/rollforward-templates/{template_id}/publish")
def publish_template(template_id: str, user: Annotated[User, Depends(authorize_path(
    "rollforward_template", "template_id", "registries:write", write=True
))], db: Session = Depends(get_db)):
    return governance_service.publish_template(db, template_id, user)


@router.delete("/rollforward-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_template(template_id: str, user: Annotated[User, Depends(authorize_path(
    "rollforward_template", "template_id", "registries:write", write=True
))], db: Session = Depends(get_db)) -> Response:
    governance_service.archive_template(db, template_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/rollforward-jobs")
def list_jobs(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    return {"jobs": governance_service.list_jobs(db, project_id)}


@router.post("/projects/{project_id}/rollforward-jobs", status_code=status.HTTP_201_CREATED)
def create_job(project_id: str, payload: RollforwardJobCreate, user: ProjectWriter,
               db: Session = Depends(get_db)):
    return governance_service.create_job(db, project_id, payload.model_dump(), user)


@router.get("/rollforward-jobs/{job_id}")
def get_job(job_id: str, user: Annotated[User, Depends(authorize_path(
    "rollforward_job", "job_id", "projects:read"
))], db: Session = Depends(get_db)):
    del user
    return governance_service.job_detail(db, job_id)


@router.patch("/rollforward-jobs/{job_id}")
def update_job(job_id: str, payload: RollforwardJobUpdate,
               user: Annotated[User, Depends(authorize_path(
                   "rollforward_job", "job_id", "registries:write", write=True
               ))], db: Session = Depends(get_db)):
    return governance_service.update_job(db, job_id, payload.model_dump(exclude_unset=True), user)


@router.get("/projects/{project_id}/reports")
def list_reports(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    return {"reports": governance_service.list_versioned(db, Report, project_id)}


@router.post("/projects/{project_id}/reports", status_code=status.HTTP_201_CREATED)
def create_report(project_id: str, payload: ReportCreate, user: ProjectWriter,
                  db: Session = Depends(get_db)):
    return governance_service.create_report(db, project_id, payload.model_dump(), user)


@router.get("/reports/{report_id}")
def get_report(report_id: str, user: Annotated[User, Depends(authorize_path(
    "report", "report_id", "projects:read"
))], db: Session = Depends(get_db)):
    del user
    return governance_service.versioned_detail(db, Report, report_id)


@router.patch("/reports/{report_id}")
def update_report(report_id: str, payload: ReportUpdate, user: Annotated[User, Depends(authorize_path(
    "report", "report_id", "registries:write", write=True
))], db: Session = Depends(get_db)):
    return governance_service.update_versioned(
        db, Report, report_id, payload.model_dump(exclude_unset=True), user
    )


@router.post("/reports/{report_id}/versions", status_code=status.HTTP_201_CREATED)
def version_report(report_id: str, user: Annotated[User, Depends(authorize_path(
    "report", "report_id", "registries:write", write=True
))], db: Session = Depends(get_db)):
    return governance_service.version_versioned(db, Report, report_id, user)


@router.post("/reports/{report_id}/publish")
def publish_report(report_id: str, user: Annotated[User, Depends(authorize_path(
    "report", "report_id", "registries:write", write=True
))], db: Session = Depends(get_db)):
    return governance_service.publish_versioned(db, Report, report_id, user)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_report(report_id: str, user: Annotated[User, Depends(authorize_path(
    "report", "report_id", "registries:write", write=True
))], db: Session = Depends(get_db)) -> Response:
    governance_service.archive_versioned(db, Report, report_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/derived-datasets")
def list_datasets(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
    del user
    return {"derived_datasets": governance_service.list_versioned(db, DerivedDataset, project_id)}


@router.post("/projects/{project_id}/derived-datasets", status_code=status.HTTP_201_CREATED)
def create_dataset(project_id: str, payload: DerivedDatasetCreate, user: ProjectWriter,
                   db: Session = Depends(get_db)):
    return governance_service.create_dataset(db, project_id, payload.model_dump(by_alias=True), user)


@router.get("/derived-datasets/{dataset_id}")
def get_dataset(dataset_id: str, user: Annotated[User, Depends(authorize_path(
    "derived_dataset", "dataset_id", "projects:read"
))], db: Session = Depends(get_db)):
    del user
    return governance_service.versioned_detail(db, DerivedDataset, dataset_id)


@router.patch("/derived-datasets/{dataset_id}")
def update_dataset(dataset_id: str, payload: DerivedDatasetUpdate,
                   user: Annotated[User, Depends(authorize_path(
                       "derived_dataset", "dataset_id", "registries:write", write=True
                   ))], db: Session = Depends(get_db)):
    return governance_service.update_versioned(
        db, DerivedDataset, dataset_id, payload.model_dump(exclude_unset=True, by_alias=True), user
    )


@router.post("/derived-datasets/{dataset_id}/versions", status_code=status.HTTP_201_CREATED)
def version_dataset(dataset_id: str, user: Annotated[User, Depends(authorize_path(
    "derived_dataset", "dataset_id", "registries:write", write=True
))], db: Session = Depends(get_db)):
    return governance_service.version_versioned(db, DerivedDataset, dataset_id, user)


@router.post("/derived-datasets/{dataset_id}/publish")
def publish_dataset(dataset_id: str, user: Annotated[User, Depends(authorize_path(
    "derived_dataset", "dataset_id", "registries:write", write=True
))], db: Session = Depends(get_db)):
    return governance_service.publish_versioned(db, DerivedDataset, dataset_id, user)


@router.delete("/derived-datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_dataset(dataset_id: str, user: Annotated[User, Depends(authorize_path(
    "derived_dataset", "dataset_id", "registries:write", write=True
))], db: Session = Depends(get_db)) -> Response:
    governance_service.archive_versioned(db, DerivedDataset, dataset_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runs/{run_id}/steps")
def get_run_steps(run_id: str, user: RunReader, db: Session = Depends(get_db)):
    del user
    return {"steps": governance_service.run_steps(db, run_id)}
