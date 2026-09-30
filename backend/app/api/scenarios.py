"""Scenario sets, scenario detail and comparison (contract §E.4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import require_permissions
from app.db.database import get_db
from app.db.models.user import User
from app.services import catalog_service

router = APIRouter(tags=["scenarios"])
Reader = Annotated[User, Depends(require_permissions("projects:read"))]


@router.get("/projects/{project_id}/scenario-sets")
def list_scenario_sets(project_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return catalog_service.list_scenario_sets(db, project_id)


# Declared before /scenarios/{scenario_id} so "compare" is not read as an ID.
@router.get("/scenarios/compare")
def compare_scenarios(
    user: Reader,
    baseline_id: str = Query(...),
    compare_id: str = Query(...),
    db: Session = Depends(get_db),
):
    del user
    return catalog_service.compare_scenarios(db, baseline_id, compare_id)


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return catalog_service.get_scenario(db, scenario_id)
