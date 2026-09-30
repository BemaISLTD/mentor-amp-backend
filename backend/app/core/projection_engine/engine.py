"""Pure projection engine (CPU reference path) — contract §F.

The engine receives a fully loaded ``RunData`` and returns results; it performs no database or
file access. For each policy and each month it:

1. builds the context (projection month, policy year, attained age, dates);
2. runs each formula in dependency order, resolving its inputs through the rules in §F.3
   (exact table lookups, no silent fallbacks, zero is a valid value, scenario overrides);
3. after the last month, runs the valuation step (§F.5) and the self-checks (§F.8);
4. records trace rows during the calculation for traced policies (§F.6).

A policy error stops that policy only; the caller decides the run status.
"""

import math
from calendar import monthrange
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.dependency_engine.graph import topological_sort
from app.core.projection_engine.run_data import (
    FormulaSpec,
    PolicyRecord,
    RunData,
    VariableSpec,
    normalize_key,
)
from app.core.valuation import illustrative_reserve

CONTEXT_FIELDS = (
    "projection_month",
    "policy_month",
    "policy_year",
    "attained_age",
    "valuation_date",
    "period_end_date",
)
OVERRIDE_OPERATIONS = ("set", "add", "subtract", "multiply", "percent_change")
CONSISTENCY_TOLERANCE = 1e-9


class EngineError(Exception):
    """A calculation error with a contract error type (§F.10)."""

    def __init__(
        self,
        error_type: str,
        message: str,
        variable: str | None = None,
        month: int | None = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.variable = variable
        self.month = month

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": self.message,
            "variable": self.variable,
            "month": self.month,
        }


@dataclass
class PolicyResult:
    policy_id: str
    outputs: list[tuple[int, str, float]] = field(default_factory=list)
    trace_rows: list[dict[str, Any]] = field(default_factory=list)
    sums: dict[str, float] = field(default_factory=dict)
    reserve_0: float | None = None
    warnings: list[str] = field(default_factory=list)
    error: EngineError | None = None
    months_computed: int = 0


@dataclass
class _Resolution:
    value: Any
    source_type: str
    source_table: str | None = None
    lookup_keys: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# Dates and context
# ---------------------------------------------------------------------------------------------

def month_end_after(start: date, months: int) -> date:
    """Last day of the month that is ``months`` after ``start``'s month."""
    total = start.year * 12 + (start.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, monthrange(year, month + 1)[1])


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def months_in_force_at(issue_date: date | None, valuation_date: date) -> int:
    if issue_date is None:
        return 0
    months = (valuation_date.year - issue_date.year) * 12 + (valuation_date.month - issue_date.month)
    return max(months, 0)


def _to_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------------------------

def order_formulas(formulas: list[FormulaSpec]) -> list[FormulaSpec]:
    """Return formulas in dependency order (deterministic). Raises EngineError on cycles."""
    by_output = {formula.output_variable: formula for formula in formulas}
    if len(by_output) != len(formulas):
        raise EngineError("invalid_formula", "Two formulas produce the same output variable.")
    ordered_names = sorted(formulas, key=lambda formula: formula.output_variable)
    try:
        order = topological_sort(ordered_names)  # type: ignore[arg-type]
    except ValueError as exc:
        raise EngineError("circular_dependency", str(exc)) from exc
    return [by_output[name] for name in order if name in by_output]


# ---------------------------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------------------------

class _PolicyCalculator:
    def __init__(
        self,
        data: RunData,
        policy: PolicyRecord,
        functions: Mapping[str, Callable[..., Any]],
    ):
        self.data = data
        self.policy = policy
        self.functions = functions
        self.traced = policy.policy_id in data.traced_policy_ids
        self.result = PolicyResult(policy_id=policy.policy_id)
        self.history: dict[str, list[float]] = {f.output_variable: [] for f in data.formulas}
        self.rate_by_month: dict[str, list[float]] = {}
        issue_date = _parse_date(policy.data.get("issue_date"))
        self.months_in_force = months_in_force_at(issue_date, data.valuation_date)
        self.issue_age = _to_int(policy.data.get("issue_age"))
        self.rate_variables: set[str] = {
            str(spec.source.get("rate"))
            for spec in data.valuation_variables()
            if spec.source.get("rate")
        }
        # per-month state
        self.month = 0
        self.context: dict[str, Any] = {}
        self.resolved: dict[str, _Resolution] = {}
        self.computed: dict[str, float] = {}

    # -- context ---------------------------------------------------------------------------
    def _build_context(self, month: int) -> dict[str, Any]:
        policy_month = self.months_in_force + month
        return {
            "projection_month": month,
            "policy_month": policy_month,
            "policy_year": (policy_month - 1) // 12 + 1 if policy_month >= 1 else 0,
            "attained_age": (
                self.issue_age + (policy_month - 1) // 12 if self.issue_age is not None else None
            ),
            "valuation_date": self.data.valuation_date.isoformat(),
            "period_end_date": month_end_after(self.data.valuation_date, month).isoformat(),
        }

    # -- trace -----------------------------------------------------------------------------
    def _trace(self, **row: Any) -> None:
        if not self.traced:
            return
        row.setdefault("policy_id", self.policy.policy_id)
        row.setdefault("projection_month", self.month)
        row.setdefault("formula_id", None)
        row.setdefault("input_values", None)
        row.setdefault("source_table", None)
        row.setdefault("lookup_keys", None)
        row.setdefault("error_message", None)
        self.result.trace_rows.append(row)

    # -- resolution (contract §F.3) --------------------------------------------------------
    def _coerce(self, spec: VariableSpec, raw: Any) -> Any:
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return None
        if spec.data_type == "number":
            if isinstance(raw, bool):
                raise EngineError(
                    "invalid_data_type", f"'{spec.name}' expects a number, got {raw!r}.",
                    spec.name, self.month,
                )
            try:
                return float(str(raw).strip()) if isinstance(raw, str) else float(raw)
            except (TypeError, ValueError) as exc:
                raise EngineError(
                    "invalid_data_type", f"'{spec.name}' expects a number, got {raw!r}.",
                    spec.name, self.month,
                ) from exc
        if spec.data_type == "string":
            text = str(raw).strip()
            return text.upper() if spec.source.get("transform") == "upper" else text
        return raw

    def _base_value(self, spec: VariableSpec) -> _Resolution:
        source = spec.source
        kind = spec.source_type

        if kind == "input":
            column = source.get("column") or spec.name
            raw = self.policy.data.get(column)
            return _Resolution(
                value=self._coerce(spec, raw),
                source_type="input",
                source_table=self.policy.dataset_name,
                lookup_keys={"policy_id": self.policy.policy_id},
                detail={"column": column, "dataset_id": self.policy.dataset_id},
            )

        if kind == "context":
            name = source.get("field") or spec.name
            if name not in CONTEXT_FIELDS:
                raise EngineError(
                    "missing_variable", f"Unknown context field '{name}'.", spec.name, self.month
                )
            value = self.context.get(name)
            if value is None:
                raise EngineError(
                    "missing_value",
                    f"Context field '{name}' is not available (check issue_age/issue_date).",
                    spec.name, self.month,
                )
            return _Resolution(value=value, source_type="context", detail={"field": name})

        if kind in ("assumption", "factor"):
            table_name = source.get("table")
            table = self.data.tables.get(table_name or "")
            if table is None:
                raise EngineError(
                    "lookup_failed", f"Table '{table_name}' is not loaded for this run.",
                    spec.name, self.month,
                )
            key_map: dict[str, str] = source.get("key_map") or {}
            keys: dict[str, Any] = {}
            for column in table.key_columns:
                key_variable = key_map.get(column, column)
                keys[column] = normalize_key(self.resolve(key_variable))
            raw = table.index.get(tuple(keys[column] for column in table.key_columns), _MISSING)
            if raw is _MISSING:
                raise EngineError(
                    "lookup_failed",
                    f"No row in '{table.name}' for {keys}.",
                    spec.name, self.month,
                )
            return _Resolution(
                value=self._coerce(spec, raw),
                source_type=kind,
                source_table=table.name,
                lookup_keys=keys,
                detail={"column": table.value_column, "dataset_id": table.id},
            )

        if kind == "manual":
            value = source.get("value", spec.default_value)
            return _Resolution(
                value=self._coerce(spec, value),
                source_type="manual",
                source_table="manual",
                detail={"note": "Variable default"},
            )

        if kind == "prior_output":
            of_variable = source.get("variable")
            offset = int(source.get("offset", 1))
            of_month = self.month - offset
            if not of_variable:
                raise EngineError(
                    "missing_variable", f"'{spec.name}' has no source variable.",
                    spec.name, self.month,
                )
            if of_month <= 0:
                if "initial_value" not in source:
                    raise EngineError(
                        "missing_value",
                        f"'{spec.name}' needs an initial_value for month {self.month}.",
                        spec.name, self.month,
                    )
                value = source["initial_value"]
                detail = {"of_variable": of_variable, "of_month": of_month, "initial_value": True}
            else:
                series = self.history.get(of_variable)
                if series is None or len(series) < of_month:
                    raise EngineError(
                        "missing_value",
                        f"No value of '{of_variable}' for month {of_month}.",
                        spec.name, self.month,
                    )
                value = series[of_month - 1]
                detail = {"of_variable": of_variable, "of_month": of_month}
            return _Resolution(
                value=self._coerce(spec, value), source_type="prior_output", detail=detail
            )

        if kind == "formula":
            raise EngineError(
                "invalid_formula",
                f"'{spec.name}' is used before its formula has been calculated.",
                spec.name, self.month,
            )

        raise EngineError(
            "missing_variable", f"Unsupported source type '{kind}' for '{spec.name}'.",
            spec.name, self.month,
        )

    def _apply_overrides(self, spec: VariableSpec, resolution: _Resolution) -> None:
        for override in self.data.scenario.overrides:
            if override.get("target_variable") != spec.name:
                continue
            start = override.get("applies_from_period")
            end = override.get("applies_to_period")
            if start not in (None, "") and self.month < int(start):
                continue
            if end not in (None, "") and self.month > int(end):
                continue
            operation = str(override.get("operation", "set"))
            try:
                operand = float(override.get("value"))
            except (TypeError, ValueError) as exc:
                raise EngineError(
                    "scenario_override_failed",
                    f"Override value for '{spec.name}' is not a number.",
                    spec.name, self.month,
                ) from exc
            base = resolution.value
            if operation != "set" and base is None:
                raise EngineError(
                    "scenario_override_failed",
                    f"Cannot {operation} a missing value for '{spec.name}'.",
                    spec.name, self.month,
                )
            if operation == "set":
                new = operand
            elif operation == "add":
                new = base + operand
            elif operation == "subtract":
                new = base - operand
            elif operation == "multiply":
                new = base * operand
            elif operation == "percent_change":
                new = base * (1.0 + operand)
            else:
                raise EngineError(
                    "scenario_override_failed",
                    f"Unknown override operation '{operation}'.",
                    spec.name, self.month,
                )
            resolution.detail["scenario_override"] = {
                "scenario": self.data.scenario.name,
                "operation": operation,
                "value": operand,
                "base_value": base,
            }
            resolution.value = new

    def _check_value(self, spec: VariableSpec, value: Any) -> None:
        if spec.data_type != "number" or value is None:
            return
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise EngineError(
                "invalid_data_type", f"'{spec.name}' is not a finite number ({value!r}).",
                spec.name, self.month,
            )
        if spec.unit == "probability" and not (0.0 <= value <= 1.0):
            raise EngineError(
                "invalid_data_type",
                f"'{spec.name}' = {value} is outside [0, 1].",
                spec.name, self.month,
            )

    def resolve(self, name: str) -> Any:
        if name in self.computed:
            return self.computed[name]
        if name in self.resolved:
            return self.resolved[name].value
        spec = self.data.variables.get(name)
        if spec is None:
            raise EngineError(
                "missing_variable", f"Variable '{name}' is not registered.", name, self.month
            )
        resolution = self._base_value(spec)
        self._apply_overrides(spec, resolution)
        was_defaulted = False
        if resolution.value is None and spec.default_value is not None:
            resolution.value = self._coerce(spec, spec.default_value)
            was_defaulted = True
        if resolution.value is None and spec.required:
            raise EngineError(
                "missing_value", f"Required variable '{name}' has no value.", name, self.month
            )
        self._check_value(spec, resolution.value)
        self.resolved[name] = resolution
        if spec.name in self.rate_variables:
            self.rate_by_month.setdefault(spec.name, []).append(resolution.value)
        self._trace(
            variable_name=name,
            output_value={"value": resolution.value},
            source_type=resolution.source_type,
            source_table=resolution.source_table,
            lookup_keys=resolution.lookup_keys,
            input_values={**resolution.detail, "was_defaulted": was_defaulted},
        )
        return resolution.value

    # -- formulas --------------------------------------------------------------------------
    def _run_formula(self, formula: FormulaSpec) -> None:
        func = self.functions.get(formula.function_ref)
        if func is None:
            raise EngineError(
                "invalid_formula",
                f"Function '{formula.function_ref}' is not registered.",
                formula.output_variable, self.month,
            )
        inputs = {dependency: self.resolve(dependency) for dependency in formula.dependencies}
        try:
            value = func(**inputs)
        except ZeroDivisionError as exc:
            raise EngineError(
                "division_by_zero", f"Division by zero in '{formula.name}'.",
                formula.output_variable, self.month,
            ) from exc
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001 - any formula failure is reported, not raised
            raise EngineError(
                "invalid_formula", f"'{formula.name}' failed: {exc}",
                formula.output_variable, self.month,
            ) from exc
        spec = self.data.variables.get(formula.output_variable)
        if spec is not None:
            self._check_value(spec, value)
        self.computed[formula.output_variable] = value
        self.history[formula.output_variable].append(value)
        self._trace(
            variable_name=formula.output_variable,
            formula_id=formula.id,
            input_values=dict(inputs),
            output_value={"value": value},
            source_type="formula",
        )

    # -- valuation and checks (contract §F.5, §F.8) ----------------------------------------
    def _valuation(self) -> dict[str, list[float]]:
        results: dict[str, list[float]] = {}
        for spec in self.data.valuation_variables():
            method = spec.source.get("method")
            if method != illustrative_reserve.METHOD:
                raise EngineError(
                    "invalid_formula", f"Unknown valuation method '{method}'.", spec.name
                )
            cash_flow_name = spec.source.get("cash_flow")
            rate_name = spec.source.get("rate")
            cash_flows = self.history.get(cash_flow_name or "")
            rates = self.rate_by_month.get(rate_name or "")
            if cash_flows is None or not rates:
                raise EngineError(
                    "missing_value",
                    f"Valuation of '{spec.name}' needs '{cash_flow_name}' and '{rate_name}'.",
                    spec.name,
                )
            rate = rates[0]
            if any(abs(other - rate) > 1e-15 for other in rates):
                self.result.warnings.append(
                    f"'{rate_name}' changes over time; the M1 valuation uses the month-1 rate."
                )
            reserves = illustrative_reserve.prospective_reserve(cash_flows, rate)
            results[spec.name] = reserves
            if self.traced:
                horizon = len(cash_flows)
                for month in range(0, horizon + 1):
                    self.month = month
                    self._trace(
                        variable_name=spec.name,
                        source_type="valuation",
                        source_table=illustrative_reserve.METHOD,
                        input_values={
                            rate_name: rate,
                            f"{cash_flow_name}_next": cash_flows[month] if month < horizon else None,
                            f"{spec.name}_next": reserves[month + 1] if month < horizon else None,
                        },
                        output_value={"value": reserves[month]},
                    )
            check_name = spec.source.get("consistency_sum_of")
            if check_name and check_name in self.history:
                total = math.fsum(self.history[check_name])
                scale = max(abs(total), abs(reserves[0]), 1.0)
                if abs(total - reserves[0]) / scale > CONSISTENCY_TOLERANCE:
                    self.result.warnings.append(
                        f"Consistency check failed: {spec.name}_0 = {reserves[0]} but "
                        f"sum of {check_name} = {total}."
                    )
        return results

    def _series_checks(self) -> None:
        for name, series in self.history.items():
            spec = self.data.variables.get(name)
            if spec is None or not spec.source.get("non_increasing"):
                continue
            for index in range(1, len(series)):
                if series[index] > series[index - 1] + 1e-15:
                    self.result.warnings.append(
                        f"'{name}' increased at month {index + 1}; it should never increase."
                    )
                    break

    # -- main loop -------------------------------------------------------------------------
    def run(self) -> PolicyResult:
        try:
            for month in range(1, self.data.horizon_months + 1):
                self.month = month
                self.context = self._build_context(month)
                self.resolved = {}
                self.computed = {}
                for formula in self.data.formulas:
                    self._run_formula(formula)
                self.result.months_computed = month
            valuation = self._valuation()
            self._series_checks()
        except EngineError as error:
            if error.month is None:
                error.month = self.month
            self.result.error = error
            self._trace(
                variable_name=error.variable or "",
                source_type="error",
                error_message=f"{error.error_type}: {error.message}",
            )
            self.result.outputs = []
            return self.result

        wanted = set(self.data.output_variables)
        outputs: list[tuple[int, str, float]] = []
        for name in self.data.output_variables:
            if name in valuation:
                outputs.extend((month, name, value) for month, value in enumerate(valuation[name]))
            elif name in self.history:
                outputs.extend(
                    (month, name, value)
                    for month, value in enumerate(self.history[name], start=1)
                )
        self.result.outputs = outputs
        for name, series in self.history.items():
            if name in wanted:
                self.result.sums[name] = math.fsum(series)
        for name, reserves in valuation.items():
            if name in wanted:
                self.result.reserve_0 = reserves[0]
                self.result.sums[f"{name}_0"] = reserves[0]
        return self.result


_MISSING = object()


def run_policy(
    data: RunData,
    policy: PolicyRecord,
    functions: Mapping[str, Callable[..., Any]],
) -> PolicyResult:
    """Project one policy over the horizon and value it. Never raises for calculation errors."""
    return _PolicyCalculator(data, policy, functions).run()
