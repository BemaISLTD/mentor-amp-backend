"""CRUD operations for the Formula Registry — backed by PostgreSQL."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.formula_engine.dsl import FormulaDslError, extract_dependencies
from app.core.model_lifecycle import invalidate_model_validation, require_mutable_model_version
from app.db.models.formula import FormulaRegistry as FormulaModel
from app.db.models.formula_dependency import FormulaDependency
from app.db.models.model_variable import ModelVariableDefinition
from app.db.models.modeling import FormulaGroup, ModelVersion
from app.db.models.user import User
from app.db.models.variable import VariableRegistry
from app.models.schemas import FormulaDefinition
from app.services.common import ServiceError


def _state(model: FormulaModel) -> dict:
    return {
        "name": model.name,
        "output_variable": model.output_variable,
        "function_ref": model.function_ref,
        "dependencies": [item.depends_on_variable for item in model.dependencies],
        "category": model.category,
        "product_applicability": model.product_applicability,
        "basis_applicability": model.basis_applicability,
        "version": model.version,
        "status": model.status,
        "model_version_id": model.model_version_id,
        "group_id": model.group_id,
        "expression_text": model.expression_text,
        "explanation": model.explanation,
        "unit": model.unit,
        "illustrative": model.illustrative,
        "effective_date": model.effective_date.isoformat() if model.effective_date else None,
        "owner_user_id": model.owner_user_id,
        "test_cases": list(model.test_cases or []),
        "created_by": model.created_by,
        "updated_by": model.updated_by,
        "deleted_by": model.deleted_by,
        "deleted_at": model.deleted_at.isoformat() if model.deleted_at else None,
    }


def _model_to_definition(model: FormulaModel) -> FormulaDefinition:
    """Convert SQLAlchemy model to Pydantic FormulaDefinition."""
    return FormulaDefinition(
        id=model.id,
        name=model.name,
        output_variable=model.output_variable,
        function_ref=model.function_ref,
        dependencies=[d.depends_on_variable for d in model.dependencies],
        category=model.category or "",
        product_applicability=model.product_applicability or [],
        basis_applicability=model.basis_applicability or [],
        version=model.version or "v1",
        status=model.status or "draft",
        model_version_id=model.model_version_id,
        expression_text=model.expression_text,
        explanation=model.explanation,
        unit=model.unit,
        group_id=model.group_id,
        illustrative=bool(model.illustrative),
        effective_date=model.effective_date,
        owner_user_id=model.owner_user_id,
        test_cases=list(model.test_cases or []),
    )


def _require_mutable_model_version(db: Session, model: FormulaModel) -> None:
    if model.model_version_id:
        require_mutable_model_version(db, model.model_version_id)


def _validate_references(
    db: Session,
    *,
    model_version_id: str | None,
    output_variable: str,
    group_id: str | None,
    owner_user_id: str | None,
    exclude_formula_id: str | None = None,
) -> None:
    registered_output = db.query(VariableRegistry).filter(
        VariableRegistry.name == output_variable,
        VariableRegistry.deleted_at.is_(None),
    ).first()
    defined_output = None
    if model_version_id:
        defined_output = db.query(ModelVariableDefinition).filter(
            ModelVariableDefinition.model_version_id == model_version_id,
            ModelVariableDefinition.variable_name == output_variable,
        ).first()
    if registered_output is None or (model_version_id and defined_output is None):
        raise ServiceError(
            422, "FORMULA_OUTPUT_NOT_DEFINED",
            f"Output variable '{output_variable}' is not defined"
            + (" in this model version." if model_version_id else "."),
        )
    if owner_user_id and db.query(User.id).filter(User.id == owner_user_id).first() is None:
        raise ServiceError(
            422, "FORMULA_OWNER_NOT_FOUND", f"Formula owner '{owner_user_id}' does not exist."
        )
    if group_id:
        group = db.get(FormulaGroup, group_id)
        if group is None or group.model_version_id != model_version_id:
            raise ServiceError(
                422, "INVALID_FORMULA_GROUP", "Formula group is not in this model version."
            )
    duplicate_query = db.query(FormulaModel).filter(
        FormulaModel.model_version_id == model_version_id,
        FormulaModel.output_variable == output_variable,
        FormulaModel.deleted_at.is_(None),
    )
    if exclude_formula_id:
        duplicate_query = duplicate_query.filter(FormulaModel.id != exclude_formula_id)
    if duplicate_query.first() is not None:
        raise ServiceError(
            409, "FORMULA_OUTPUT_EXISTS",
            f"A formula for output '{output_variable}' already exists in this model version.",
        )


def register(db: Session, formula: FormulaDefinition, actor_user_id: str) -> FormulaDefinition:
    """Create a formula with its dependencies."""
    mv_id = getattr(formula, "model_version_id", None)
    if mv_id:
        require_mutable_model_version(db, mv_id)
    _validate_references(
        db,
        model_version_id=mv_id,
        output_variable=formula.output_variable,
        group_id=formula.group_id,
        owner_user_id=formula.owner_user_id or actor_user_id,
    )
    if mv_id:
        invalidate_model_validation(db, mv_id, actor_user_id)
    dependencies = list(formula.dependencies)
    if formula.function_ref == "dsl" or formula.function_ref.startswith("dsl:"):
        if not formula.expression_text:
            raise ServiceError(422, "DSL_EXPRESSION_REQUIRED", "DSL formulas require expression_text.")
        try:
            extracted = extract_dependencies(formula.expression_text)
        except FormulaDslError as exc:
            raise ServiceError(422, exc.code, exc.message, exc.as_dict()) from exc
        # The expression is authoritative. Persisting caller-supplied metadata that differs
        # from the parsed graph would validate in the editor and then fail in the engine.
        dependencies = extracted.dependencies
    model = FormulaModel(
        id=formula.id,
        name=formula.name,
        output_variable=formula.output_variable,
        function_ref=formula.function_ref,
        category=formula.category,
        product_applicability=formula.product_applicability,
        basis_applicability=formula.basis_applicability,
        version=formula.version,
        status=formula.status,
        model_version_id=mv_id,
        group_id=formula.group_id,
        expression_text=formula.expression_text,
        explanation=formula.explanation,
        unit=formula.unit,
        illustrative=formula.illustrative,
        effective_date=formula.effective_date,
        owner_user_id=formula.owner_user_id or actor_user_id,
        test_cases=list(formula.test_cases),
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    db.add(model)
    for dep_var in dependencies:
        model.dependencies.append(FormulaDependency(depends_on_variable=dep_var))
    db.flush()
    record_audit(
        db, actor_user_id=actor_user_id, action="formula.created",
        entity_type="formula", entity_id=model.id, after_state=_state(model),
    )
    db.commit()
    db.refresh(model)
    return _model_to_definition(model)


def get_by_id(db: Session, formula_id: str) -> FormulaDefinition | None:
    model = db.query(FormulaModel).filter(
        FormulaModel.id == formula_id, FormulaModel.deleted_at.is_(None)
    ).first()
    if model is None:
        return None
    return _model_to_definition(model)


def get_by_output(db: Session, output_variable: str) -> FormulaDefinition | None:
    model = db.query(FormulaModel).filter(
        FormulaModel.output_variable == output_variable, FormulaModel.deleted_at.is_(None)
    ).first()
    if model is None:
        return None
    return _model_to_definition(model)


def list_all(db: Session, category: str | None = None, product: str | None = None) -> list[FormulaDefinition]:
    query = db.query(FormulaModel).filter(FormulaModel.deleted_at.is_(None))
    if category:
        query = query.filter(FormulaModel.category == category)
    if product:
        query = query.filter(FormulaModel.product_applicability.any(product))
    return [_model_to_definition(m) for m in query.order_by(FormulaModel.name).all()]


def update(db: Session, formula_id: str, updates: dict, actor_user_id: str) -> FormulaDefinition | None:
    model = db.query(FormulaModel).filter(
        FormulaModel.id == formula_id, FormulaModel.deleted_at.is_(None)
    ).first()
    if model is None:
        return None
    _require_mutable_model_version(db, model)
    before = _state(model)
    allowed = {"name", "output_variable", "function_ref", "category",
               "product_applicability", "basis_applicability", "status", "version",
               "group_id", "expression_text", "explanation", "unit", "illustrative",
               "effective_date", "owner_user_id", "test_cases"}
    next_function_ref = updates.get("function_ref", model.function_ref)
    next_expression = updates.get("expression_text", model.expression_text)
    _validate_references(
        db,
        model_version_id=model.model_version_id,
        output_variable=updates.get("output_variable", model.output_variable),
        group_id=updates.get("group_id", model.group_id),
        owner_user_id=updates.get("owner_user_id", model.owner_user_id),
        exclude_formula_id=model.id,
    )
    invalidate_model_validation(db, model.model_version_id, actor_user_id)
    derived_dependencies = None
    if next_function_ref == "dsl" or str(next_function_ref).startswith("dsl:"):
        if not next_expression:
            raise ServiceError(422, "DSL_EXPRESSION_REQUIRED", "DSL formulas require expression_text.")
        try:
            extracted = extract_dependencies(next_expression)
        except FormulaDslError as exc:
            raise ServiceError(422, exc.code, exc.message, exc.as_dict()) from exc
        derived_dependencies = extracted.dependencies
    for key, value in updates.items():
        if key in allowed:
            setattr(model, key, value)
    dependency_updates = (
        derived_dependencies
        if derived_dependencies is not None
        else updates.get("dependencies")
    )
    if dependency_updates is not None:
        model.dependencies.clear()
        for dep in dependency_updates:
            model.dependencies.append(FormulaDependency(depends_on_variable=dep))
    model.updated_by = actor_user_id
    db.flush()
    record_audit(
        db, actor_user_id=actor_user_id, action="formula.updated",
        entity_type="formula", entity_id=model.id,
        before_state=before, after_state=_state(model),
    )
    db.commit()
    db.refresh(model)
    return _model_to_definition(model)


def delete(db: Session, formula_id: str, actor_user_id: str) -> bool:
    model = db.query(FormulaModel).filter(
        FormulaModel.id == formula_id, FormulaModel.deleted_at.is_(None)
    ).first()
    if model is None:
        return False
    _require_mutable_model_version(db, model)
    invalidate_model_validation(db, model.model_version_id, actor_user_id)
    before = _state(model)
    model.deleted_at = datetime.now(timezone.utc)
    model.deleted_by = actor_user_id
    model.updated_by = actor_user_id
    db.flush()
    record_audit(
        db, actor_user_id=actor_user_id, action="formula.deleted",
        entity_type="formula", entity_id=model.id, before_state=before,
        after_state=_state(model), context={"deletion": "soft"},
    )
    db.commit()
    return True
