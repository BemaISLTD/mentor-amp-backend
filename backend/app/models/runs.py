"""HTTP contracts for projection run management and analytical results."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.schemas import ProjectionSummary


class RunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_id: str
    formula_database_id: str
    dataset_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(min_length=1)
    projection_length_months: int = Field(ge=1, le=1200)
    selected_output_variables: list[str] = Field(default_factory=list)
    debug_mode: bool = False


class RunResponse(BaseModel):
    id: str
    project_id: str
    projection_key: str | None = None
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunListResponse(BaseModel):
    runs: list[RunResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class RunManifestResponse(BaseModel):
    run_id: str
    fingerprint: str
    manifest: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class RunResultsResponse(BaseModel):
    run_id: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class RunTraceResponse(BaseModel):
    run_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class RunSummaryResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "success", "partial_success", "failed"]
    summary: ProjectionSummary
