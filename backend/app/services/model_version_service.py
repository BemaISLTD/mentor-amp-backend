"""Governed model and whole-model version lifecycle."""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.project_lifecycle import require_active_project
from app.db.models.formula import FormulaRegistry
from app.db.models.formula_dependency import FormulaDependency
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.modeling import FormulaGroup, Model, ModelPublishedOutput, ModelVersion
from app.services import access
from app.services.common import ServiceError, conflict, iso, not_found, now_utc


def _model_state(row: Model) -> dict[str, Any]:
    return {
        "project_id": row.project_id, "name": row.name, "product_code": row.product_code,
        "description": row.description, "owner_user_id": row.owner_user_id, "status": row.status,
        "created_by": row.created_by, "updated_by": row.updated_by,
        "deleted_by": row.deleted_by, "deleted_at": iso(row.deleted_at),
    }


def _version_state(row: ModelVersion) -> dict[str, Any]:
    return {
        "model_id": row.model_id, "version_label": row.version_label,
        "version_number": row.version_number, "parent_version_id": row.parent_version_id,
        "block_name": row.block_name, "profile_name": row.profile_name, "basis": row.basis,
        "methodology": row.methodology, "status": row.status, "is_current": row.is_current,
        "illustrative": row.illustrative, "notes": row.notes,
        "change_summary": row.change_summary, "configuration": row.configuration,
        "created_by": row.created_by, "updated_by": row.updated_by,
        "published_by": row.published_by, "published_at": iso(row.published_at),
        "deleted_by": row.deleted_by, "deleted_at": iso(row.deleted_at),
    }


def serialize_model(row: Model) -> dict[str, Any]:
    return {"id": row.id, **_model_state(row), "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at)}


def serialize_version(row: ModelVersion) -> dict[str, Any]:
    return {"id": row.id, **_version_state(row), "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at)}


def create_model(db: Session, project_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    access.require_project_access(db, user, project_id, write=True)
    require_active_project(db, project_id)
    duplicate = db.query(Model).filter(
        Model.project_id == project_id, Model.name == payload["name"]
    ).first()
    if duplicate:
        raise conflict(f"Model '{payload['name']}' already exists in this project.")
    row = Model(
        project_id=project_id, name=payload["name"], product_code=payload["product_code"],
        description=payload.get("description"), owner_user_id=payload.get("owner_user_id") or user.id,
        status="draft", created_by=user.id, updated_by=user.id,
    )
    db.add(row)
    db.flush()
    record_audit(db, actor_user_id=user.id, action="model.created", entity_type="model",
                 entity_id=row.id, after_state=_model_state(row))
    db.commit()
    db.refresh(row)
    return serialize_model(row)


def create_version(db: Session, model_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    model = db.query(Model).filter(Model.id == model_id, Model.deleted_at.is_(None)).first()
    if model is None:
        raise not_found(f"Model '{model_id}' not found.")
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)
    parent = None
    parent_id = payload.get("parent_version_id")
    if parent_id:
        parent = db.query(ModelVersion).filter(
            ModelVersion.id == parent_id, ModelVersion.model_id == model_id,
            ModelVersion.deleted_at.is_(None),
        ).first()
        if parent is None:
            raise not_found(f"Parent model version '{parent_id}' not found.")
    number = (db.query(func.max(ModelVersion.version_number)).filter(
        ModelVersion.model_id == model_id
    ).scalar() or 0) + 1
    label = payload.get("version_label") or f"v{number}"
    if db.query(ModelVersion).filter(
        ModelVersion.model_id == model_id, ModelVersion.version_label == label
    ).first():
        raise conflict(f"Model version '{label}' already exists.")
    source = parent
    row = ModelVersion(
        model_id=model_id, version_label=label, version_number=number,
        parent_version_id=parent.id if parent else None,
        block_name=payload.get("block_name", source.block_name if source else None),
        profile_name=payload.get("profile_name", source.profile_name if source else None),
        basis=payload.get("basis") or (source.basis if source else None),
        methodology=payload.get("methodology", source.methodology if source else None),
        illustrative=payload.get("illustrative", source.illustrative if source else False),
        notes=payload.get("notes"), change_summary=payload.get("change_summary"),
        configuration=(payload.get("configuration")
                       or (dict(source.configuration or {}) if source else {})),
        status="draft", is_current=False, created_by=user.id, updated_by=user.id,
    )
    if not row.basis:
        raise ServiceError(422, "VALIDATION_ERROR", "A model version requires a basis.")
    db.add(row)
    db.flush()
    if parent:
        _clone_version_children(db, parent, row, user.id)
    record_audit(db, actor_user_id=user.id, action="model_version.created",
                 entity_type="model_version", entity_id=row.id, after_state=_version_state(row),
                 context={"cloned_from": parent.id if parent else None})
    db.commit()
    db.refresh(row)
    return serialize_version(row)


def _clone_version_children(db: Session, parent: ModelVersion, target: ModelVersion, actor_id: str) -> None:
    group_ids: dict[str, str] = {}
    for source in db.query(FormulaGroup).filter(FormulaGroup.model_version_id == parent.id).all():
        copy = FormulaGroup(
            model_version_id=target.id, name=source.name, description=source.description,
            lineage_state="inherited", version_label=source.version_label, sort_order=source.sort_order,
        )
        db.add(copy)
        db.flush()
        group_ids[source.id] = copy.id
    for source in db.query(FormulaRegistry).filter(
        FormulaRegistry.model_version_id == parent.id, FormulaRegistry.deleted_at.is_(None)
    ).all():
        copy = FormulaRegistry(
            name=source.name, output_variable=source.output_variable, function_ref=source.function_ref,
            category=source.category, product_applicability=list(source.product_applicability or []),
            basis_applicability=list(source.basis_applicability or []), version=source.version,
            status="draft", created_by=actor_id, updated_by=actor_id, model_version_id=target.id,
            group_id=group_ids.get(source.group_id), expression_text=source.expression_text,
            explanation=source.explanation, unit=source.unit, illustrative=source.illustrative,
        )
        db.add(copy)
        db.flush()
        for dependency in source.dependencies:
            copy.dependencies.append(FormulaDependency(depends_on_variable=dependency.depends_on_variable))
    for source in db.query(ModelVariableDefinition).filter(
        ModelVariableDefinition.model_version_id == parent.id
    ).all():
        db.add(ModelVariableDefinition(
            model_version_id=target.id, variable_name=source.variable_name,
            display_name=source.display_name, description=source.description, kind=source.kind,
            data_type=source.data_type, unit=source.unit, required=source.required,
            default_value=source.default_value, source=dict(source.source or {}),
            allow_scenario_override=source.allow_scenario_override, version=source.version,
            created_by=actor_id, updated_by=actor_id,
        ))
    for source in db.query(ModelPublishedOutput).filter(
        ModelPublishedOutput.model_version_id == parent.id
    ).all():
        db.add(ModelPublishedOutput(
            model_version_id=target.id, variable_name=source.variable_name,
            display_name=source.display_name, unit=source.unit, dimension=source.dimension,
            aggregation=source.aggregation, description=source.description, sort_order=source.sort_order,
        ))


def list_versions(db: Session, model_id: str) -> list[dict[str, Any]]:
    return [serialize_version(row) for row in db.query(ModelVersion).filter(
        ModelVersion.model_id == model_id, ModelVersion.deleted_at.is_(None)
    ).order_by(ModelVersion.version_number.desc()).all()]


def update_version(db: Session, version_id: str, payload: dict[str, Any], user: Any) -> dict[str, Any]:
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)
    if row.status in {"published", "approved"}:
        raise conflict("Published model versions are immutable; create a new version.")
    before = _version_state(row)
    for field in (
        "version_label", "block_name", "profile_name", "basis", "methodology", "illustrative",
        "notes", "change_summary", "configuration",
    ):
        if field in payload:
            setattr(row, field, payload[field])
    row.updated_by = user.id
    db.flush()
    record_audit(db, actor_user_id=user.id, action="model_version.updated",
                 entity_type="model_version", entity_id=row.id,
                 before_state=before, after_state=_version_state(row))
    db.commit()
    db.refresh(row)
    return serialize_version(row)


def publish_version(db: Session, version_id: str, user: Any) -> dict[str, Any]:
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)
    before = _version_state(row)
    db.query(ModelVersion).filter(
        ModelVersion.model_id == row.model_id, ModelVersion.id != row.id
    ).update({ModelVersion.is_current: False}, synchronize_session=False)
    # Approved is the existing runnable lifecycle state. ``published_at`` and ``is_current``
    # express publication without inventing a status that the execution gate rejects.
    row.status = "approved"
    row.is_current = True
    row.published_by = user.id
    row.published_at = now_utc()
    row.updated_by = user.id
    model.status = "active"
    model.updated_by = user.id
    db.flush()
    record_audit(db, actor_user_id=user.id, action="model_version.published",
                 entity_type="model_version", entity_id=row.id,
                 before_state=before, after_state=_version_state(row))
    db.commit()
    db.refresh(row)
    return serialize_version(row)


def archive_version(db: Session, version_id: str, user: Any) -> None:
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)
    before = _version_state(row)
    row.deleted_at = now_utc()
    row.deleted_by = user.id
    row.updated_by = user.id
    row.status = "archived"
    row.is_current = False
    db.flush()
    record_audit(db, actor_user_id=user.id, action="model_version.archived",
                 entity_type="model_version", entity_id=row.id,
                 before_state=before, after_state=_version_state(row),
                 context={"deletion": "soft"})
    db.commit()
