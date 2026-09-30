"""Scenario sets, scenario detail and comparison (contract §E.4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path, require_permissions
from app.db.database import get_db
from app.db.models.user import User
from app.services import access, catalog_service
from app.services.common import bad_request

router = APIRouter(tags=["scenarios"])
Reader = Annotated[User, Depends(require_permissions("projects:read"))]
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
ScenarioReader = Annotated[User, Depends(authorize_path("scenario", "scenario_id", "projects:read"))]


@router.get("/projects/{project_id}/scenario-sets")
def list_scenario_sets(project_id: str, user: ProjectReader, db: Session = Depends(get_db)):
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
    baseline_project = access.require_object_access(db, user, "scenario", baseline_id)
    compare_project = access.require_object_access(db, user, "scenario", compare_id)
    if baseline_project != compare_project:
        raise bad_request("Scenarios from different projects cannot be compared.")
    return catalog_service.compare_scenarios(db, baseline_id, compare_id)


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, user: ScenarioReader, db: Session = Depends(get_db)):
    del user
    return catalog_service.get_scenario(db, scenario_id)
