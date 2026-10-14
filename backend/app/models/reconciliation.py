"""Response contracts for prior-versus-current run reconciliation."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReconciliationDetail(BaseModel):
    policy_id: str
    scenario_id: str
    month: int
    variable: str
    baseline_value: Any = None
    current_value: Any = None
    variance: float | None = None
    variance_pct: float | None = None
    change_type: Literal["added", "removed", "changed", "unchanged"]


class ReconciliationBridge(BaseModel):
    variable: str
    baseline_total: float
    current_total: float
    variance: float
    variance_pct: float | None = None
    matched_points: int
    added_points: int
    removed_points: int
    non_numeric_points: int


class ReconciliationSummary(BaseModel):
    compared_points: int
    changed_points: int
    unchanged_points: int
    added_points: int
    removed_points: int


class ReconciliationResponse(BaseModel):
    baseline_run_id: str
    current_run_id: str
    summary: ReconciliationSummary
    bridges: list[ReconciliationBridge] = Field(default_factory=list)
    details: list[ReconciliationDetail] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
