"""Non-HTTP engine entry points — usable from scripts and tests without FastAPI."""

from sqlalchemy.orm import Session

from app.core.dependency_engine.graph import topological_sort, validate_dependencies
from app.core.formula_engine.registry import list_all as list_all_formulas
from app.core.projection_engine.runner import run_projection as _run_projection
from app.core.variable_registry.registry import register as _register_var
from app.core.variable_registry.registry import list_all as list_all_vars
from app.core.variable_registry.resolver import resolve as _resolve
from app.core.trace_engine.tracer import log_resolution
from app.db.database import SessionLocal
from app.models.schemas import (
    DependencyError,
    DependencyGraphView,
    FormulaDefinition,
    ProjectionContext,
    ProjectionResultSet,
    ProjectionRunDefinition,
    VariableDefinition,
    VariableResolutionResult,
)


def register_variable(variable: VariableDefinition) -> VariableDefinition:
    db: Session = SessionLocal()
    try:
        return _register_var(db, variable)
    finally:
        db.close()


def resolve_variable(name: str, context: ProjectionContext) -> VariableResolutionResult:
    db: Session = SessionLocal()
    try:
        return _resolve(name, context, db, trace_logger=lambda ctx, res: log_resolution(ctx, res, db))
    finally:
        db.close()


def build_dependency_graph(formula_ids: list[str] | None = None) -> DependencyGraphView:
    from app.core.dependency_engine.visualizer import to_graph_view
    db: Session = SessionLocal()
    try:
        formulas = list_all_formulas(db)
        if formula_ids:
            formulas = [f for f in formulas if f.id in formula_ids]
        return to_graph_view(formulas)
    finally:
        db.close()


def validate_formula_database() -> list[DependencyError]:
    db: Session = SessionLocal()
    try:
        formulas = list_all_formulas(db)
        variables = list_all_vars(db)
        registered = {v.name for v in variables}
        return validate_dependencies(formulas, registered)
    finally:
        db.close()


def get_execution_order() -> list[str]:
    db: Session = SessionLocal()
    try:
        formulas = list_all_formulas(db)
        return topological_sort(formulas)
    finally:
        db.close()


def run_projection(run_def: ProjectionRunDefinition) -> ProjectionResultSet:
    """Execute a full projection run (non-HTTP entry point)."""
    db: Session = SessionLocal()
    try:
        return _run_projection(run_def, db)
    finally:
        db.close()
