"""API endpoints for the Variable Registry."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.variable_registry.registry import (
    delete,
    get_by_name,
    list_all,
    register,
    update,
)
from app.db.database import get_db
from app.models.schemas import VariableDefinition

router = APIRouter(prefix="/variables", tags=["variables"])


@router.get("/", response_model=list[VariableDefinition])
def list_variables(
    product: str | None = Query(None),
    kind: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List all registered variables, optionally filtered."""
    return list_all(db, product=product, kind=kind)


@router.post("/", response_model=VariableDefinition, status_code=status.HTTP_201_CREATED)
def create_variable(variable: VariableDefinition, db: Session = Depends(get_db)):
    """Register a new variable."""
    existing = get_by_name(db, variable.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Variable '{variable.name}' already exists.",
        )
    return register(db, variable)


@router.get("/{name}", response_model=VariableDefinition)
def get_variable(name: str, db: Session = Depends(get_db)):
    """Get a variable by name."""
    var = get_by_name(db, name)
    if var is None:
        raise HTTPException(status_code=404, detail=f"Variable '{name}' not found.")
    return var


@router.put("/{name}", response_model=VariableDefinition)
def update_variable(name: str, updates: dict, db: Session = Depends(get_db)):
    """Update a variable's fields."""
    var = update(db, name, updates)
    if var is None:
        raise HTTPException(status_code=404, detail=f"Variable '{name}' not found.")
    return var


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variable(name: str, db: Session = Depends(get_db)):
    """Delete a variable by name."""
    success = delete(db, name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Variable '{name}' not found.")
