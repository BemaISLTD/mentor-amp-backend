"""Freezing run packages: resolve, validate and snapshot every input of a run at submission.

``freeze`` is the gate between editable configuration and execution. It re-checks everything
the run will use — even if the Projection Set was validated earlier — because the Projection
Set, its inputs or a request-level scenario may have changed since:

- the Projection Set, model, model version, inputs and scenario belong to the run's project;
- the model version, formulas, inputs and scenario have a runnable lifecycle status;
- every formula function is registered and the formula graph has no cycles;
- every variable the formulas and outputs need is registered;
- every assumption/factor table a variable reads is pinned by ID, exactly once per name;
- every pinned dataset is fingerprinted, and the scenario still matches its fingerprint;
- scenario overrides target variables this model actually uses, with valid operations/values.

The shared check functions are also used by Projection Set validation, so both apply the same
rules. See ``app.core.execution.run_package`` for the document layout.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.core import lifecycle
from app.core.execution import run_package
from app.core.execution.fingerprints import INFORCE_FINGERPRINT_SCHEME, scenario_fingerprint
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS, FORMULA_REGISTRY
from app.core.projection_engine.engine import OVERRIDE_OPERATIONS, EngineError, order_formulas
from app.core.projection_engine.run_data import VariableSpec
from app.db.models.assumption import AssumptionSet, AssumptionTable
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile
from app.db.models.modeling import Model, ModelPublishedOutput, ModelVersion
from app.db.models.project import Project
from app.db.models.projection import ProjectionSet
from app.db.models.scenario import ScenarioSet, ScenarioTable
from app.db.models.model_variable import ModelVariableDefinition
from app.products.registry import register_all_products
from app.services.build_info import BuildIdentityUnavailable, build_identity
from app.services.common import ServiceError, iso, now_utc
from app.services.model_definition import (
    load_model_formulas,
    to_variable_spec,
    variable_closure_rows,
)

TABLE_KINDS = ("assumption", "factor")
TRACE_MODES = ("none", "selected_policies", "all")
CALCULATED_KINDS = ("formula", "valuation")
MAX_PERIOD = 1200  # the longest horizon a Projection Set allows


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}


class PackageInvalid(ServiceError):
    def __init__(self, problems: list[Problem], **context: Any):
        super().__init__(
            422, "RUN_PACKAGE_INVALID",
            "The run cannot be submitted: " + "; ".join(problem.message for problem in problems[:5]),
            {"problems": [problem.as_dict() for problem in problems], **context},
        )
        self.problems = problems


@dataclass
class FrozenPackage:
    document: dict[str, Any]
    fingerprint: str
    configuration: dict[str, Any]


def normalise_trace_scope(scope: dict | None) -> dict[str, Any]:
    scope = dict(scope or {})
    return {"mode": scope.get("mode", "none"), "policy_ids": list(scope.get("policy_ids") or [])}


# =============================================================================
# Shared checks (also used by Projection Set validation)
# =============================================================================

@dataclass
class ModelResolution:
    version: ModelVersion | None = None
    model: Model | None = None
    formulas: list[FormulaRegistry] = field(default_factory=list)
    variable_rows: dict[str, ModelVariableDefinition] = field(default_factory=dict)
    variables: dict[str, VariableSpec] = field(default_factory=dict)
    published: dict[str, ModelPublishedOutput] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)


def check_model(db: Session, project_id: str, projection_set: ProjectionSet) -> ModelResolution:
    register_all_products()
    result = ModelResolution()
    if not projection_set.model_version_id:
        result.problems.append(Problem("MODEL_VERSION_NOT_SELECTED", "No model version is selected."))
        return result
    version = db.get(ModelVersion, projection_set.model_version_id)
    model = db.get(Model, version.model_id) if version else None
    if version is None or model is None or model.project_id != project_id:
        result.problems.append(Problem(
            "MODEL_VERSION_NOT_FOUND",
            f"Model version '{projection_set.model_version_id}' was not found in this project.",
        ))
        return result
    result.version, result.model = version, model
    if version.status not in lifecycle.RUNNABLE_MODEL_STATUSES:
        result.problems.append(Problem(
            "MODEL_VERSION_NOT_RUNNABLE",
            f"Model version {version.version_label} is '{version.status}', not validated.",
        ))
    result.formulas = load_model_formulas(db, version.id)
    if not result.formulas:
        result.problems.append(Problem("NO_FORMULAS", "The model version has no formulas."))
    for formula in result.formulas:
        if formula.function_ref not in FORMULA_FUNCTIONS:
            result.problems.append(Problem(
                "FUNCTION_NOT_REGISTERED",
                f"Formula '{formula.name}' uses unregistered function '{formula.function_ref}'.",
            ))
        if formula.status not in lifecycle.RUNNABLE_FORMULA_STATUSES:
            result.problems.append(Problem(
                "FORMULA_NOT_RUNNABLE",
                f"Formula '{formula.name}' is '{formula.status}', not validated.",
            ))
    rows, missing = variable_closure_rows(
        db, version.id, result.formulas, list(projection_set.output_variables or []),
    )
    for name in sorted(missing):
        result.problems.append(Problem(
            "VARIABLE_NOT_DEFINED",
            f"Variable '{name}' is used but not defined for model version {version.version_label}.",
        ))
    result.variable_rows = rows
    result.variables = {name: to_variable_spec(row) for name, row in rows.items()}
    try:
        order_formulas([
            run_package.formula_spec(_formula_entry(formula)) for formula in result.formulas
        ])
    except EngineError as error:
        result.problems.append(Problem("FORMULA_GRAPH_INVALID", error.message))
    result.published = {
        row.variable_name: row
        for row in db.query(ModelPublishedOutput)
        .filter(ModelPublishedOutput.model_version_id == version.id)
        .all()
    }
    return result


def check_outputs(projection_set: ProjectionSet, published: dict[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    outputs = list(projection_set.output_variables or [])
    if not outputs:
        problems.append(Problem("OUTPUTS_NOT_SELECTED", "No output variables are selected."))
    for name in outputs:
        if name not in published:
            problems.append(Problem("OUTPUT_NOT_PUBLISHED", f"'{name}' is not a published output."))
    if not 1 <= (projection_set.horizon_months or 0) <= 1200:
        problems.append(Problem("HORIZON_INVALID", "The horizon must be 1 to 1200 months."))
    if projection_set.time_step != "monthly":
        problems.append(Problem("TIME_STEP_INVALID", "Only monthly time steps are supported."))
    scope = normalise_trace_scope(projection_set.trace_scope)
    if scope["mode"] not in TRACE_MODES:
        problems.append(Problem("TRACE_SCOPE_INVALID", f"Unknown trace mode '{scope['mode']}'."))
    if len(scope["policy_ids"]) > settings.max_traced_policies:
        problems.append(Problem(
            "TRACE_SCOPE_TOO_LARGE",
            f"At most {settings.max_traced_policies} policies can be traced per run.",
        ))
    return problems


def _input_problems(label: str, object_id: str, status: str | None, fingerprint: str | None) -> list[Problem]:
    problems: list[Problem] = []
    if status not in lifecycle.RUNNABLE_INPUT_STATUSES:
        problems.append(Problem(
            "INPUT_NOT_RUNNABLE", f"{label} is '{status}', not validated.", {"id": object_id},
        ))
    if not fingerprint:
        problems.append(Problem("INPUT_NOT_FINGERPRINTED", f"{label} has no fingerprint.", {"id": object_id}))
    return problems


def check_inforce(db: Session, project_id: str, file_ids: list[str]) -> tuple[list[InforceFile], list[Problem]]:
    problems: list[Problem] = []
    if not file_ids:
        return [], [Problem("INFORCE_NOT_SELECTED", "No inforce file is selected.")]
    if len(set(file_ids)) != len(file_ids):
        problems.append(Problem("INPUT_DUPLICATED", "An inforce file is selected more than once."))
    files: list[InforceFile] = []
    for file_id in dict.fromkeys(file_ids):
        file = db.get(InforceFile, file_id)
        if file is None or file.project_id != project_id:
            problems.append(Problem(
                "INPUT_NOT_IN_PROJECT", f"Inforce file '{file_id}' was not found in this project.",
                {"id": file_id},
            ))
            continue
        files.append(file)
        label = f"Inforce file '{file.filename}'"
        problems += _input_problems(label, file.id, file.status, file.fingerprint)
        if file.fingerprint and file.fingerprint_scheme != INFORCE_FINGERPRINT_SCHEME:
            problems.append(Problem(
                "INPUT_FINGERPRINT_OUTDATED",
                f"{label} has a '{file.fingerprint_scheme}' fingerprint; re-validate it to "
                f"{INFORCE_FINGERPRINT_SCHEME} before running.",
                {"id": file.id},
            ))
        if not file.row_count:
            problems.append(Problem("INPUT_EMPTY", f"{label} has no records.", {"id": file.id}))
    return files, problems


def check_tables(
    db: Session, project_id: str, kind: str, table_ids: list[str]
) -> tuple[list[tuple[Any, Any]], list[Problem]]:
    """Pinned tables of one kind: (table, set) pairs that belong to the project, and problems."""
    model, set_model = (AssumptionTable, AssumptionSet) if kind == "assumption" else (FactorTable, FactorSet)
    problems: list[Problem] = []
    if len(set(table_ids)) != len(table_ids):
        problems.append(Problem("INPUT_DUPLICATED", f"A {kind} table is pinned more than once."))
    found: list[tuple[Any, Any]] = []
    for table_id in dict.fromkeys(table_ids):
        table = db.get(model, table_id)
        table_set = db.get(set_model, table.set_id) if table else None
        if table is None or table_set is None or table_set.project_id != project_id:
            problems.append(Problem(
                "INPUT_NOT_IN_PROJECT", f"{kind.title()} table '{table_id}' was not found in this project.",
                {"id": table_id},
            ))
            continue
        found.append((table, table_set))
        problems += _input_problems(
            f"{kind.title()} table '{table.table_name}'", table.id, table.status, table.fingerprint,
        )
    return found, problems


def bind_tables(
    variables: dict[str, VariableSpec],
    pinned: dict[str, list[tuple[Any, Any]]],
) -> tuple[dict[str, dict[str, str]], dict[str, str], list[Problem]]:
    """Resolve every table a variable reads to exactly one pinned table ID.

    Returns (bindings name -> {kind, table_id}, value column per table ID, problems). A table is
    never chosen by name among unpinned tables, and two pinned tables with one name are refused.
    """
    bindings: dict[str, dict[str, str]] = {}
    value_columns: dict[str, str] = {}
    problems: list[Problem] = []
    for spec in sorted(variables.values(), key=lambda item: item.name):
        kind = spec.source_type
        if kind not in TABLE_KINDS:
            continue
        name = spec.source.get("table")
        if not name:
            problems.append(Problem("TABLE_NOT_NAMED", f"Variable '{spec.name}' names no {kind} table."))
            continue
        candidates = [table for table, _set in pinned[kind] if table.table_name == name]
        if not candidates:
            problems.append(Problem(
                "TABLE_NOT_PINNED",
                f"Variable '{spec.name}' needs {kind} table '{name}', which is not pinned in the "
                "Projection Set.",
                {"table_name": name, "kind": kind},
            ))
            continue
        if len(candidates) > 1:
            problems.append(Problem(
                "TABLE_AMBIGUOUS",
                f"{len(candidates)} pinned {kind} tables are named '{name}'; pin exactly one.",
                {"table_name": name, "table_ids": sorted(table.id for table in candidates)},
            ))
            continue
        table = candidates[0]
        existing = bindings.get(name)
        if existing and (existing["kind"] != kind or existing["table_id"] != table.id):
            problems.append(Problem(
                "TABLE_BINDING_CONFLICT", f"Table name '{name}' is bound to two different tables.",
            ))
            continue
        bindings[name] = {"kind": kind, "table_id": table.id}
        column = table.value_column or spec.source.get("value_column")
        if not column:
            problems.append(Problem("TABLE_NO_VALUE_COLUMN", f"{kind.title()} table '{name}' has no value column."))
        elif value_columns.setdefault(table.id, column) != column:
            problems.append(Problem(
                "TABLE_VALUE_COLUMN_CONFLICT",
                f"Variables read different value columns from table '{name}'.",
            ))
    return bindings, value_columns, problems


def check_scenario(
    db: Session, project_id: str, scenario_id: str, variables: dict[str, VariableSpec]
) -> tuple[ScenarioTable | None, ScenarioSet | None, list[Problem]]:
    """A scenario is runnable for this model: same project, validated, unchanged, valid overrides."""
    scenario = db.get(ScenarioTable, scenario_id)
    scenario_set = db.get(ScenarioSet, scenario.set_id) if scenario else None
    if scenario is None or scenario_set is None or scenario_set.project_id != project_id:
        return None, None, [Problem(
            "SCENARIO_NOT_IN_PROJECT", f"Scenario '{scenario_id}' was not found in this project.",
            {"scenario_id": scenario_id},
        )]
    label = f"Scenario '{scenario.scenario_name}'"
    problems: list[Problem] = []
    if scenario.status not in lifecycle.RUNNABLE_INPUT_STATUSES:
        problems.append(Problem("SCENARIO_NOT_RUNNABLE", f"{label} is '{scenario.status}', not validated."))
    overrides = list(scenario.overrides or [])
    if not scenario.fingerprint:
        problems.append(Problem("SCENARIO_NOT_FINGERPRINTED", f"{label} has no fingerprint."))
    elif scenario.fingerprint != scenario_fingerprint(overrides):
        problems.append(Problem(
            "SCENARIO_CHANGED",
            f"{label} was edited after it was validated (fingerprint mismatch); validate it again.",
        ))
    if scenario.scenario_type != "deterministic":
        problems.append(Problem(
            "SCENARIO_TYPE_UNSUPPORTED", f"{label} is '{scenario.scenario_type}'; only deterministic "
            "override scenarios can run.",
        ))
    problems += override_problems(label, overrides, variables)
    return scenario, scenario_set, problems


def _period(value: Any) -> int | None | bool:
    """A valid month number, None when absent, or False when invalid."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_PERIOD:
        return False
    return value


def override_problems(label: str, overrides: list, variables: dict[str, VariableSpec]) -> list[Problem]:
    """Deterministic override contract (the engine applies an override for months
    ``applies_from_period <= month <= applies_to_period``, both inclusive, either open):

    - the target is a variable of this model version, numeric, not calculated (formula /
      valuation), and its definition explicitly allows scenario overrides;
    - the operation is one of set / add / subtract / multiply / percent_change, with a numeric
      (non-boolean) value;
    - periods are whole months in 0..1200 and ``from <= to``;
    - two overrides of the same variable may not overlap in time (stacking is not defined, so
      ambiguity is rejected rather than resolved by list order).
    """
    problems: list[Problem] = []
    ranges: dict[str, list[tuple[int, float, int]]] = {}
    for index, override in enumerate(overrides):
        if not isinstance(override, dict):
            problems.append(Problem("SCENARIO_OVERRIDE_INVALID", f"{label}: override {index} is not an object."))
            continue
        target = override.get("target_variable")
        spec = variables.get(target)
        if spec is None:
            problems.append(Problem(
                "SCENARIO_TARGET_UNKNOWN", f"{label}: '{target}' is not a variable of this model.",
            ))
            continue
        if spec.source_type in CALCULATED_KINDS:
            problems.append(Problem(
                "SCENARIO_TARGET_CALCULATED", f"{label}: '{target}' is calculated and cannot be overridden.",
            ))
        elif not spec.allow_scenario_override:
            problems.append(Problem(
                "SCENARIO_TARGET_NOT_OVERRIDABLE",
                f"{label}: '{target}' does not allow scenario overrides in this model version.",
            ))
        if spec.data_type != "number":
            problems.append(Problem(
                "SCENARIO_TARGET_NOT_NUMERIC", f"{label}: '{target}' is not numeric; overrides are numeric.",
            ))
        operation = override.get("operation", "set")
        if operation not in OVERRIDE_OPERATIONS:
            problems.append(Problem("SCENARIO_OPERATION_INVALID", f"{label}: unknown operation '{operation}'."))
        value = override.get("value")
        if isinstance(value, bool) or not _is_number(value):
            problems.append(Problem(
                "SCENARIO_VALUE_INVALID", f"{label}: '{target}' {operation} needs a numeric value.",
            ))
        start, end = _period(override.get("applies_from_period")), _period(override.get("applies_to_period"))
        for key, parsed in (("applies_from_period", start), ("applies_to_period", end)):
            if parsed is False:
                problems.append(Problem(
                    "SCENARIO_PERIOD_INVALID", f"{label}: {key} must be a whole month from 0 to {MAX_PERIOD}.",
                ))
        if start is False or end is False:
            continue
        if start is not None and end is not None and start > end:
            problems.append(Problem(
                "SCENARIO_PERIOD_RANGE_INVALID",
                f"{label}: override of '{target}' starts at month {start} after it ends at month {end}.",
            ))
            continue
        low, high = start if start is not None else 0, end if end is not None else float("inf")
        for other_low, other_high, other_index in ranges.get(target, []):
            if low <= other_high and other_low <= high:
                problems.append(Problem(
                    "SCENARIO_OVERRIDE_OVERLAP",
                    f"{label}: overrides {other_index} and {index} of '{target}' overlap in time; "
                    "overlapping overrides are ambiguous and not allowed.",
                ))
        ranges.setdefault(target, []).append((low, high, index))
    return problems


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


# =============================================================================
# Freezing
# =============================================================================

def _implementation(function_ref: str) -> dict[str, Any] | None:
    registration = FORMULA_REGISTRY.get(function_ref)
    return registration.identity() if registration else None


def _formula_entry(row: FormulaRegistry) -> dict[str, Any]:
    entry = {
        "id": row.id,
        "name": row.name,
        "output_variable": row.output_variable,
        "function_ref": row.function_ref,
        "version": row.version or "v1",
        "dependencies": sorted(dep.depends_on_variable for dep in row.dependencies),
        "expression_text": row.expression_text,
        "unit": row.unit,
        "illustrative": bool(row.illustrative),
        # The exact executable code frozen with the formula (verified again before execution).
        "implementation": _implementation(row.function_ref),
    }
    entry["content_fingerprint"] = run_package.content_fingerprint(entry)
    return entry


def _variable_entry(row: ModelVariableDefinition) -> dict[str, Any]:
    entry = {
        "id": row.id,
        "definition_id": row.id,
        "name": row.variable_name,
        "display_name": row.display_name,
        "version": row.version or "v1",
        "kind": row.kind,
        "data_type": row.data_type,
        "unit": row.unit,
        "required": bool(row.required),
        "default_value": row.default_value,
        "source": dict(row.source or {}),
        "allow_scenario_override": bool(row.allow_scenario_override),
    }
    entry["content_fingerprint"] = run_package.content_fingerprint(entry)
    return entry


def _table_entry(table: Any, table_set: Any, value_column: str | None) -> dict[str, Any]:
    return {
        "id": table.id,
        "name": table.table_name,
        "set_id": table_set.id,
        "set_name": table_set.name,
        "table_type": table.table_type,
        "version_label": table.version_label,
        "lookup_keys": list(table.lookup_keys or []),
        "value_column": value_column or table.value_column,
        "row_count": len(table.data or []),
        "fingerprint": table.fingerprint,
    }


def collect(
    db: Session, project: Project, projection_set: ProjectionSet, scenario_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[Problem]]:
    """Resolve and check everything; return (configuration, governance, problems)."""
    problems: list[Problem] = []
    if projection_set.project_id != project.id:
        return None, None, [Problem("PROJECTION_SET_NOT_IN_PROJECT", "The Projection Set is not in this project.")]
    if projection_set.status not in lifecycle.RUNNABLE_PROJECTION_SET_STATUSES:
        problems.append(Problem(
            "PROJECTION_SET_NOT_VALIDATED",
            f"Projection Set '{projection_set.name}' is '{projection_set.status}'; validate it first.",
        ))
    model = check_model(db, project.id, projection_set)
    problems += model.problems
    problems += check_outputs(projection_set, model.published)
    files, inforce_problems = check_inforce(db, project.id, list(projection_set.inforce_file_ids or []))
    problems += inforce_problems
    pinned: dict[str, list[tuple[Any, Any]]] = {}
    for kind, ids in (("assumption", projection_set.assumption_table_ids),
                      ("factor", projection_set.factor_table_ids)):
        pinned[kind], table_problems = check_tables(db, project.id, kind, list(ids or []))
        problems += table_problems
    bindings, value_columns, binding_problems = bind_tables(model.variables, pinned)
    problems += binding_problems
    scenario, scenario_set, scenario_problems = check_scenario(db, project.id, scenario_id, model.variables)
    problems += scenario_problems
    try:
        build = build_identity()
    except BuildIdentityUnavailable as error:
        problems.append(Problem("BUILD_IDENTITY_UNAVAILABLE", str(error)))
    if problems:
        return None, None, problems

    version, owning_model = model.version, model.model
    configuration: dict[str, Any] = {
        "project": {"id": project.id},
        "projection_set": {
            "id": projection_set.id,
            "name": projection_set.name,
            "version_label": projection_set.version_label,
        },
        "model": {"id": owning_model.id, "name": owning_model.name, "product_code": owning_model.product_code},
        "model_version": {
            "id": version.id,
            "version_label": version.version_label,
            "basis": version.basis,
            "methodology": version.methodology,
            "methodology_version": None,  # not modelled yet (recorded explicitly as unknown)
            "block_name": version.block_name,
            "profile_name": version.profile_name,
            "illustrative": bool(version.illustrative),
        },
        "valuation_date": iso(projection_set.valuation_date),
        "horizon_months": projection_set.horizon_months,
        "time_step": projection_set.time_step,
        "execution_backend": "cpu",
        "parameters": dict(projection_set.parameters or {}),
        "formulas": [_formula_entry(row) for row in sorted(model.formulas, key=lambda r: r.output_variable)],
        "variables": [_variable_entry(model.variable_rows[name]) for name in sorted(model.variable_rows)],
        "datasets": {
            "inforce": [
                {
                    "id": file.id,
                    "name": file.filename,
                    "version_label": file.version_label,
                    "record_count": file.row_count,
                    "fingerprint": file.fingerprint,
                    "fingerprint_scheme": file.fingerprint_scheme,
                }
                for file in sorted(files, key=lambda item: item.id)
            ],
            "assumption_tables": [
                _table_entry(table, table_set, value_columns.get(table.id))
                for table, table_set in sorted(pinned["assumption"], key=lambda pair: pair[0].id)
            ],
            "factor_tables": [
                _table_entry(table, table_set, value_columns.get(table.id))
                for table, table_set in sorted(pinned["factor"], key=lambda pair: pair[0].id)
            ],
        },
        "table_bindings": bindings,
        "scenario": {
            "id": scenario.id,
            "set_id": scenario_set.id,
            "set_name": scenario_set.name,
            "name": scenario.scenario_name,
            "version_label": scenario.version_label,
            "scenario_type": scenario.scenario_type,
            "as_of_date": iso(scenario.as_of_date),
            "path_count": scenario.path_count,
            "fingerprint": scenario.fingerprint,
            "overrides": [dict(item) for item in scenario.overrides or []],
        },
        "outputs": {
            "output_variables": list(projection_set.output_variables or []),
            "trace_scope": normalise_trace_scope(projection_set.trace_scope),
            "max_traced_policies": settings.max_traced_policies,
            # How each output is labelled and aggregated (sum of flows vs end-of-period balance).
            "published": {
                name: {
                    "display_name": model.published[name].display_name,
                    "unit": model.published[name].unit,
                    "dimension": model.published[name].dimension,
                    "aggregation": model.published[name].aggregation,
                }
                for name in projection_set.output_variables or []
            },
        },
        "build": build,
    }
    governance = {
        "projection_set": {
            "status": projection_set.status,
            "validated_at": (projection_set.validation or {}).get("validated_at"),
        },
        "model": {"status": owning_model.status},
        "model_version": {"status": version.status, "is_current": bool(version.is_current)},
        "formulas": {row.id: row.status for row in model.formulas},
        "datasets": {
            **{file.id: file.status for file in files},
            **{table.id: table.status for kind in TABLE_KINDS for table, _set in pinned[kind]},
        },
        "scenario": {"status": scenario.status},
    }
    return configuration, governance, []


def freeze(
    db: Session,
    project: Project,
    projection_set: ProjectionSet,
    scenario_id: str,
    user: Any,
    *,
    run_id: str,
    run_set_id: str | None,
) -> FrozenPackage:
    """Build the immutable package for one run, or raise ``PackageInvalid`` listing every problem."""
    configuration, governance, problems = collect(db, project, projection_set, scenario_id)
    if problems:
        raise PackageInvalid(problems, projection_set_id=projection_set.id, scenario_id=scenario_id)
    now = now_utc()
    identity = {
        "package_id": str(uuid.uuid4()),
        "frozen_for_run_id": run_id,
        "run_set_id": run_set_id,
        "project_id": project.id,
        "created_at": iso(now),
        "created_by": {"id": user.id, "full_name": getattr(user, "full_name", None)},
    }
    governance = {**governance, "frozen_at": iso(now)}
    document = run_package.seal(identity, governance, configuration)
    return FrozenPackage(document=document, fingerprint=document["fingerprint"], configuration=configuration)
