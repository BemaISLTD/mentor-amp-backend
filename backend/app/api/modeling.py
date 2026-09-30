"""Models, model versions, formula groups and formula detail (contract §E.2, §E.5)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import require_permissions
from app.db.database import get_db
from app.db.models.user import User
from app.services import catalog_service

router = APIRouter(tags=["models"])
Reader = Annotated[User, Depends(require_permissions("projects:read"))]


@router.get("/projects/{project_id}/models")
def list_models(
    project_id: str,
    user: Reader,
    product_code: str | None = Query(None),
    status: str | None = Query(None),
    owner: Literal["me", "others"] | None = Query(None),
    search: str | None = Query(None),
    sort: Literal["updated_desc", "name_asc"] = Query("updated_desc"),
    db: Session = Depends(get_db),
):
    return catalog_service.list_models(
        db, project_id, user.id, product_code=product_code, status=status, owner=owner,
        search=search, sort=sort,
    )


@router.get("/models/{model_id}")
def get_model(model_id: str, user: Reader, db: Session = Depends(get_db)):
    return catalog_service.get_model(db, model_id, user.id)


@router.get("/model-versions/{model_version_id}/structure")
def model_version_structure(model_version_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return catalog_service.model_version_structure(db, model_version_id)


@router.get("/model-versions/{model_version_id}/published-outputs")
def published_outputs(model_version_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return catalog_service.published_outputs(db, model_version_id)


@router.get("/model-versions/{model_version_id}/formula-groups")
def formula_groups(model_version_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return catalog_service.formula_groups(db, model_version_id)


@router.get("/formulas/{formula_id}/detail")
def formula_detail(formula_id: str, user: Reader, db: Session = Depends(get_db)):
    del user
    return catalog_service.formula_detail(db, formula_id)
