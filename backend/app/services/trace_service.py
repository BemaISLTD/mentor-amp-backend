"""Trace: explain one calculated value from rows captured during the run (contract §E.10, §F.6).

Nothing is recalculated here. The tree is assembled from ``trace_logs`` rows written by the
engine while it calculated, so children are exactly the inputs the formula actually used. Only
rows of the run's accepted attempt are read, under the same access rules as other results, and
formula text/version, variable names and units come from the run's frozen package, not from
today's (editable) registry.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.projection_engine.engine import month_end_after
from app.core.valuation import illustrative_reserve
from app.db.models.run import Run
from app.db.models.trace_log import TraceLog
from app.services.common import ServiceError, iso, not_found, unwrap_value
from app.services.results_service import is_complete, result_run
from app.services.run_query_service import run_configuration

MAX_DEPTH = 10


def _accepted(db: Session, run: Run):
    return db.query(TraceLog).filter(
        TraceLog.run_id == run.id, TraceLog.attempt_number == run.accepted_attempt_number,
    )


def _traced_policy_ids(db: Session, run: Run) -> list[str]:
    return sorted(
        row[0] for row in db.query(TraceLog.policy_id)
        .filter(TraceLog.run_id == run.id, TraceLog.attempt_number == run.accepted_attempt_number)
        .distinct().all()
    )


def traced_policies(db: Session, run_id: str, include_partial: bool = False) -> dict[str, Any]:
    run = result_run(db, run_id, include_partial)
    config = run_configuration(db, run)
    mode = ((config.get("outputs") or {}).get("trace_scope") or {}).get("mode", "none")
    return {"run_id": run.id, "mode": mode, "policy_ids": _traced_policy_ids(db, run),
            "months": run.horizon_months, "complete": is_complete(run)}


class _TraceIndex:
    def __init__(self, rows: list[TraceLog]):
        self.formula: dict[tuple[str, int], TraceLog] = {}
        self.resolution: dict[tuple[str, int], TraceLog] = {}
        self.valuation: dict[tuple[str, int], TraceLog] = {}
        for row in rows:
            key = (row.variable_name, row.projection_month)
            if row.source_type == "formula" and row.formula_id:
                self.formula[key] = row
            elif row.source_type == "valuation":
                self.valuation[key] = row
            elif row.source_type != "error":
                self.resolution.setdefault(key, row)


class _TreeBuilder:
    def __init__(self, run, config: dict[str, Any], rows: list[TraceLog]):
        self.run = run
        self.index = _TraceIndex(rows)
        self.variables = {entry["name"]: entry for entry in config.get("variables") or []}
        self.formulas = {entry["id"]: entry for entry in config.get("formulas") or [] if entry.get("id")}

    def _base(self, variable: str, month: int, kind: str, value: Any) -> dict[str, Any]:
        registry = self.variables.get(variable) or {}
        return {
            "variable": variable,
            "display_name": registry.get("display_name") or variable,
            "kind": kind,
            "month": month,
            "period_end_date": iso(month_end_after(self.run.valuation_date, month)) if self.run.valuation_date else None,
            "value": value,
            "unit": registry.get("unit"),
            "formula": None,
            "source": None,
            "scenario_override": None,
            "was_defaulted": False,
            "children": [],
            "truncated": False,
        }

    def node(self, variable: str, month: int, depth: int) -> dict[str, Any]:
        key = (variable, month)
        if key in self.index.formula:
            row = self.index.formula[key]
            formula = self.formulas.get(row.formula_id) or {}
            node = self._base(variable, month, "formula", unwrap_value(row.output_value))
            node["formula"] = {
                "id": row.formula_id,
                "name": formula.get("name"),
                "function_ref": formula.get("function_ref"),
                "expression_text": formula.get("expression_text"),
                "version": formula.get("version"),
                "content_fingerprint": formula.get("content_fingerprint"),
                "illustrative": bool(formula.get("illustrative")),
            }
            inputs = list((row.input_values or {}).keys())
            if depth <= 0:
                node["truncated"] = bool(inputs)
            else:
                node["children"] = [self.node(name, month, depth - 1) for name in inputs]
            return node

        if key in self.index.valuation:
            row = self.index.valuation[key]
            node = self._base(variable, month, "valuation", unwrap_value(row.output_value))
            node["formula"] = {
                "id": None,
                "name": "Illustrative prospective reserve",
                "function_ref": illustrative_reserve.METHOD,
                "expression_text": illustrative_reserve.EXPRESSION_TEXT,
                "version": "m1",
                "illustrative": True,
            }
            node["source"] = {"type": "valuation", "method": illustrative_reserve.METHOD}
            if depth > 0:
                children = []
                for name, value in (row.input_values or {}).items():
                    if name.endswith("_next"):
                        base_name = name[: -len("_next")]
                        if value is None:
                            continue
                        if (base_name, month + 1) in self.index.formula:
                            children.append(self.node(base_name, month + 1, depth - 1))
                        else:
                            child = self._base(base_name, month + 1, "valuation", value)
                            child["truncated"] = True
                            children.append(child)
                    else:
                        rate_month = max(month, 1)
                        if (name, rate_month) in self.index.resolution:
                            children.append(self.node(name, rate_month, depth - 1))
                        else:
                            children.append(self._base(name, rate_month, "manual", value))
                node["children"] = children
            else:
                node["truncated"] = True
            return node

        if key in self.index.resolution:
            row = self.index.resolution[key]
            details = dict(row.input_values or {})
            kind = row.source_type or "input"
            node = self._base(variable, month, kind, unwrap_value(row.output_value))
            node["was_defaulted"] = bool(details.get("was_defaulted"))
            override = details.get("scenario_override")
            if override:
                node["scenario_override"] = override
            if kind in ("input", "assumption", "factor"):
                node["source"] = {
                    "type": kind,
                    "table": row.source_table,
                    "column": details.get("column"),
                    "lookup_keys": row.lookup_keys,
                    "dataset_id": details.get("dataset_id"),
                }
            elif kind == "context":
                node["source"] = {"type": "context", "field": details.get("field")}
            elif kind == "manual":
                node["source"] = {"type": "manual", "note": details.get("note") or "Variable default"}
            elif kind == "prior_output":
                node["source"] = {
                    "type": "prior_output",
                    "of_variable": details.get("of_variable"),
                    "of_month": details.get("of_month"),
                    "initial_value": bool(details.get("initial_value")),
                }
                node["truncated"] = not details.get("initial_value")
            else:
                node["source"] = {"type": kind}
            return node

        raise not_found(f"No trace for '{variable}' at month {month}.")


def trace_tree(
    db: Session, run_id: str, policy_id: str, month: int, variable: str, depth: int = 4,
    include_partial: bool = False,
) -> dict[str, Any]:
    run = result_run(db, run_id, include_partial)
    depth = max(0, min(depth, MAX_DEPTH))
    rows = (
        _accepted(db, run)
        .filter(
            TraceLog.policy_id == policy_id,
            TraceLog.projection_month.in_([month, month + 1, max(month, 1)]),
        )
        .all()
    )
    if not rows:
        traced = _traced_policy_ids(db, run)
        if policy_id not in traced:
            raise ServiceError(
                404, "NOT_FOUND",
                f"Trace was not captured for policy '{policy_id}' in this run.",
                {"reason": "TRACE_NOT_CAPTURED", "traced_policy_ids": traced},
            )
        raise not_found(f"No trace for month {month}.")
    config = run_configuration(db, run)
    builder = _TreeBuilder(run, config, rows)
    root = builder.node(variable, month, depth)
    scenario = config.get("scenario") or {}
    return {
        "run_id": run.id,
        "attempt_number": run.accepted_attempt_number,
        "complete": is_complete(run),
        "run_package_fingerprint": run.run_package_fingerprint,
        "policy_id": policy_id,
        "scenario": {"id": scenario.get("id"), "name": scenario.get("name")} if scenario.get("id") else None,
        "month": month,
        "period_end_date": iso(month_end_after(run.valuation_date, month)) if run.valuation_date else None,
        "illustrative": bool(run.illustrative),
        "root": root,
    }


def dependents(
    db: Session, run_id: str, policy_id: str, month: int, variable: str, include_partial: bool = False,
) -> dict[str, Any]:
    run = result_run(db, run_id, include_partial)
    rows = (
        _accepted(db, run)
        .filter(
            TraceLog.policy_id == policy_id,
            TraceLog.projection_month.in_([month - 1, month, month + 1]),
        )
        .all()
    )
    if not rows:
        raise ServiceError(
            404, "NOT_FOUND",
            f"Trace was not captured for policy '{policy_id}' in this run.",
            {"reason": "TRACE_NOT_CAPTURED", "traced_policy_ids": _traced_policy_ids(db, run)},
        )
    found: list[dict[str, Any]] = []
    for row in rows:
        inputs = row.input_values or {}
        if row.source_type == "formula" and row.projection_month == month and variable in inputs:
            found.append({"variable": row.variable_name, "kind": "formula", "month": month,
                          "value": unwrap_value(row.output_value), "formula_id": row.formula_id})
        elif (row.source_type == "prior_output" and row.projection_month == month + 1
              and inputs.get("of_variable") == variable):
            found.append({"variable": row.variable_name, "kind": "prior_output", "month": month + 1,
                          "value": unwrap_value(row.output_value), "formula_id": None})
        elif (row.source_type == "valuation" and row.projection_month == month - 1
              and f"{variable}_next" in inputs):
            found.append({"variable": row.variable_name, "kind": "valuation", "month": month - 1,
                          "value": unwrap_value(row.output_value), "formula_id": None})
    found.sort(key=lambda item: (item["month"], item["variable"]))
    return {"variable": variable, "month": month, "dependents": found}
