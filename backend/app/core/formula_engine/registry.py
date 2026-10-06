"""CRUD operations for the Formula Registry — backed by PostgreSQL."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.db.models.formula import FormulaRegistry as FormulaModel
from app.db.models.formula_dependency import FormulaDependency
from app.models.schemas import FormulaDefinition


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
    )


def register(db: Session, formula: FormulaDefinition, actor_user_id: str) -> FormulaDefinition:
    """Create a formula with its dependencies."""
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
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    db.add(model)
    for dep_var in formula.dependencies:
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
    before = _state(model)
    allowed = {"name", "output_variable", "function_ref", "category",
               "product_applicability", "basis_applicability", "status"}
    for key, value in updates.items():
        if key in allowed:
            setattr(model, key, value)
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
