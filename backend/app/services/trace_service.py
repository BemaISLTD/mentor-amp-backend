"""Trace: explain one calculated value from rows captured during the run (contract §E.10, §F.6).

Nothing is recalculated here. The tree is assembled from ``trace_logs`` rows written by the
engine while it calculated, so children are exactly the inputs the formula actually used.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.projection_engine.engine import month_end_after
from app.core.valuation import illustrative_reserve
from app.db.models.formula import FormulaRegistry
from app.db.models.scenario import ScenarioTable
from app.db.models.trace_log import TraceLog
from app.db.models.variable import VariableRegistry
from app.services.common import ServiceError, iso, not_found, unwrap_value
from app.services.run_service import get_run

MAX_DEPTH = 10


def traced_policies(db: Session, run_id: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    policy_ids = sorted(
        row[0] for row in db.query(TraceLog.policy_id).filter(TraceLog.run_id == run.id).distinct().all()
    )
    mode = ((run.manifest or {}).get("trace_scope") or {}).get("mode", "none")
    return {"run_id": run.id, "mode": mode, "policy_ids": policy_ids, "months": run.horizon_months}


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
    def __init__(self, db: Session, run, rows: list[TraceLog]):
        self.db = db
        self.run = run
        self.index = _TraceIndex(rows)
        self.variables = {row.name: row for row in db.query(VariableRegistry).all()}
        self._formulas: dict[str, FormulaRegistry | None] = {}

    def _formula(self, formula_id: str) -> FormulaRegistry | None:
        if formula_id not in self._formulas:
            self._formulas[formula_id] = self.db.get(FormulaRegistry, formula_id)
        return self._formulas[formula_id]

    def _base(self, variable: str, month: int, kind: str, value: Any) -> dict[str, Any]:
        registry = self.variables.get(variable)
        return {
            "variable": variable,
            "display_name": (registry.display_name if registry and registry.display_name else variable),
            "kind": kind,
            "month": month,
            "period_end_date": iso(month_end_after(self.run.valuation_date, month)) if self.run.valuation_date else None,
            "value": value,
            "unit": registry.unit if registry else None,
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
            formula = self._formula(row.formula_id)
            node = self._base(variable, month, "formula", unwrap_value(row.output_value))
            node["formula"] = {
                "id": row.formula_id,
                "name": formula.name if formula else None,
                "function_ref": formula.function_ref if formula else None,
                "expression_text": formula.expression_text if formula else None,
                "version": formula.version if formula else None,
                "illustrative": bool(formula.illustrative) if formula else False,
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
    db: Session, run_id: str, policy_id: str, month: int, variable: str, depth: int = 4
) -> dict[str, Any]:
    run = get_run(db, run_id)
    depth = max(0, min(depth, MAX_DEPTH))
    rows = (
        db.query(TraceLog)
        .filter(
            TraceLog.run_id == run.id,
            TraceLog.policy_id == policy_id,
            TraceLog.projection_month.in_([month, month + 1, max(month, 1)]),
        )
        .all()
    )
    if not rows:
        traced = traced_policies(db, run.id)["policy_ids"]
        if policy_id not in traced:
            raise ServiceError(
                404, "NOT_FOUND",
                f"Trace was not captured for policy '{policy_id}' in this run.",
                {"reason": "TRACE_NOT_CAPTURED", "traced_policy_ids": traced},
            )
        raise not_found(f"No trace for month {month}.")
    builder = _TreeBuilder(db, run, rows)
    root = builder.node(variable, month, depth)
    scenario = db.get(ScenarioTable, run.scenario_id) if run.scenario_id else None
    return {
        "run_id": run.id,
        "policy_id": policy_id,
        "scenario": {"id": scenario.id, "name": scenario.scenario_name} if scenario else None,
        "month": month,
        "period_end_date": iso(month_end_after(run.valuation_date, month)) if run.valuation_date else None,
        "illustrative": bool(run.illustrative),
        "root": root,
    }


def dependents(db: Session, run_id: str, policy_id: str, month: int, variable: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    rows = (
        db.query(TraceLog)
        .filter(
            TraceLog.run_id == run.id,
            TraceLog.policy_id == policy_id,
            TraceLog.projection_month.in_([month - 1, month, month + 1]),
        )
        .all()
    )
    if not rows:
        raise ServiceError(
            404, "NOT_FOUND",
            f"Trace was not captured for policy '{policy_id}' in this run.",
            {"reason": "TRACE_NOT_CAPTURED", "traced_policy_ids": traced_policies(db, run.id)["policy_ids"]},
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
