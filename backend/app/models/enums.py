"""Enums and literal types used across all MentorAmp schemas."""

from typing import Literal

VariableSourceType = Literal[
    "input", "assumption", "factor", "scenario", "formula", "prior_output", "manual",
]

DataType = Literal["number", "string", "boolean", "date", "vector", "table"]

VariableKind = Literal[
    "input", "assumption", "factor", "formula", "output", "lookup",
    "manual", "prior_output", "scenario",
]

FormulaStatus = Literal["draft", "active", "deprecated"]

VariableStatus = Literal["valid", "invalid", "missing_dependency", "circular_reference"]

RunStatus = Literal["pending", "running", "success", "partial_success", "failed"]

ScenarioOperation = Literal["set", "add", "subtract", "multiply", "percent_change"]

CalculationErrorType = Literal[
    "missing_variable", "missing_value", "invalid_formula", "circular_dependency",
    "division_by_zero", "invalid_data_type", "lookup_failed", "scenario_override_failed",
]
