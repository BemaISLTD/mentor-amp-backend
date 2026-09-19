"""CRUD operations for the Variable Registry — backed by PostgreSQL."""

from sqlalchemy.orm import Session

from app.db.models.variable import VariableRegistry as VariableModel
from app.models.schemas import VariableDefinition


def _model_to_definition(model: VariableModel) -> VariableDefinition:
    """Convert a SQLAlchemy model to a Pydantic VariableDefinition."""
    default_val = None
    if model.default_value and isinstance(model.default_value, dict):
        default_val = model.default_value.get("value")

    source = None
    if model.source_type == "manual":
        source = {"type": "manual", "value": default_val}
    elif model.source_type and model.source_table:
        source = {"type": model.source_type, "table_id": model.source_table}
        if model.lookup_keys:
            source["lookup_keys"] = model.lookup_keys
    elif model.source_type == "input":
        source = {"type": "input", "column_name": model.name}

    return VariableDefinition(
        id=model.id, name=model.name, label=model.display_name,
        kind=model.source_type, data_type=model.data_type,
        source=source, dependencies=[], required=model.required,
        default_value=default_val,
        product_applicability=model.product_applicability or [],
        basis_applicability=model.basis_applicability or [],
        description=model.description,
    )


def register(db: Session, variable: VariableDefinition) -> VariableDefinition:
    """Create a new variable in the registry."""
    source = variable.source or {}
    source_type = variable.kind

    # Store value from source or default_value
    default_value = None
    if isinstance(source, dict) and source.get("value") is not None:
        default_value = {"value": source.get("value")}
    elif variable.default_value is not None:
        default_value = {"value": variable.default_value}

    model = VariableModel(
        name=variable.name,
        display_name=variable.label,
        description=variable.description,
        data_type=variable.data_type,
        source_type=source_type,
        source_table=source.get("table_id") if isinstance(source, dict) else getattr(source, "table_id", None),
        lookup_keys=source.get("lookup_keys", []) if isinstance(source, dict) else [],
        required=variable.required,
        default_value=default_value,
        product_applicability=variable.product_applicability,
        basis_applicability=variable.basis_applicability,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return _model_to_definition(model)


def get_by_name(db: Session, name: str) -> VariableDefinition | None:
    model = db.query(VariableModel).filter(VariableModel.name == name).first()
    if model is None:
        return None
    return _model_to_definition(model)


def get_by_id(db: Session, variable_id: str) -> VariableDefinition | None:
    model = db.query(VariableModel).filter(VariableModel.id == variable_id).first()
    if model is None:
        return None
    return _model_to_definition(model)


def list_all(db: Session, product: str | None = None, kind: str | None = None) -> list[VariableDefinition]:
    query = db.query(VariableModel)
    if kind:
        query = query.filter(VariableModel.source_type == kind)
    if product:
        query = query.filter(VariableModel.product_applicability.any(product))
    return [_model_to_definition(m) for m in query.order_by(VariableModel.name).all()]


def update(db: Session, name: str, updates: dict) -> VariableDefinition | None:
    model = db.query(VariableModel).filter(VariableModel.name == name).first()
    if model is None:
        return None
    allowed = {"display_name", "description", "data_type", "source_type", "source_table",
               "lookup_keys", "required", "default_value", "product_applicability", "basis_applicability"}
    for key, value in updates.items():
        if key in allowed:
            setattr(model, key, value)
    db.commit()
    db.refresh(model)
    return _model_to_definition(model)


def delete(db: Session, name: str) -> bool:
    model = db.query(VariableModel).filter(VariableModel.name == name).first()
    if model is None:
        return False
    db.delete(model)
    db.commit()
    return True
