"""Pydantic data contracts — the shape of every object in the MentorAmp engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# =============================================================================
# VARIABLE SOURCES
# =============================================================================

class InputSource(BaseModel):
    type: Literal["input"] = "input"
    dataset_id: str = ""
    column_name: str = ""


class AssumptionSource(BaseModel):
    type: Literal["assumption"] = "assumption"
    table_id: str
    key: Optional[str] = None


class FactorSource(BaseModel):
    type: Literal["factor"] = "factor"
    table_id: str = ""
    lookup_keys: list[str] = Field(default_factory=list)


class ScenarioSource(BaseModel):
    type: Literal["scenario"] = "scenario"
    scenario_id: str = ""
    override_id: str = ""


class FormulaSource(BaseModel):
    type: Literal["formula"] = "formula"
    formula_id: str = ""


class PriorOutputSource(BaseModel):
    type: Literal["prior_output"] = "prior_output"
    variable_name: str = ""
    offset_periods: int = 1


class ManualSource(BaseModel):
    type: Literal["manual"] = "manual"
    value: Any = None


VariableSource = Union[
    InputSource, AssumptionSource, FactorSource, ScenarioSource,
    FormulaSource, PriorOutputSource, ManualSource, dict,
]


# =============================================================================
# CORE REGISTRY OBJECTS
# =============================================================================

class VariableDefinition(BaseModel):
    id: str
    name: str
    label: Optional[str] = None
    kind: Literal["input", "assumption", "factor", "formula", "output", "lookup", "manual", "prior_output", "scenario"] = "input"
    data_type: Literal["number", "string", "boolean", "date", "vector", "table"] = "number"
    source: Optional[dict] = None
    dependencies: list[str] = Field(default_factory=list)
    required: bool = True
    default_value: Optional[Any] = None
    product_applicability: list[str] = Field(default_factory=list)
    basis_applicability: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    status: Literal["valid", "invalid", "missing_dependency", "circular_reference"] = "valid"


class FormulaDefinition(BaseModel):
    id: str
    name: str
    output_variable: str
    function_ref: str
    dependencies: list[str] = Field(default_factory=list)
    category: str = ""
    product_applicability: list[str] = Field(default_factory=list)
    basis_applicability: list[str] = Field(default_factory=list)
    version: str = "v1"
    status: Literal["draft", "active", "deprecated"] = "draft"
    test_case_ids: list[str] = Field(default_factory=list)


class FormulaDatabase(BaseModel):
    id: str
    name: str
    variables: list[VariableDefinition] = Field(default_factory=list)
    formulas: list[FormulaDefinition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScenarioOverride(BaseModel):
    id: str = ""
    target_variable: str
    operation: Literal["set", "add", "subtract", "multiply", "percent_change"]
    value: float | str | bool
    applies_from_period: Optional[int] = None
    applies_to_period: Optional[int] = None


class ScenarioDefinition(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    overrides: list[ScenarioOverride] = Field(default_factory=list)


class ProjectionRunDefinition(BaseModel):
    id: str
    name: str
    project_id: str
    formula_database_id: str
    dataset_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    projection_length_months: int
    selected_output_variables: list[str] = Field(default_factory=list)
    debug_mode: bool = False


class ProjectionContext(BaseModel):
    project_id: str = ""
    run_id: str = ""
    product: str = ""
    basis: Optional[str] = None
    methodology: Optional[str] = None
    policy_id: str = ""
    scenario_id: str = ""
    projection_month: int = 0
    duration: Optional[int] = None
    attained_age: Optional[int] = None
    valuation_date: Optional[str] = None
    assumption_set: Optional[str] = None
    factor_set: Optional[str] = None
    scenario_set: Optional[str] = None
    projection_key: Optional[str] = None


class CalculationError(BaseModel):
    type: Literal["missing_variable", "missing_value", "invalid_formula", "circular_dependency",
                  "division_by_zero", "invalid_data_type", "lookup_failed", "scenario_override_failed"]
    message: str
    variable_id: Optional[str] = None
    formula_id: Optional[str] = None
    policy_id: Optional[str] = None
    period: Optional[int] = None
    scenario_id: Optional[str] = None
    dependency_path: list[str] = Field(default_factory=list)


class CalculationResult(BaseModel):
    run_id: str
    variable_id: str
    policy_id: Optional[str] = None
    period: Optional[int] = None
    scenario_id: Optional[str] = None
    value: Any = None
    status: Literal["success", "error", "skipped"]
    error: Optional[CalculationError] = None


class VariableResolutionResult(BaseModel):
    variable_name: str
    value: Any = None
    source_type: str = ""
    source_table: Optional[str] = None
    lookup_keys: dict[str, Any] = Field(default_factory=dict)
    was_defaulted: bool = False
    error_message: Optional[str] = None


class CalculationTrace(BaseModel):
    variable_name: str
    formula_id: Optional[str] = None
    value: Optional[Any] = None
    source: Optional[VariableResolutionResult] = None
    dependencies: list[CalculationTrace] = Field(default_factory=list)
    period: Optional[int] = None
    scenario_id: Optional[str] = None


class ProjectionSummary(BaseModel):
    policy_count: int = 0
    period_count: int = 0
    scenario_count: int = 0
    calculated_variable_count: int = 0
    error_count: int = 0
    warning_count: int = 0


class ProjectionResultSet(BaseModel):
    run_id: str
    scenario_id: str
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    status: Literal["success", "partial_success", "failed"] = "success"
    results: list[CalculationResult] = Field(default_factory=list)
    errors: list[CalculationError] = Field(default_factory=list)
    traces: list[CalculationTrace] = Field(default_factory=list)
    summary: ProjectionSummary = Field(default_factory=ProjectionSummary)


class DependencyError(BaseModel):
    type: Literal["circular_dependency", "missing_dependency"]
    variable_id: str
    message: str
    path: list[str] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    kind: Literal["input", "assumption", "factor", "formula", "output"]
    status: Literal["valid", "error", "warning"] = "valid"


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: Optional[str] = None


class DependencyGraphView(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# =============================================================================
# API SCHEMAS
# =============================================================================

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse] = Field(default_factory=list)
    total: int = 0


class ImportPreviewResponse(BaseModel):
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)


class RunStatusResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "success", "partial_success", "failed"]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    summary: Optional[ProjectionSummary] = None
