"""Results: summary, rows, aggregates, CSV export, run comparison, dashboard (contract §E.1, §E.8)."""

import csv
import io
import math
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.projection_engine.engine import month_end_after
from app.db.models.modeling import Model, ModelPublishedOutput
from app.db.models.projection import ProjectionSet, RunSet
from app.db.models.run import Run
from app.db.models.run_output import RunOutput
from app.db.models.scenario import ScenarioTable
from app.db.models.variable import VariableRegistry
from app.services import catalog_service
from app.services.common import (
    TERMINAL_RUN_STATUSES,
    bad_request,
    conflict,
    iso,
    now_utc,
    unwrap_value,
)
from app.services.run_service import get_run, run_view

DEFAULT_VARIABLES = ["reserve", "expected_payment"]


# =============================================================================
# Variable metadata for a run
# =============================================================================

def _variable_meta(db: Session, run: Run, names: list[str]) -> list[dict[str, Any]]:
    published = {
        row.variable_name: row
        for row in db.query(ModelPublishedOutput)
        .filter(ModelPublishedOutput.model_version_id == run.model_version_id)
        .all()
    } if run.model_version_id else {}
    registry = {row.name: row for row in db.query(VariableRegistry).filter(VariableRegistry.name.in_(names)).all()}
    meta = []
    for name in names:
        output = published.get(name)
        variable = registry.get(name)
        meta.append({
            "name": name,
            "display_name": output.display_name if output else (variable.display_name if variable else name),
            "unit": output.unit if output else (variable.unit if variable else None),
            "aggregation": output.aggregation if output else "sum",
        })
    return meta


def _requested_variables(run: Run, variables: str | None) -> list[str]:
    available = list((run.manifest or {}).get("output_variables") or [])
    if variables:
        requested = [name.strip() for name in variables.split(",") if name.strip()]
    else:
        requested = [name for name in DEFAULT_VARIABLES if name in available] or available
    unknown = [name for name in requested if name not in available]
    if unknown:
        raise bad_request(
            f"Variables not captured by this run: {', '.join(unknown)}.",
            available=available,
        )
    return requested


# =============================================================================
# Summary and rows
# =============================================================================

def summary(db: Session, run_id: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    if run.summary:
        return {**run.summary, "status": run.status}
    scenario = db.get(ScenarioTable, run.scenario_id) if run.scenario_id else None
    return {
        "run_id": run.id,
        "status": run.status,
        "illustrative": bool(run.illustrative),
        "scenario": {"id": scenario.id, "name": scenario.scenario_name} if scenario else None,
        "valuation_date": iso(run.valuation_date),
        "policy_count": run.progress_total or 0,
        "failed_policy_count": None,
        "period_count": run.horizon_months,
        "output_variable_count": len((run.manifest or {}).get("output_variables") or []),
        "output_row_count": None,
        "error_count": run.error_count or 0,
        "warning_count": run.warning_count or 0,
        "headline": None,
        "totals": {},
        "trace": {"captured": False, "mode": None, "captured_policy_count": 0, "policy_ids": []},
    }


def result_rows(
    db: Session,
    run_id: str,
    policy_id: str | None = None,
    variable: str | None = None,
    month: int | None = None,
    month_from: int | None = None,
    month_to: int | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> dict[str, Any]:
    run = get_run(db, run_id)
    query = db.query(RunOutput).filter(RunOutput.run_id == run.id)
    if policy_id:
        query = query.filter(RunOutput.policy_id == policy_id)
    if variable:
        query = query.filter(RunOutput.variable_name == variable)
    if month is not None:
        query = query.filter(RunOutput.projection_month == month)
    if month_from is not None:
        query = query.filter(RunOutput.projection_month >= month_from)
    if month_to is not None:
        query = query.filter(RunOutput.projection_month <= month_to)
    total = query.count()
    rows = (
        query.order_by(RunOutput.policy_id, RunOutput.projection_month, RunOutput.variable_name)
        .offset(offset)
        .limit(limit)
        .all()
    )
    units = {item["name"]: item["unit"] for item in _variable_meta(db, run, sorted({r.variable_name for r in rows}))}
    return {
        "run_id": run.id,
        "rows": [
            {
                "policy_id": row.policy_id,
                "scenario_id": row.scenario_id,
                "month": row.projection_month,
                "projection_year": math.ceil(row.projection_month / 12) if row.projection_month else 0,
                "period_end_date": iso(month_end_after(run.valuation_date, row.projection_month))
                if run.valuation_date else None,
                "variable": row.variable_name,
                "value": unwrap_value(row.value),
                "unit": units.get(row.variable_name),
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "illustrative": bool(run.illustrative),
    }


# =============================================================================
# Aggregates
# =============================================================================

def _monthly_sums(db: Session, run: Run, names: list[str], policy_id: str | None) -> dict[str, dict[int, float]]:
    value = RunOutput.value["value"].as_float()
    query = (
        db.query(RunOutput.variable_name, RunOutput.projection_month, func.sum(value))
        .filter(RunOutput.run_id == run.id, RunOutput.variable_name.in_(names))
    )
    if policy_id:
        query = query.filter(RunOutput.policy_id == policy_id)
    sums: dict[str, dict[int, float]] = {name: {} for name in names}
    for name, month, total in query.group_by(RunOutput.variable_name, RunOutput.projection_month).all():
        sums[name][int(month)] = float(total) if total is not None else None
    return sums


def _change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous * 100.0


def aggregates(
    db: Session,
    run_id: str,
    group_by: str = "projection_year",
    variables: str | None = None,
    policy_id: str | None = None,
) -> dict[str, Any]:
    run = get_run(db, run_id)
    if run.status not in TERMINAL_RUN_STATUSES:
        raise conflict(f"Run '{run.id}' is not finished (status: {run.status}).")
    if group_by not in ("projection_year", "projection_month"):
        raise bad_request("group_by must be 'projection_year' or 'projection_month'.")
    names = _requested_variables(run, variables)
    meta = _variable_meta(db, run, names)
    sums = _monthly_sums(db, run, names, policy_id)
    horizon = run.horizon_months or 0
    valuation_date = run.valuation_date

    if group_by == "projection_month":
        periods = [(month, month) for month in range(0, horizon + 1)]
    else:
        years = math.ceil(horizon / 12)
        periods = [(0, 0)] + [(year, min(year * 12, horizon)) for year in range(1, years + 1)]

    rows: list[dict[str, Any]] = []
    previous: dict[str, float | None] = {}
    for period, end_month in periods:
        start_month = 0 if period == 0 else (
            end_month if group_by == "projection_month" else (period - 1) * 12 + 1
        )
        values: dict[str, float | None] = {}
        for item in meta:
            series = sums[item["name"]]
            if item["aggregation"] == "end_of_period":
                values[item["name"]] = series.get(end_month)
            elif period == 0:
                values[item["name"]] = None
            else:
                months = [series[m] for m in range(start_month, end_month + 1) if series.get(m) is not None]
                values[item["name"]] = math.fsum(months) if months else None
        period_end = month_end_after(valuation_date, end_month) if valuation_date else None
        if group_by == "projection_month":
            label = period_end.strftime("%Y-%m") if period_end else str(period)
            if period == 0:
                label += " (valuation)"
        else:
            label = f"{period_end.year} (valuation)" if period == 0 else str(period_end.year)
        rows.append({
            "period": period,
            "period_label": label,
            "period_end_date": iso(period_end),
            "values": values,
            "change_pct": {name: _change_pct(values[name], previous.get(name)) for name in values},
        })
        previous = values
    return {
        "run_id": run.id,
        "group_by": group_by,
        "valuation_date": iso(valuation_date),
        "illustrative": bool(run.illustrative),
        "variables": meta,
        "rows": rows,
    }


def export_csv(
    db: Session, run_id: str, group_by: str = "projection_year", variables: str | None = None
) -> tuple[str, str]:
    data = aggregates(db, run_id, group_by=group_by, variables=variables)
    names = [item["name"] for item in data["variables"]]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["period", "period_label", "period_end_date", *names])
    for row in data["rows"]:
        writer.writerow([
            row["period"], row["period_label"], row["period_end_date"],
            *[("" if row["values"][name] is None else repr(row["values"][name])) for name in names],
        ])
    filename = f"mentoramp-run-{run_id[:8]}-{group_by}.csv"
    return buffer.getvalue(), filename


# =============================================================================
# Comparison (§E.8)
# =============================================================================

def _override_value(default: Any, override: dict | None) -> Any:
    return catalog_service._effective_value(default, override)  # noqa: SLF001 - shared rule


def _changed_inputs(db: Session, baseline: Run, current: Run) -> list[dict[str, Any]]:
    base_manifest = baseline.manifest or {}
    curr_manifest = current.manifest or {}
    changes: list[dict[str, Any]] = []
    base_overrides = {o.get("target_variable"): o for o in ((base_manifest.get("scenario") or {}).get("overrides") or [])}
    curr_overrides = {o.get("target_variable"): o for o in ((curr_manifest.get("scenario") or {}).get("overrides") or [])}
    registry = {row.name: row for row in db.query(VariableRegistry).all()}
    for name in sorted(set(base_overrides) | set(curr_overrides)):
        row = registry.get(name)
        default = None
        if row is not None:
            source = row.source or {}
            default = source.get("value", unwrap_value(row.default_value))
        base_value = _override_value(default, base_overrides.get(name))
        curr_value = _override_value(default, curr_overrides.get(name))
        if base_value != curr_value:
            changes.append({
                "variable": name,
                "baseline": base_value,
                "current": curr_value,
                "source": "scenario override",
                "unit": row.unit if row is not None else None,
            })
    for key, label in (("model_version", "model version"), ("projection_set", "projection set")):
        if (base_manifest.get(key) or {}).get("id") != (curr_manifest.get(key) or {}).get("id"):
            changes.append({
                "variable": key,
                "baseline": (base_manifest.get(key) or {}).get("version_label"),
                "current": (curr_manifest.get(key) or {}).get("version_label"),
                "source": label,
            })
    base_datasets = {d.get("id"): d.get("fingerprint") for d in base_manifest.get("datasets") or []}
    curr_datasets = {d.get("id"): d.get("fingerprint") for d in curr_manifest.get("datasets") or []}
    if base_datasets != curr_datasets:
        changes.append({"variable": "datasets", "baseline": sorted(base_datasets), "current": sorted(curr_datasets),
                        "source": "input datasets"})
    return changes


def _diff(baseline: float | None, current: float | None) -> dict[str, float | None]:
    difference = current - baseline if baseline is not None and current is not None else None
    return {
        "baseline": baseline,
        "current": current,
        "difference": difference,
        "difference_pct": (difference / baseline * 100.0) if difference is not None and baseline else None,
    }


def compare_runs(
    db: Session,
    baseline_run_id: str,
    current_run_id: str,
    variables: str | None = None,
    group_by: str = "projection_year",
) -> dict[str, Any]:
    baseline = get_run(db, baseline_run_id)
    current = get_run(db, current_run_id)
    if baseline.project_id != current.project_id:
        raise bad_request("Runs from different projects cannot be compared.")
    for label, run in (("Baseline", baseline), ("Current", current)):
        if run.status not in TERMINAL_RUN_STATUSES:
            raise conflict(f"{label} run '{run.id}' is not finished (status: {run.status}).")
    requested = variables or ",".join(
        name for name in ("reserve", "expected_payment", "pv_expected_payment")
        if name in ((baseline.manifest or {}).get("output_variables") or [])
    )
    base_agg = aggregates(db, baseline.id, group_by=group_by, variables=requested or None)
    curr_agg = aggregates(db, current.id, group_by=group_by, variables=requested or None)
    meta = base_agg["variables"]

    totals = []
    for item in meta:
        name = item["name"]
        if item["aggregation"] == "end_of_period":
            base_value = base_agg["rows"][0]["values"].get(name)
            curr_value = curr_agg["rows"][0]["values"].get(name)
            label = f"{item['display_name'].split(' (')[0]} at valuation"
        else:
            base_value = math.fsum(r["values"][name] or 0.0 for r in base_agg["rows"])
            curr_value = math.fsum(r["values"][name] or 0.0 for r in curr_agg["rows"])
            label = f"Total {item['display_name'].lower()}"
        totals.append({"variable": name, "label": label, **_diff(base_value, curr_value), "unit": item["unit"]})

    current_rows = {row["period"]: row for row in curr_agg["rows"]}
    by_period = []
    for row in base_agg["rows"]:
        other = current_rows.get(row["period"])
        if other is None:
            continue
        by_period.append({
            "period": row["period"],
            "period_label": row["period_label"],
            "values": {
                item["name"]: _diff(row["values"].get(item["name"]), other["values"].get(item["name"]))
                for item in meta
            },
        })

    changed = _changed_inputs(db, baseline, current)
    headline = totals[0] if totals else None
    registry = {row.name: row for row in db.query(VariableRegistry).all()}
    if len(changed) == 1 and headline and headline["difference"] is not None:
        driver = changed[0]["variable"]
        attribution = {
            "method": "single_changed_input",
            "available": True,
            "note": "Exactly one input differs between the runs, so the whole difference is "
                    "attributed to it. Multi-driver attribution arrives in M3.",
            "drivers": [{
                "driver": driver,
                "label": registry[driver].display_name if driver in registry and registry[driver].display_name else driver,
                "amount": headline["difference"],
                "unit": headline["unit"],
            }],
        }
    else:
        attribution = {
            "method": "single_changed_input",
            "available": False,
            "note": "More than one input differs (or none); multi-driver attribution arrives in M3.",
            "drivers": [],
        }

    def run_ref(run: Run) -> dict[str, Any]:
        scenario = db.get(ScenarioTable, run.scenario_id) if run.scenario_id else None
        return {"run_id": run.id, "name": run.name, "scenario_name": scenario.scenario_name if scenario else None,
                "status": run.status}

    return {
        "baseline": run_ref(baseline),
        "current": run_ref(current),
        "same_projection_set": baseline.projection_set_id == current.projection_set_id,
        "same_model_version": baseline.model_version_id == current.model_version_id,
        "illustrative": bool(baseline.illustrative or current.illustrative),
        "changed_inputs": changed,
        "totals": totals,
        "by_period": by_period,
        "attribution": attribution,
    }


# =============================================================================
# Dashboard (§E.1)
# =============================================================================

def _latest_comparison(db: Session, project_id: str) -> dict[str, Any] | None:
    for run_set in (
        db.query(RunSet).filter(RunSet.project_id == project_id).order_by(RunSet.created_at.desc()).limit(10).all()
    ):
        runs = db.query(Run).filter(Run.run_set_id == run_set.id, Run.status == "success").all()
        by_projection: dict[str, list[Run]] = {}
        for run in runs:
            by_projection.setdefault(run.projection_set_id, []).append(run)
        for projection_set_id, group in by_projection.items():
            if len(group) < 2:
                continue
            projection_set = db.get(ProjectionSet, projection_set_id)
            order = {scenario_id: index for index, scenario_id in enumerate(projection_set.scenario_ids or [])}
            group.sort(key=lambda run: order.get(run.scenario_id, 99))
            baseline, current = group[0], group[1]
            base_headline = (baseline.summary or {}).get("headline") or {}
            curr_headline = (current.summary or {}).get("headline") or {}
            if not base_headline or not curr_headline:
                continue
            diff = _diff(base_headline.get("value"), curr_headline.get("value"))
            base_scenario = db.get(ScenarioTable, baseline.scenario_id)
            curr_scenario = db.get(ScenarioTable, current.scenario_id)
            return {
                "baseline_run_id": baseline.id,
                "current_run_id": current.id,
                "baseline_label": base_scenario.scenario_name if base_scenario else None,
                "current_label": curr_scenario.scenario_name if curr_scenario else None,
                "metric": base_headline.get("metric"),
                "baseline_value": diff["baseline"],
                "current_value": diff["current"],
                "difference": diff["difference"],
                "difference_pct": diff["difference_pct"],
                "changed_inputs": [
                    {"variable": c["variable"], "baseline": c["baseline"], "current": c["current"]}
                    for c in _changed_inputs(db, baseline, current)
                ],
            }
    return None


def dashboard(db: Session, project_id: str) -> dict[str, Any]:
    catalog_service.get_project(db, project_id)
    now = now_utc()
    models = db.query(Model).filter(Model.project_id == project_id).all()
    model_status: dict[str, int] = {}
    for model in models:
        model_status[model.status] = model_status.get(model.status, 0) + 1

    run_status = dict(
        db.query(Run.status, func.count(Run.id)).filter(Run.project_id == project_id).group_by(Run.status).all()
    )
    last_7 = db.query(func.count(Run.id)).filter(
        Run.project_id == project_id, Run.created_at >= now - timedelta(days=7)
    ).scalar() or 0
    previous_7 = db.query(func.count(Run.id)).filter(
        Run.project_id == project_id,
        Run.created_at >= now - timedelta(days=14),
        Run.created_at < now - timedelta(days=7),
    ).scalar() or 0

    inputs = catalog_service.list_inputs(db, project_id)["summary"]
    latest_success = (
        db.query(Run)
        .filter(Run.project_id == project_id, Run.status == "success")
        .order_by(Run.completed_at.desc())
        .first()
    )
    headline = None
    if latest_success and (latest_success.summary or {}).get("headline"):
        head = latest_success.summary["headline"]
        headline = {
            "run_id": latest_success.id,
            "run_name": latest_success.name,
            "scenario_name": (latest_success.summary.get("scenario") or {}).get("name"),
            "metric": head.get("metric"),
            "label": head.get("label"),
            "value": head.get("value"),
            "unit": head.get("unit"),
            "as_of": head.get("as_of"),
            "illustrative": bool(latest_success.illustrative),
            "trace_policy_id": ((latest_success.summary.get("trace") or {}).get("policy_ids") or [None])[0],
        }
    recent = db.query(Run).filter(Run.project_id == project_id).order_by(Run.created_at.desc()).limit(5).all()
    return {
        "project_id": project_id,
        "as_of": iso(now),
        "models": {"total": len(models), "by_status": model_status},
        "runs": {
            "total": sum(run_status.values()),
            "last_7_days": last_7,
            "previous_7_days": previous_7,
            "week_over_week_pct": _change_pct(float(last_7), float(previous_7)) if previous_7 else None,
            "by_status": run_status,
        },
        "inputs": {"total": inputs["total"], "validated": inputs["validated"], "needs_review": inputs["needs_review"]},
        "investigations": {"available": False, "total": 0, "pinned": 0},
        "headline": headline,
        "latest_comparison": _latest_comparison(db, project_id),
        "recent_runs": [
            {
                "id": run.id,
                "name": run.name,
                "scenario_name": view["scenario"]["name"] if view["scenario"] else None,
                "valuation_date": view["valuation_date"],
                "status": run.status,
                "created_at": view["created_at"],
                "completed_at": view["completed_at"],
            }
            for run in recent
            for view in [run_view(db, run, include_summary=False)]
        ],
        "recent_investigations": [],
        "continue_investigation": None,
    }
