"""API endpoints for the Formula Registry."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.formula_engine.registry import (
    delete, get_by_id, get_by_output, list_all, register, update,
)
from app.core.dependency_engine.visualizer import to_graph_view
from app.db.database import get_db
from app.models.schemas import DependencyGraphView, FormulaDefinition

router = APIRouter(prefix="/formulas", tags=["formulas"])


@router.get("/", response_model=list[FormulaDefinition])
def list_formulas(
    category: str | None = Query(None),
    product: str | None = Query(None),
    db: Session = Depends(get_db),
):
    return list_all(db, category=category, product=product)


@router.post("/", response_model=FormulaDefinition, status_code=status.HTTP_201_CREATED)
def create_formula(formula: FormulaDefinition, db: Session = Depends(get_db)):
    existing = get_by_output(db, formula.output_variable)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Formula for output '{formula.output_variable}' already exists.",
        )
    return register(db, formula)


@router.get("/{formula_id}", response_model=FormulaDefinition)
def get_formula(formula_id: str, db: Session = Depends(get_db)):
    f = get_by_id(db, formula_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Formula not found.")
    return f


@router.get("/{formula_id}/dependencies", response_model=DependencyGraphView)
def get_dependency_graph(formula_id: str, db: Session = Depends(get_db)):
    f = get_by_id(db, formula_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Formula not found.")
    return to_graph_view([f])


@router.put("/{formula_id}", response_model=FormulaDefinition)
def update_formula(formula_id: str, updates: dict, db: Session = Depends(get_db)):
    f = update(db, formula_id, updates)
    if f is None:
        raise HTTPException(status_code=404, detail="Formula not found.")
    return f


@router.delete("/{formula_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_formula(formula_id: str, db: Session = Depends(get_db)):
    success = delete(db, formula_id)
    if not success:
        raise HTTPException(status_code=404, detail="Formula not found.")
