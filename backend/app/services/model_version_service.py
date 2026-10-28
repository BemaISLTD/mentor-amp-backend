"""Governed model and whole-model version lifecycle."""

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.model_fingerprint import compute_model_definition_fingerprint
from app.core.formula_engine.dsl import FormulaDslError, extract_dependencies
from app.core.projection_engine.engine import order_formulas
from app.services.model_definition import to_formula_spec
from app.core.model_lifecycle import (
    APPROVED,
    DRAFT,
    NEEDS_REVIEW,
    PROTECTED_STATUSES,
    PUBLISHED,
    REJECTED,
    SUPERSEDED,
    VALIDATED,
    invalidate_model_validation,
    require_mutable_model_version,
    transition_model_version,
)
from app.core.project_lifecycle import require_active_project
from app.db.models.formula import FormulaRegistry
from app.db.models.formula_dependency import FormulaDependency
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.modeling import FormulaGroup, Model, ModelPublishedOutput, ModelVersion
from app.db.models.projection import ProjectionSet
from app.products.registry import register_all_products
from app.services import access, catalog_service
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
        "validated_by": row.validated_by, "validated_at": iso(row.validated_at),
        "validation_fingerprint": row.validation_fingerprint,
        "approved_by": row.approved_by, "approved_at": iso(row.approved_at),
        "approved_fingerprint": row.approved_fingerprint,
        "rejected_by": row.rejected_by, "rejected_at": iso(row.rejected_at),
        "rejection_reason": row.rejection_reason,
        "published_by": row.published_by, "published_at": iso(row.published_at),
        "last_material_modified_by": row.last_material_modified_by,
        "last_material_modified_at": iso(row.last_material_modified_at),
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
        last_material_modified_by=user.id, last_material_modified_at=now_utc(),
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
            effective_date=source.effective_date, owner_user_id=source.owner_user_id,
            test_cases=list(source.test_cases or []),
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
    require_mutable_model_version(db, version_id, for_update=True)
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).with_for_update().first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)
    before = _version_state(row)
    changed = False
    material_fields = {
        "block_name", "profile_name", "basis", "methodology", "illustrative",
        "notes", "configuration",
    }
    material_changed = False
    for field in (
        "version_label", "block_name", "profile_name", "basis", "methodology", "illustrative",
        "notes", "change_summary", "configuration",
    ):
        if field in payload and getattr(row, field) != payload[field]:
            setattr(row, field, payload[field])
            changed = True
            if field in material_fields:
                material_changed = True
    if changed:
        row.updated_by = user.id
        if material_changed:
            invalidate_model_validation(db, row.id, actor_id=user.id)
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

    # Concurrency lock on parent model
    db.query(Model).filter(Model.id == row.model_id).with_for_update().first()

    # P0: Publish must require independent approval (status == approved)
    if row.status != "approved":
        raise ServiceError(
            409, "MODEL_VERSION_NOT_APPROVED",
            f"Only approved model versions can be published; current status is '{row.status}'.",
        )

    # P0: Verify current definition matches approved fingerprint
    current_fingerprint = compute_model_definition_fingerprint(db, row.id)
    if not row.approved_fingerprint or row.approved_fingerprint != current_fingerprint:
        raise ServiceError(
            409, "MODEL_DEFINITION_CHANGED_SINCE_APPROVAL",
            "Model version definition differs from the approved definition; publication refused.",
        )

    before = _version_state(row)
    # Mark previous current version(s) as superseded / non-current
    current_versions = db.query(ModelVersion).filter(
        ModelVersion.model_id == row.model_id,
        ModelVersion.id != row.id,
        ModelVersion.is_current.is_(True),
    ).all()
    for prev in current_versions:
        prev.is_current = False
        prev.status = "superseded"
    if current_versions:
        db.flush()

    row.is_current = True
    row.status = PUBLISHED
    row.published_by = user.id
    row.published_at = now_utc()
    row.updated_by = user.id
    model.status = "active"
    model.updated_by = user.id
    db.flush()
    record_audit(db, actor_user_id=user.id, action="model_version.published",
                 entity_type="model_version", entity_id=row.id,
                 before_state=before, after_state=_version_state(row),
                 context={"approved_fingerprint": row.approved_fingerprint})
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
    if row.is_current:
        raise ServiceError(409, "CANNOT_ARCHIVE_CURRENT_VERSION", "Cannot archive the current active model version.")
    if row.status in {"approved", "published"}:
        raise ServiceError(409, "CANNOT_ARCHIVE_APPROVED_VERSION", "Cannot archive an approved or published model version; create a replacement version first.")
    in_use = db.query(ProjectionSet).filter(
        ProjectionSet.model_version_id == row.id,
        ProjectionSet.status != "archived",
    ).count()
    if in_use > 0:
        raise ServiceError(409, "MODEL_VERSION_IN_USE", f"Cannot archive model version '{row.version_label}': it is referenced by {in_use} active projection set(s).")
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


def validate_version(db: Session, version_id: str, user: Any) -> dict[str, Any]:
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).with_for_update().first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)

    # P0: Approved/current model versions must never go back to validated
    if row.status in PROTECTED_STATUSES or (row.is_current and row.published_at is not None):
        raise ServiceError(
            409, "MODEL_VERSION_IMMUTABLE",
            f"Model version '{row.version_label}' is in protected state '{row.status}' and cannot be revalidated.",
            {"model_version_id": row.id, "status": row.status},
        )

    register_all_products()
    formulas = db.query(FormulaRegistry).filter(
        FormulaRegistry.model_version_id == row.id,
        FormulaRegistry.deleted_at.is_(None),
    ).order_by(FormulaRegistry.output_variable).all()
    published = db.query(ModelPublishedOutput).filter(
        ModelPublishedOutput.model_version_id == row.id
    ).order_by(ModelPublishedOutput.sort_order, ModelPublishedOutput.variable_name).all()

    checks_result = catalog_service.model_version_checks(db, formulas, published, row.id)
    before = _version_state(row)
    if checks_result["status"] == "validated":
        fingerprint = compute_model_definition_fingerprint(db, row.id)
        row.validation_fingerprint = fingerprint
        row.validated_by = user.id
        row.validated_at = now_utc()
        row.status = "validated"
        checks_result["fingerprint"] = fingerprint
        for formula in formulas:
            if formula.status == "draft":
                formula.status = "validated"
                formula.updated_by = user.id
        row.updated_by = user.id
        db.flush()
        record_audit(db, actor_user_id=user.id, action="model_version.validated",
                     entity_type="model_version", entity_id=row.id,
                     before_state=before, after_state=_version_state(row),
                     context={"checks": checks_result["checks"], "validation_fingerprint": fingerprint})
        db.commit()
        db.refresh(row)

    return {
        "model_version": serialize_version(row),
        "status": row.status,
        "validation": checks_result,
        "fingerprint": row.validation_fingerprint,
        "validation_evidence": {
            "validation_fingerprint": row.validation_fingerprint,
            "validated_by": row.validated_by,
            "validated_at": row.validated_at.isoformat() if row.validated_at else None,
            "scheme": "mentoramp.model_definition/v1",
        },
    }


def approve_version(db: Session, version_id: str, user: Any, reason: str | None = None) -> dict[str, Any]:
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).with_for_update().first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id)
    require_active_project(db, model.project_id)

    # P1: Dedicated model approval permission (models:approve)
    user_roles = {role.name for role in getattr(user, "roles", [])}
    user_permissions = {
        p.name for role in getattr(user, "roles", []) for p in getattr(role, "permissions", [])
    }
    can_approve = bool("models:approve" in user_permissions or "admin" in user_roles)
    if not can_approve:
        raise ServiceError(403, "MODEL_APPROVAL_PERMISSION_REQUIRED", "You do not have permission to approve model versions.")

    # P0: Eligible status check
    if row.status not in {"validated", "needs_review"}:
        raise ServiceError(
            409, "MODEL_VERSION_NOT_VALIDATED",
            f"Only validated or needs_review model versions can be approved; current status is '{row.status}'.",
        )

    # P0: Four-eyes rule: creator cannot self-approve, AND last material editor cannot self-approve
    if row.created_by == user.id or row.last_material_modified_by == user.id:
        raise ServiceError(
            409, "SELF_APPROVAL_FORBIDDEN",
            "A model version must be approved by another user who did not create or materially modify it.",
        )

    # P0: Approval must verify current validation fingerprint
    current_fingerprint = compute_model_definition_fingerprint(db, row.id)
    if not row.validation_fingerprint or row.validation_fingerprint != current_fingerprint:
        raise ServiceError(
            409, "MODEL_VALIDATION_STALE",
            "Model version content has changed since it was validated; revalidation is required.",
        )

    before = _version_state(row)
    row.status = "approved"
    row.approved_by = user.id
    row.approved_at = now_utc()
    row.approved_fingerprint = current_fingerprint
    row.updated_by = user.id
    db.flush()
    record_audit(
        db, actor_user_id=user.id, action="model_version.approved",
        entity_type="model_version", entity_id=row.id,
        before_state=before, after_state=_version_state(row),
        context={"reason": reason, "approved_fingerprint": current_fingerprint},
    )
    db.commit()
    db.refresh(row)
    return serialize_version(row)


def reject_version(db: Session, version_id: str, user: Any, reason: str | None = None) -> dict[str, Any]:
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).with_for_update().first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id)
    require_active_project(db, model.project_id)

    user_roles = {role.name for role in getattr(user, "roles", [])}
    user_permissions = {
        p.name for role in getattr(user, "roles", []) for p in getattr(role, "permissions", [])
    }
    can_reject = bool("models:approve" in user_permissions or "admin" in user_roles)
    if not can_reject:
        raise ServiceError(403, "MODEL_APPROVAL_PERMISSION_REQUIRED", "You do not have permission to reject model versions.")

    # P1: Rejection must require a real reason
    clean_reason = (reason or "").strip()
    if not clean_reason or len(clean_reason) < 3:
        raise ServiceError(422, "MODEL_REJECTION_REASON_REQUIRED", "A non-empty reason is required to reject a model version.")

    if row.status in PROTECTED_STATUSES or (row.is_current and row.published_at is not None):
        raise conflict(f"Approved or published model versions cannot be rejected; current status is '{row.status}'.")

    before = _version_state(row)
    row.status = "rejected"
    row.rejected_by = user.id
    row.rejected_at = now_utc()
    row.rejection_reason = clean_reason
    row.validation_fingerprint = None
    row.validated_at = None
    row.validated_by = None
    row.updated_by = user.id
    db.flush()
    record_audit(
        db, actor_user_id=user.id, action="model_version.rejected",
        entity_type="model_version", entity_id=row.id,
        before_state=before, after_state=_version_state(row),
        context={"reason": clean_reason},
    )
    db.commit()
    db.refresh(row)
    return serialize_version(row)


def dependency_analysis(
    db: Session, version_id: str, variable: str | None = None
) -> dict[str, Any]:
    formulas = db.query(FormulaRegistry).filter(
        FormulaRegistry.model_version_id == version_id,
        FormulaRegistry.deleted_at.is_(None),
    ).order_by(FormulaRegistry.output_variable).all()
    ordered = order_formulas([to_formula_spec(row) for row in formulas])
    execution_order = [row.output_variable for row in ordered]
    same_step_deps: dict[str, set[str]] = {}
    prior_deps: dict[str, set[str]] = {}
    for row in formulas:
        names = {item.depends_on_variable for item in row.dependencies}
        priors: set[str] = set()
        if row.function_ref == "dsl" or row.function_ref.startswith("dsl:"):
            try:
                priors = set(extract_dependencies(row.expression_text or "").prior_dependencies)
            except FormulaDslError:
                pass
        same_step_deps[row.output_variable] = names
        prior_deps[row.output_variable] = priors

    upstream: set[str] = set()

    def collect_upstream(name: str) -> None:
        for dependency in same_step_deps.get(name, set()):
            if dependency not in upstream:
                upstream.add(dependency)
                collect_upstream(dependency)

    if variable:
        collect_upstream(variable)

    reverse: dict[str, set[str]] = {}
    for output, names in same_step_deps.items():
        for name in names:
            reverse.setdefault(name, set()).add(output)
    downstream: set[str] = set()
    pending = list(reverse.get(variable, set())) if variable else []
    while pending:
        name = pending.pop()
        if name in downstream:
            continue
        downstream.add(name)
        pending.extend(reverse.get(name, set()))

    ordered_upstream = [name for name in execution_order if name in upstream]
    ordered_upstream.extend(sorted(upstream - set(ordered_upstream)))
    return {
        "model_version_id": version_id,
        "variable": variable,
        "execution_order": execution_order,
        "same_step_dependencies": sorted(same_step_deps.get(variable, set())) if variable else {k: sorted(v) for k, v in same_step_deps.items()},
        "prior_dependencies": sorted(prior_deps.get(variable, set())) if variable else {k: sorted(v) for k, v in prior_deps.items()},
        "upstream_dependencies": ordered_upstream,
        "downstream_impacted": [name for name in execution_order if name in downstream],
    }


def _changed_fields(before: Any, after: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: {"before": getattr(before, field), "after": getattr(after, field)}
        for field in fields
        if getattr(before, field) != getattr(after, field)
    }


def _collection_diff(
    before_rows: list[Any], after_rows: list[Any], key: str, fields: tuple[str, ...]
) -> dict[str, Any]:
    before = {getattr(row, key): row for row in before_rows}
    after = {getattr(row, key): row for row in after_rows}
    shared = sorted(before.keys() & after.keys())
    modified = []
    for name in shared:
        changes = _changed_fields(before[name], after[name], fields)
        if changes:
            modified.append({"key": name, "before": {f: getattr(before[name], f) for f in fields},
                             "after": {f: getattr(after[name], f) for f in fields},
                             "changes": changes})
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "modified": modified,
    }


def compare_versions(db: Session, version_id: str, other_version_id: str) -> dict[str, Any]:
    before = db.get(ModelVersion, version_id)
    after = db.get(ModelVersion, other_version_id)
    if before is None or before.deleted_at is not None or after is None or after.deleted_at is not None:
        raise not_found("One or both model versions were not found.")
    if before.model_id != after.model_id:
        raise ServiceError(422, "MODEL_VERSION_MODEL_MISMATCH", "Model versions must belong to the same model.")
    formula_fields = ("expression_text", "function_ref", "unit", "explanation")
    before_formulas = db.query(FormulaRegistry).filter(
        FormulaRegistry.model_version_id == before.id, FormulaRegistry.deleted_at.is_(None)
    ).all()
    after_formulas = db.query(FormulaRegistry).filter(
        FormulaRegistry.model_version_id == after.id, FormulaRegistry.deleted_at.is_(None)
    ).all()
    formula_diff = _collection_diff(
        before_formulas, after_formulas, "output_variable", formula_fields
    )
    before_by_output = {row.output_variable: row for row in before_formulas}
    after_by_output = {row.output_variable: row for row in after_formulas}
    modified_by_name = {item["key"]: item for item in formula_diff["modified"]}
    for name in sorted(before_by_output.keys() & after_by_output.keys()):
        before_deps = sorted(dep.depends_on_variable for dep in before_by_output[name].dependencies)
        after_deps = sorted(dep.depends_on_variable for dep in after_by_output[name].dependencies)
        item = modified_by_name.get(name)
        if item is None and before_deps != after_deps:
            item = {
                "key": name,
                "before": {field: getattr(before_by_output[name], field) for field in formula_fields},
                "after": {field: getattr(after_by_output[name], field) for field in formula_fields},
                "changes": {},
            }
            formula_diff["modified"].append(item)
            modified_by_name[name] = item
        if item is None:
            continue
        item["before"]["dependencies"] = before_deps
        item["after"]["dependencies"] = after_deps
        if before_deps != after_deps:
            item["changes"]["dependencies"] = {"before": before_deps, "after": after_deps}
    formula_diff["modified"].sort(key=lambda item: item["key"])
    before_variables = db.query(ModelVariableDefinition).filter(
        ModelVariableDefinition.model_version_id == before.id
    ).all()
    after_variables = db.query(ModelVariableDefinition).filter(
        ModelVariableDefinition.model_version_id == after.id
    ).all()
    before_outputs = db.query(ModelPublishedOutput).filter(
        ModelPublishedOutput.model_version_id == before.id
    ).all()
    after_outputs = db.query(ModelPublishedOutput).filter(
        ModelPublishedOutput.model_version_id == after.id
    ).all()
    return {
        "model_version_id": before.id,
        "other_version_id": after.id,
        "metadata_changes": _changed_fields(
            before, after, ("basis", "methodology", "configuration", "status")
        ),
        "formula_changes": formula_diff,
        "variable_changes": _collection_diff(
            before_variables, after_variables, "variable_name",
            ("display_name", "description", "kind", "data_type", "unit", "required",
             "default_value", "source", "allow_scenario_override", "version"),
        ),
        "published_output_changes": _collection_diff(
            before_outputs, after_outputs, "variable_name",
            ("display_name", "unit", "dimension", "aggregation", "description", "sort_order"),
        ),
    }


def rollback_version(db: Session, version_id: str, user: Any) -> dict[str, Any]:
    row = db.query(ModelVersion).filter(
        ModelVersion.id == version_id, ModelVersion.deleted_at.is_(None)
    ).with_for_update().first()
    if row is None:
        raise not_found(f"Model version '{version_id}' not found.")
    model = db.get(Model, row.model_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)
    if row.status not in {APPROVED, PUBLISHED, SUPERSEDED}:
        raise ServiceError(
            409, "MODEL_VERSION_NOT_ACTIVATABLE",
            f"Only approved, published, or superseded model versions can be activated; current status is '{row.status}'.",
        )
    prior = db.query(ModelVersion).filter(
        ModelVersion.model_id == row.model_id,
        ModelVersion.is_current.is_(True),
        ModelVersion.id != row.id,
    ).with_for_update().all()
    before = _version_state(row)
    for current in prior:
        current.is_current = False
        if current.status == PUBLISHED:
            current.status = SUPERSEDED
        current.updated_by = user.id
    if prior:
        # Satisfy the partial unique index before promoting the rollback target.
        db.flush()
    row.is_current = True
    if row.status == SUPERSEDED:
        row.status = PUBLISHED
    row.updated_by = user.id
    model.status = "active"
    model.updated_by = user.id
    db.flush()
    record_audit(
        db, actor_user_id=user.id, action="model_version.rollback_activated",
        entity_type="model_version", entity_id=row.id,
        before_state=before, after_state=_version_state(row),
        context={"replaced_current_version_ids": [item.id for item in prior]},
    )
    db.commit()
    db.refresh(row)
    return serialize_version(row)


def create_published_output(
    db: Session, model_version_id: str, payload: dict[str, Any], user: Any
) -> dict[str, Any]:
    require_mutable_model_version(db, model_version_id)
    model_version, model = catalog_service.get_model_version(db, model_version_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)

    existing = (
        db.query(ModelPublishedOutput)
        .filter(
            ModelPublishedOutput.model_version_id == model_version_id,
            ModelPublishedOutput.variable_name == payload["variable_name"],
        )
        .first()
    )
    if existing:
        raise conflict(f"Published output for '{payload['variable_name']}' already exists.")

    row = ModelPublishedOutput(
        model_version_id=model_version_id,
        variable_name=payload["variable_name"],
        display_name=payload.get("display_name", payload["variable_name"]),
        unit=payload.get("unit"),
        dimension=payload.get("dimension"),
        aggregation=payload.get("aggregation", "sum"),
        description=payload.get("description"),
        sort_order=payload.get("sort_order", 0),
    )
    db.add(row)
    invalidate_model_validation(db, model_version_id, actor_id=user.id)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "model_version_id": row.model_version_id,
        "variable_name": row.variable_name,
        "display_name": row.display_name,
        "unit": row.unit,
        "dimension": row.dimension,
        "aggregation": row.aggregation,
        "description": row.description,
        "sort_order": row.sort_order,
    }


def update_published_output(
    db: Session, model_version_id: str, variable_name: str, payload: dict[str, Any], user: Any
) -> dict[str, Any]:
    require_mutable_model_version(db, model_version_id)
    model_version, model = catalog_service.get_model_version(db, model_version_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)

    row = (
        db.query(ModelPublishedOutput)
        .filter(
            ModelPublishedOutput.model_version_id == model_version_id,
            ModelPublishedOutput.variable_name == variable_name,
        )
        .first()
    )
    if row is None:
        raise not_found(f"Published output '{variable_name}' not found.")

    for field in ("display_name", "unit", "dimension", "aggregation", "description", "sort_order"):
        if field in payload:
            setattr(row, field, payload[field])

    invalidate_model_validation(db, model_version_id, actor_id=user.id)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "model_version_id": row.model_version_id,
        "variable_name": row.variable_name,
        "display_name": row.display_name,
        "unit": row.unit,
        "dimension": row.dimension,
        "aggregation": row.aggregation,
        "description": row.description,
        "sort_order": row.sort_order,
    }


def delete_published_output(
    db: Session, model_version_id: str, variable_name: str, user: Any
) -> None:
    require_mutable_model_version(db, model_version_id)
    model_version, model = catalog_service.get_model_version(db, model_version_id)
    access.require_project_access(db, user, model.project_id, write=True)
    require_active_project(db, model.project_id)

    row = (
        db.query(ModelPublishedOutput)
        .filter(
            ModelPublishedOutput.model_version_id == model_version_id,
            ModelPublishedOutput.variable_name == variable_name,
        )
        .first()
    )
    if row is None:
        raise not_found(f"Published output '{variable_name}' not found.")

    db.delete(row)
    invalidate_model_validation(db, model_version_id, actor_id=user.id)
    db.commit()

