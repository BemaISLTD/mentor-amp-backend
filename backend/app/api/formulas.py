"""API endpoints for the Formula Registry.

Formulas that belong to a model version are project-owned: reading or changing them requires
access to that project. Formulas without a model version are the shared legacy catalog.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.core.formula_engine.registry import (
    delete, get_by_id, get_by_output, list_all, register, update,
)
from app.core.dependency_engine.visualizer import to_graph_view
from app.db.database import get_db
from app.db.models.user import User
from app.models.schemas import DependencyGraphView, FormulaDefinition
from app.services import access

router = APIRouter(prefix="/formulas", tags=["formulas"])
CurrentUser = Annotated[User, Depends(get_current_user)]


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
def update_formula(formula_id: str, updates: dict, user: CurrentUser, db: Session = Depends(get_db)):
    _authorize(db, user, formula_id, write=True)
    f = update(db, formula_id, updates, user.id)
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
