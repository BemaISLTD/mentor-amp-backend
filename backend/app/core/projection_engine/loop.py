"""Data loading helpers for the projection loop."""

from sqlalchemy.orm import Session

from app.core.formula_engine.registry import list_all as list_formulas
from app.db.models.inforce import InforceRecord
from app.models.schemas import FormulaDefinition


def load_policies(db: Session, dataset_ids: list[str]) -> list[dict]:
    """Load all inforce policy records for the given datasets.

    Returns a list of dicts, each with 'policy_id' and 'data' keys.
    """
    if not dataset_ids:
        # If no datasets specified, load all inforce records
        records = db.query(InforceRecord).all()
    else:
        records = (
            db.query(InforceRecord)
            .filter(InforceRecord.file_id.in_(dataset_ids))
            .all()
        )

    return [
        {"policy_id": r.policy_id, "data": r.data or {}}
        for r in records
    ]


def load_formulas(db: Session) -> list[FormulaDefinition]:
    """Load all active formulas from the registry."""
    return list_formulas(db)
