"""Model-version variable definitions: list and edit (authorized per model version's project).

Editing a definition changes only this model version's future run packages; submitted runs keep
the definition frozen in their package. Every effective change bumps the definition version
(v1 → v2 …), so packages and comparisons show which definition a run used.
"""

import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.db.models.model_variable import ModelVariableDefinition
from app.services.common import ServiceError, not_found
from app.services.model_definition import definition_view, definitions_for

EDITABLE_FIELDS = (
    "display_name", "description", "unit", "required", "default_value", "source",
    "allow_scenario_override",
)


def list_definitions(db: Session, model_version_id: str) -> dict[str, Any]:
    rows = definitions_for(db, model_version_id)
    return {
        "model_version_id": model_version_id,
        "variables": [definition_view(row) for row in rows.values()],
        "total": len(rows),
    }


def _next_version(label: str | None) -> str:
    match = re.fullmatch(r"v(\d+)", label or "")
    return f"v{int(match.group(1)) + 1}" if match else "v2"


def update_definition(
    db: Session, model_version_id: str, variable_name: str, changes: dict[str, Any],
    user: Any | None = None,
) -> dict[str, Any]:
    row = (
        db.query(ModelVariableDefinition)
        .filter(ModelVariableDefinition.model_version_id == model_version_id,
                ModelVariableDefinition.variable_name == variable_name)
        .first()
    )
    if row is None:
        raise not_found(f"Variable '{variable_name}' is not defined for this model version.")
    before = definition_view(row)
    # Fields that cannot be null: an explicit null means "no change".
    changes = {key: value for key, value in changes.items()
               if not (value is None and key in ("required", "allow_scenario_override", "source"))}
    source = changes.get("source")
    if source is not None:
        if not isinstance(source, dict) or source.get("type") not in (None, row.kind):
            raise ServiceError(
                422, "VALIDATION_ERROR",
                f"The source of '{variable_name}' must keep type '{row.kind}'.",
            )
        changes = {**changes, "source": {**source, "type": row.kind}}
    changed = False
    for field in EDITABLE_FIELDS:
        if field in changes and getattr(row, field) != changes[field]:
            setattr(row, field, changes[field])
            changed = True
    if changed:
        row.version = _next_version(row.version)
        row.updated_by = user.id if user else row.updated_by
        db.flush()
        if user is not None:
            record_audit(
                db, actor_user_id=user.id, action="model_variable_definition.updated",
                entity_type="model_variable_definition", entity_id=row.id,
                before_state=before, after_state=definition_view(row),
            )
    db.commit()
    db.refresh(row)
    return definition_view(row)
