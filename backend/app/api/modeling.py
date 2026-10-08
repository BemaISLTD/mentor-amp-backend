"""Models, model versions, formula groups and formula detail (contract §E.2, §E.5)."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path
from app.db.database import get_db
from app.db.models.user import User
from app.services import catalog_service, model_version_service, variable_definition_service

router = APIRouter(tags=["models"])
ProjectReader = Annotated[User, Depends(authorize_path("project", "project_id", "projects:read"))]
ProjectWriter = Annotated[
    User, Depends(authorize_path("project", "project_id", "registries:write", write=True))
]
ModelReader = Annotated[User, Depends(authorize_path("model", "model_id", "projects:read"))]
ModelWriter = Annotated[User, Depends(authorize_path("model", "model_id", "registries:write", write=True))]
VersionReader = Annotated[
    User, Depends(authorize_path("model_version", "model_version_id", "projects:read"))
]
VersionWriter = Annotated[
    User, Depends(authorize_path("model_version", "model_version_id", "registries:write", write=True))
]
FormulaReader = Annotated[User, Depends(authorize_path("formula", "formula_id", "projects:read"))]


class VariableDefinitionUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    unit: str | None = None
    required: bool | None = None
    default_value: Any = None
    source: dict[str, Any] | None = None
    allow_scenario_override: bool | None = None


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    product_code: str = Field(min_length=1, max_length=50)
    description: str | None = None
    owner_user_id: str | None = None


class ModelVersionCreate(BaseModel):
    version_label: str | None = Field(None, min_length=1, max_length=30)
    parent_version_id: str | None = None
    block_name: str | None = None
    profile_name: str | None = None
    basis: str | None = Field(None, max_length=100)
    methodology: str | None = Field(None, max_length=100)
    illustrative: bool | None = None
    notes: str | None = None
    change_summary: str | None = None
    configuration: dict[str, Any] | None = None


class ModelVersionUpdate(BaseModel):
    version_label: str | None = Field(None, min_length=1, max_length=30)
    block_name: str | None = None
    profile_name: str | None = None
    basis: str | None = Field(None, min_length=1, max_length=100)
    methodology: str | None = Field(None, max_length=100)
    illustrative: bool | None = None
    notes: str | None = None
    change_summary: str | None = None
    configuration: dict[str, Any] | None = None


@router.get("/projects/{project_id}/models")
def list_models(
    project_id: str,
    user: ProjectReader,
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


@router.post("/projects/{project_id}/models", status_code=status.HTTP_201_CREATED)
def create_model(
    project_id: str, payload: ModelCreate, user: ProjectWriter, db: Session = Depends(get_db)
):
    return model_version_service.create_model(db, project_id, payload.model_dump(), user)


@router.get("/models/{model_id}")
def get_model(model_id: str, user: ModelReader, db: Session = Depends(get_db)):
    return catalog_service.get_model(db, model_id, user.id)


@router.get("/models/{model_id}/versions")
def list_model_versions(model_id: str, user: ModelReader, db: Session = Depends(get_db)):
    del user
    return {"versions": model_version_service.list_versions(db, model_id)}


@router.post("/models/{model_id}/versions", status_code=status.HTTP_201_CREATED)
def create_model_version(
    model_id: str, payload: ModelVersionCreate, user: ModelWriter, db: Session = Depends(get_db)
):
    return model_version_service.create_version(db, model_id, payload.model_dump(exclude_unset=True), user)


@router.patch("/model-versions/{model_version_id}")
def update_model_version(
    model_version_id: str, payload: ModelVersionUpdate, user: VersionWriter,
    db: Session = Depends(get_db),
):
    return model_version_service.update_version(
        db, model_version_id, payload.model_dump(exclude_unset=True), user
    )


@router.post("/model-versions/{model_version_id}/publish")
def publish_model_version(
    model_version_id: str, user: VersionWriter, db: Session = Depends(get_db)
):
    return model_version_service.publish_version(db, model_version_id, user)


@router.delete("/model-versions/{model_version_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_model_version(
    model_version_id: str, user: VersionWriter, db: Session = Depends(get_db)
) -> Response:
    model_version_service.archive_version(db, model_version_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/model-versions/{model_version_id}/structure")
def model_version_structure(model_version_id: str, user: VersionReader, db: Session = Depends(get_db)):
    del user
    return catalog_service.model_version_structure(db, model_version_id)


@router.get("/model-versions/{model_version_id}/published-outputs")
def published_outputs(model_version_id: str, user: VersionReader, db: Session = Depends(get_db)):
    del user
    return catalog_service.published_outputs(db, model_version_id)


@router.get("/model-versions/{model_version_id}/formula-groups")
def formula_groups(model_version_id: str, user: VersionReader, db: Session = Depends(get_db)):
    del user
    return catalog_service.formula_groups(db, model_version_id)


@router.get("/formulas/{formula_id}/detail")
def formula_detail(formula_id: str, user: FormulaReader, db: Session = Depends(get_db)):
    del user
    return catalog_service.formula_detail(db, formula_id)


@router.get("/model-versions/{model_version_id}/variables")
def model_version_variables(model_version_id: str, user: VersionReader, db: Session = Depends(get_db)):
    """The model version's own variable definitions (the only source of variable resolution)."""
    del user
    return variable_definition_service.list_definitions(db, model_version_id)


@router.patch("/model-versions/{model_version_id}/variables/{variable_name}")
def update_model_version_variable(
    model_version_id: str,
    variable_name: str,
    payload: VariableDefinitionUpdate,
    user: VersionWriter,
    db: Session = Depends(get_db),
):
    return variable_definition_service.update_definition(
        db, model_version_id, variable_name, payload.model_dump(exclude_unset=True), user,
    )
