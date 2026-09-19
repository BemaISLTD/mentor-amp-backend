"""CRUD operations for the Formula Registry — backed by PostgreSQL."""

from sqlalchemy.orm import Session

from app.db.models.formula import FormulaRegistry as FormulaModel
from app.db.models.formula_dependency import FormulaDependency
from app.models.schemas import FormulaDefinition


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


def register(db: Session, formula: FormulaDefinition) -> FormulaDefinition:
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
    )
    db.add(model)
    db.flush()

    for dep_var in formula.dependencies:
        dep = FormulaDependency(formula_id=model.id, depends_on_variable=dep_var)
        db.add(dep)

    db.commit()
    db.refresh(model)
    return _model_to_definition(model)


def get_by_id(db: Session, formula_id: str) -> FormulaDefinition | None:
    model = db.query(FormulaModel).filter(FormulaModel.id == formula_id).first()
    if model is None:
        return None
    return _model_to_definition(model)


def get_by_output(db: Session, output_variable: str) -> FormulaDefinition | None:
    model = db.query(FormulaModel).filter(FormulaModel.output_variable == output_variable).first()
    if model is None:
        return None
    return _model_to_definition(model)


def list_all(db: Session, category: str | None = None, product: str | None = None) -> list[FormulaDefinition]:
    query = db.query(FormulaModel)
    if category:
        query = query.filter(FormulaModel.category == category)
    if product:
        query = query.filter(FormulaModel.product_applicability.any(product))
    return [_model_to_definition(m) for m in query.order_by(FormulaModel.name).all()]


def update(db: Session, formula_id: str, updates: dict) -> FormulaDefinition | None:
    model = db.query(FormulaModel).filter(FormulaModel.id == formula_id).first()
    if model is None:
        return None
    allowed = {"name", "output_variable", "function_ref", "category",
               "product_applicability", "basis_applicability", "status"}
    for key, value in updates.items():
        if key in allowed:
            setattr(model, key, value)
    db.commit()
    db.refresh(model)
    return _model_to_definition(model)


def delete(db: Session, formula_id: str) -> bool:
    model = db.query(FormulaModel).filter(FormulaModel.id == formula_id).first()
    if model is None:
        return False
    db.delete(model)
    db.commit()
    return True
