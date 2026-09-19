"""Projection Runner — the main execution loop."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.dependency_engine.graph import topological_sort
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.projection_engine.context import build_context
from app.core.projection_engine.loop import load_formulas, load_policies
from app.core.output.aggregate import compute_summary
from app.core.output.storage import save_output, update_run_status
from app.core.variable_registry.resolver import resolve
from app.db.models.run import Run
from app.models.schemas import (
    CalculationError,
    CalculationResult,
    ProjectionResultSet,
    ProjectionRunDefinition,
)


def run_projection(
    run_def: ProjectionRunDefinition,
    db: Session,
    trace_logger=None,
) -> ProjectionResultSet:
    """Execute a full projection run."""

    # Ensure run record exists
    existing = db.query(Run).filter(Run.id == run_def.id).first()
    if existing is None:
        run = Run(
            id=run_def.id,
            project_id=run_def.project_id,
            status="pending",
        )
        db.add(run)
        db.flush()

    update_run_status(db, run_def.id, "running")
    db.commit()

    all_results: list[CalculationResult] = []
    all_errors: list[CalculationError] = []
    started_at = datetime.now(timezone.utc)

    policies = load_policies(db, run_def.dataset_ids)
    formulas = load_formulas(db)

    if not formulas:
        update_run_status(db, run_def.id, "success")
        db.commit()
        return ProjectionResultSet(
            run_id=run_def.id,
            scenario_id=",".join(run_def.scenario_ids),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            status="success",
            results=[], errors=[],
        )

    try:
        formula_order = topological_sort(formulas)
    except ValueError as e:
        update_run_status(db, run_def.id, "failed")
        db.commit()
        return ProjectionResultSet(
            run_id=run_def.id,
            scenario_id=",".join(run_def.scenario_ids),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            status="failed",
            results=[], errors=[CalculationError(type="circular_dependency", message=str(e))],
        )

    formula_map = {f.output_variable: f for f in formulas}
    error_count = 0

    for policy in policies:
        policy_id = policy["policy_id"]
        policy_data = policy.get("data", {})

        for scenario_id in run_def.scenario_ids:
            for month in range(1, run_def.projection_length_months + 1):
                context = build_context(run_def, policy_data, policy_id, scenario_id, month)

                for var_name in formula_order:
                    formula = formula_map.get(var_name)
                    if formula is None:
                        continue

                    try:
                        resolved_inputs: dict[str, Any] = {}
                        for dep in formula.dependencies:
                            resolution = resolve(dep, context, db, trace_logger=None)
                            if resolution.error_message:
                                raise ValueError(f"Cannot resolve '{dep}': {resolution.error_message}")
                            resolved_inputs[dep] = resolution.value

                        func = FORMULA_FUNCTIONS.get(formula.function_ref)
                        if func is None:
                            raise ValueError(f"Function '{formula.function_ref}' not found")

                        value = func(**resolved_inputs)

                        save_output(db, run_def.id, policy_id, scenario_id, month, var_name, value, product=context.product)

                        all_results.append(CalculationResult(
                            run_id=run_def.id, variable_id=var_name, policy_id=policy_id,
                            period=month, scenario_id=scenario_id, value=value, status="success",
                        ))

                    except Exception as e:
                        error_count += 1
                        error = CalculationError(
                            type=_classify_error(e), message=str(e), variable_id=var_name,
                            formula_id=formula.id, policy_id=policy_id, period=month, scenario_id=scenario_id,
                        )
                        all_errors.append(error)
                        all_results.append(CalculationResult(
                            run_id=run_def.id, variable_id=var_name, policy_id=policy_id,
                            period=month, scenario_id=scenario_id, value=None, status="error", error=error,
                        ))

        db.commit()

    if error_count == 0:
        status = "success"
    elif all(r.status == "error" for r in all_results):
        status = "failed"
    else:
        status = "partial_success"

    update_run_status(db, run_def.id, status)
    db.commit()

    summary = compute_summary(db, run_def.id, error_count=error_count)

    return ProjectionResultSet(
        run_id=run_def.id,
        scenario_id=",".join(run_def.scenario_ids),
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        status=status,
        results=all_results,
        errors=all_errors,
        summary=summary,
    )


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "zero division" in msg or "division by zero" in msg:
        return "division_by_zero"
    if "cannot resolve" in msg or "could not be resolved" in msg:
        return "missing_value"
    if "not found" in msg:
        return "invalid_formula"
    if "circular" in msg:
        return "circular_dependency"
    return "invalid_formula"
