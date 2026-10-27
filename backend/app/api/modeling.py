"""Models, model versions, formula groups and formula detail (contract §E.2, §E.5)."""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_path, get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.db.models.formula import FormulaRegistry
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.modeling import FormulaGroup
from app.db.models.variable import VariableRegistry
from app.models.schemas import FormulaDefinition
from app.core.formula_engine.registry import register as register_formula
from app.core.model_lifecycle import require_mutable_model_version
from app.services.common import ServiceError
from app.services import access, catalog_service, model_version_service, variable_definition_service

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


def _authorize_version_review(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> User:
    access.require_object_access(db, user, "model_version", request.path_params["model_version_id"])
    user_roles = {role.name for role in getattr(user, "roles", [])}
    user_permissions = {
        p.name for role in getattr(user, "roles", []) for p in getattr(role, "permissions", [])
    }
    can_review = bool(
        "models:approve" in user_permissions
        or "admin" in user_roles
    )
    if not can_review:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to review model versions.",
        )
    return user


VersionReviewer = Annotated[User, Depends(_authorize_version_review)]


class ModelReviewRequest(BaseModel):
    reason: str | None = None


class VariableDefinitionUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    unit: str | None = None
    required: bool | None = None
    default_value: Any = None
    source: dict[str, Any] | None = None
    allow_scenario_override: bool | None = None


class PublishedOutputCreate(BaseModel):
    variable_name: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(None, max_length=255)
    unit: str | None = Field(None, max_length=50)
    dimension: str | None = Field(None, max_length=255)
    aggregation: str | None = Field("sum", max_length=20)
    description: str | None = None
    sort_order: int | None = 0


class PublishedOutputUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=255)
    unit: str | None = Field(None, max_length=50)
    dimension: str | None = Field(None, max_length=255)
    aggregation: str | None = Field(None, max_length=20)
    description: str | None = None
    sort_order: int | None = None


class ModelFormulaCreate(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    output_variable: str = Field(min_length=1, max_length=255)
    function_ref: str = Field("dsl", min_length=1, max_length=255)
    dependencies: list[str] | None = None
    category: str = ""
    product_applicability: list[str] = Field(default_factory=list)
    basis_applicability: list[str] = Field(default_factory=list)
    version: str = "v1"
    group_id: str | None = None
    expression_text: str | None = Field(None, max_length=10_000)
    explanation: str | None = None
    unit: str | None = Field(None, max_length=50)
    illustrative: bool = False
    effective_date: date | None = None
    owner_user_id: str | None = None
    test_cases: list[dict[str, Any]] = Field(default_factory=list)



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


@router.delete("/model-versions/{model_version_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_model_version(
    model_version_id: str, user: VersionWriter, db: Session = Depends(get_db)
) -> Response:
    model_version_service.archive_version(db, model_version_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/model-versions/{model_version_id}/validate")
def validate_model_version(
    model_version_id: str, user: VersionWriter, db: Session = Depends(get_db)
):
    return model_version_service.validate_version(db, model_version_id, user)


@router.post("/model-versions/{model_version_id}/approve")
def approve_model_version(
    model_version_id: str,
    user: VersionReviewer,
    payload: ModelReviewRequest | None = None,
    db: Session = Depends(get_db),
):
    reason = payload.reason if payload else None
    return model_version_service.approve_version(db, model_version_id, user, reason)


@router.post("/model-versions/{model_version_id}/reject")
def reject_model_version(
    model_version_id: str,
    user: VersionReviewer,
    payload: ModelReviewRequest | None = None,
    db: Session = Depends(get_db),
):
    reason = payload.reason if payload else None
    return model_version_service.reject_version(db, model_version_id, user, reason)


@router.post("/model-versions/{model_version_id}/publish")
def publish_model_version(
    model_version_id: str, user: VersionWriter, db: Session = Depends(get_db)
):
    return model_version_service.publish_version(db, model_version_id, user)


@router.post("/model-versions/{model_version_id}/rollback")
def rollback_model_version(
    model_version_id: str, user: VersionReviewer, db: Session = Depends(get_db)
):
    return model_version_service.rollback_version(db, model_version_id, user)


@router.get("/model-versions/{model_version_id}/dependency-analysis")
def model_version_dependency_analysis(
    model_version_id: str,
    user: VersionReader,
    variable: str | None = Query(None),
    db: Session = Depends(get_db),
):
    del user
    return model_version_service.dependency_analysis(db, model_version_id, variable)


@router.get("/model-versions/{model_version_id}/compare/{other_version_id}")
def compare_model_versions(
    model_version_id: str,
    other_version_id: str,
    user: VersionReader,
    db: Session = Depends(get_db),
):
    access.require_object_access(db, user, "model_version", other_version_id)
    return model_version_service.compare_versions(db, model_version_id, other_version_id)


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


@router.post(
    "/model-versions/{model_version_id}/formulas",
    response_model=FormulaDefinition,
    status_code=status.HTTP_201_CREATED,
)
def create_model_version_formula(
    model_version_id: str,
    payload: ModelFormulaCreate,
    user: VersionWriter,
    db: Session = Depends(get_db),
):
    require_mutable_model_version(db, model_version_id)
    defined_output = db.query(ModelVariableDefinition).filter(
        ModelVariableDefinition.model_version_id == model_version_id,
        ModelVariableDefinition.variable_name == payload.output_variable,
    ).first()
    registered_output = db.query(VariableRegistry).filter(
        VariableRegistry.name == payload.output_variable,
        VariableRegistry.deleted_at.is_(None),
    ).first()
    if defined_output is None or registered_output is None:
        raise ServiceError(
            422, "FORMULA_OUTPUT_NOT_DEFINED",
            f"Output variable '{payload.output_variable}' is not defined in this model version.",
        )
    if payload.owner_user_id and db.get(User, payload.owner_user_id) is None:
        raise ServiceError(
            422, "FORMULA_OWNER_NOT_FOUND",
            f"Formula owner '{payload.owner_user_id}' does not exist.",
        )
    existing = db.query(FormulaRegistry).filter(
        FormulaRegistry.model_version_id == model_version_id,
        FormulaRegistry.output_variable == payload.output_variable,
        FormulaRegistry.deleted_at.is_(None),
    ).first()
    if existing:
        raise ServiceError(
            409, "FORMULA_OUTPUT_EXISTS",
            f"A formula for output '{payload.output_variable}' already exists in this model version.",
        )
    group = None
    if payload.group_id:
        group = db.query(FormulaGroup).filter(
            FormulaGroup.id == payload.group_id,
            FormulaGroup.model_version_id == model_version_id,
        ).first()
        if group is None:
            raise ServiceError(422, "INVALID_FORMULA_GROUP", "Formula group is not in this model version.")
    else:
        group = db.query(FormulaGroup).filter(
            FormulaGroup.model_version_id == model_version_id
        ).order_by(FormulaGroup.sort_order, FormulaGroup.name).first()
        if group is None:
            group = FormulaGroup(
                model_version_id=model_version_id,
                name="General",
                description="Default Formula Studio group",
                lineage_state="local",
                version_label="v1",
                sort_order=0,
            )
            db.add(group)
            db.flush()
    values = payload.model_dump(exclude={"id"})
    values["id"] = payload.id or str(uuid.uuid4())
    values["model_version_id"] = model_version_id
    values["group_id"] = group.id
    values["dependencies"] = payload.dependencies or []
    return register_formula(db, FormulaDefinition(**values), user.id)


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


@router.delete("/model-versions/{model_version_id}/variables/{variable_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_version_variable(
    model_version_id: str,
    variable_name: str,
    user: VersionWriter,
    db: Session = Depends(get_db),
) -> Response:
    variable_definition_service.delete_definition(db, model_version_id, variable_name, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/model-versions/{model_version_id}/published-outputs", status_code=status.HTTP_201_CREATED)
def create_published_output(
    model_version_id: str,
    payload: PublishedOutputCreate,
    user: VersionWriter,
    db: Session = Depends(get_db),
):
    return model_version_service.create_published_output(
        db, model_version_id, payload.model_dump(exclude_unset=True), user
    )


@router.patch("/model-versions/{model_version_id}/published-outputs/{variable_name}")
def update_published_output(
    model_version_id: str,
    variable_name: str,
    payload: PublishedOutputUpdate,
    user: VersionWriter,
    db: Session = Depends(get_db),
):
    return model_version_service.update_published_output(
        db, model_version_id, variable_name, payload.model_dump(exclude_unset=True), user
    )


@router.delete("/model-versions/{model_version_id}/published-outputs/{variable_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_published_output(
    model_version_id: str,
    variable_name: str,
    user: VersionWriter,
    db: Session = Depends(get_db),
) -> Response:
    model_version_service.delete_published_output(db, model_version_id, variable_name, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

