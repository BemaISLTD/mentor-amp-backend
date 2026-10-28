"""API endpoints for the Formula Registry.

Formulas that belong to a model version are project-owned: reading or changing them requires
access to that project. Formulas without a model version are the shared legacy catalog.
"""

import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.core.formula_engine.registry import (
    delete, get_by_id, get_by_output, list_all, register, update,
    require_mutable_model_version,
)
from app.core.formula_engine.dsl import (
    FormulaDslError, evaluate_expression, extract_dependencies,
)
from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.core.dependency_engine.visualizer import to_graph_view
from app.core.project_lifecycle import require_active_project
from app.db.database import get_db
from app.db.models.formula import FormulaRegistry as FormulaModel
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.user import User
from app.models.schemas import DependencyGraphView, FormulaDefinition, FormulaUpdateRequest
from app.services import access
from app.services.common import ServiceError

router = APIRouter(prefix="/formulas", tags=["formulas"])
CurrentUser = Annotated[User, Depends(get_current_user)]


class ExpressionValidationRequest(BaseModel):
    expression_text: str = Field(min_length=1, max_length=10_000)
    model_version_id: str | None = None
    output_variable: str | None = None


class FormulaPreviewRequest(BaseModel):
    expression_text: str = Field(min_length=1, max_length=10_000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    prior_inputs: dict[str, Any] = Field(default_factory=dict)


def _dsl_http_error(error: FormulaDslError) -> ServiceError:
    return ServiceError(422, error.code, error.message, error.as_dict())


def _known_names(db: Session, model_version_id: str) -> set[str]:
    variables = {
        row.variable_name for row in db.query(ModelVariableDefinition).filter(
            ModelVariableDefinition.model_version_id == model_version_id
        ).all()
    }
    outputs = {
        row.output_variable for row in db.query(FormulaModel).filter(
            FormulaModel.model_version_id == model_version_id,
            FormulaModel.deleted_at.is_(None),
        ).all()
    }
    return variables | outputs


@router.post(
    "/validate-expression",
    dependencies=[Depends(require_permissions("registries:read"))],
)
def validate_formula_expression(
    payload: ExpressionValidationRequest, user: CurrentUser, db: Session = Depends(get_db),
):
    try:
        info = extract_dependencies(payload.expression_text)
    except FormulaDslError as exc:
        raise _dsl_http_error(exc) from exc
    unknown: list[str] = []
    if payload.model_version_id:
        access.require_object_access(db, user, "model_version", payload.model_version_id)
        known = _known_names(db, payload.model_version_id)
        unknown = sorted(
            (set(info.dependencies) | set(info.prior_dependencies))
            - known
            - ({payload.output_variable} if payload.output_variable else set())
        )
    errors = [f"Unknown variable '{name}'." for name in unknown]
    return {
        "valid": not errors,
        "expression_text": payload.expression_text,
        "dependencies": info.dependencies,
        "prior_dependencies": info.prior_dependencies,
        "functions_used": info.functions_used,
        "unknown_variables": unknown,
        "errors": errors,
    }


@router.post(
    "/preview",
    dependencies=[Depends(require_permissions("registries:read"))],
)
def preview_formula(payload: FormulaPreviewRequest, user: CurrentUser):
    del user
    try:
        info = extract_dependencies(payload.expression_text)
        result = evaluate_expression(payload.expression_text, payload.inputs, payload.prior_inputs)
    except FormulaDslError as exc:
        raise _dsl_http_error(exc) from exc
    return {
        "valid": True,
        "result": result,
        "dependencies": info.dependencies,
        "inputs_used": {name: payload.inputs.get(name) for name in info.dependencies},
        "errors": [],
    }


@router.post(
    "/{formula_id}/run-tests",
    dependencies=[Depends(require_permissions("registries:read"))],
)
def run_formula_tests(formula_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    _authorize(db, user, formula_id)
    formula = db.query(FormulaModel).filter(
        FormulaModel.id == formula_id, FormulaModel.deleted_at.is_(None)
    ).first()
    if formula is None:
        raise HTTPException(status_code=404, detail="Formula not found.")
    results = []
    for index, case in enumerate(formula.test_cases or []):
        expected = case.get("expected_output")
        tolerance = float(case.get("abs_tol", 0.0))
        actual = None
        error = None
        passed = False
        try:
            if formula.function_ref == "dsl" or formula.function_ref.startswith("dsl:"):
                actual = evaluate_expression(
                    formula.expression_text or "", case.get("inputs") or {},
                    case.get("prior_inputs") or {},
                )
            else:
                function = FORMULA_FUNCTIONS.get(formula.function_ref)
                if function is None:
                    raise ValueError(f"Function '{formula.function_ref}' is not registered.")
                actual = function(**(case.get("inputs") or {}))
            passed = math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance)
        except Exception as exc:  # noqa: BLE001 - each governed test returns its own error
            error = str(exc)
        abs_diff = abs(float(actual) - float(expected)) if error is None else None
        results.append({
            "name": case.get("name") or f"Test {index + 1}",
            "expected_output": expected,
            "actual_output": actual,
            "abs_diff": abs_diff,
            "abs_tol": tolerance,
            "passed": passed,
            "error": error,
        })
    passed_count = sum(1 for result in results if result["passed"])
    return {
        "formula_id": formula.id,
        "all_passed": bool(results) and passed_count == len(results),
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": results,
    }


def _authorize(db: Session, user: User, formula_id: str, write: bool = False) -> None:
    """404 for formulas of projects the user cannot access (and for missing formulas)."""
    access.require_object_access(db, user, "formula", formula_id, write=write)


@router.get("/", response_model=list[FormulaDefinition])
def list_formulas(
    user: CurrentUser,
    category: str | None = Query(None),
    product: str | None = Query(None),
    db: Session = Depends(get_db),
):
    formulas = list_all(db, category=category, product=product)
    allowed = access.accessible_project_ids(db, user)
    if allowed is None:
        return formulas
    visible = []
    for formula in formulas:
        project_id = access.project_id_of(db, "formula", formula.id)
        if project_id == access.GLOBAL or project_id in allowed:
            visible.append(formula)
    return visible


@router.post(
    "/",
    response_model=FormulaDefinition,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def create_formula(formula: FormulaDefinition, user: CurrentUser, db: Session = Depends(get_db)):
    if formula.model_version_id:
        project_id = access.project_id_of(db, "model_version", formula.model_version_id)
        if not project_id or project_id == access.GLOBAL:
            raise HTTPException(status_code=404, detail="Model version not found.")
        access.require_project_access(db, user, project_id, write=True)
        require_active_project(db, project_id)
        require_mutable_model_version(db, formula.model_version_id)
    existing = get_by_output(db, formula.output_variable)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Formula for output '{formula.output_variable}' already exists.",
        )
    return register(db, formula, user.id)


@router.get("/{formula_id}", response_model=FormulaDefinition)
def get_formula(formula_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    _authorize(db, user, formula_id)
    return get_by_id(db, formula_id)


@router.get("/{formula_id}/dependencies", response_model=DependencyGraphView)
def get_dependency_graph(formula_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    _authorize(db, user, formula_id)
    return to_graph_view([get_by_id(db, formula_id)])


@router.put(
    "/{formula_id}",
    response_model=FormulaDefinition,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def update_formula(formula_id: str, updates: FormulaUpdateRequest, user: CurrentUser, db: Session = Depends(get_db)):
    _authorize(db, user, formula_id, write=True)
    update_data = updates.model_dump(exclude_unset=True)
    f = update(db, formula_id, update_data, user.id)
    if f is None:
        raise HTTPException(status_code=404, detail="Formula not found.")
    return f


@router.delete(
    "/{formula_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def delete_formula(formula_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    _authorize(db, user, formula_id, write=True)
    success = delete(db, formula_id, user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Formula not found.")
