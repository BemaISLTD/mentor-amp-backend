"""API endpoints for the Variable Registry — the global SEMANTIC variable catalog.

The catalog records what a variable *is*. How a model resolves it (source, default, override
policy) lives in model-version definitions (``/v1/model-versions/{id}/variables``), which are
project-scoped. Catalog entries are never used to resolve values; changing them is reserved for
platform administrators.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.core.variable_registry.registry import (
    delete,
    get_by_name,
    list_all,
    register,
    update,
)
from app.db.database import get_db
from app.db.models.user import User
from app.models.schemas import VariableDefinition

router = APIRouter(prefix="/variables", tags=["variables"])
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminOnly = [Depends(require_roles("admin"))]


@router.get("/", response_model=list[VariableDefinition])
def list_variables(
    product: str | None = Query(None),
    kind: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List all registered variables, optionally filtered."""
    return list_all(db, product=product, kind=kind)


@router.post("/", response_model=VariableDefinition, status_code=status.HTTP_201_CREATED, dependencies=AdminOnly)
def create_variable(variable: VariableDefinition, user: CurrentUser, db: Session = Depends(get_db)):
    """Register a new variable."""
    existing = get_by_name(db, variable.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Variable '{variable.name}' already exists.",
        )
    return register(db, variable, user.id)


@router.get("/{name}", response_model=VariableDefinition)
def get_variable(name: str, db: Session = Depends(get_db)):
    """Get a variable by name."""
    var = get_by_name(db, name)
    if var is None:
        raise HTTPException(status_code=404, detail=f"Variable '{name}' not found.")
    return var


@router.put("/{name}", response_model=VariableDefinition, dependencies=AdminOnly)
def update_variable(name: str, updates: dict, user: CurrentUser, db: Session = Depends(get_db)):
    """Update a variable's fields."""
    var = update(db, name, updates, user.id)
    if var is None:
        raise HTTPException(status_code=404, detail=f"Variable '{name}' not found.")
    return var


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT, dependencies=AdminOnly)
def delete_variable(name: str, user: CurrentUser, db: Session = Depends(get_db)):
    """Delete a variable by name."""
    success = delete(db, name, user.id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Variable '{name}' not found.")
