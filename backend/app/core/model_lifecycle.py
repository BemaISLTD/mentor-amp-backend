"""Central model-version lifecycle state machine and mutation governance.

Governs:
- Valid lifecycle states and transitions
- Invalidation of validation upon material modification
- Mutability guards (approved, published, superseded, or current versions are immutable)
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.modeling import ModelVersion
from app.services.common import ServiceError

DRAFT = "draft"
NEEDS_REVIEW = "needs_review"
VALIDATED = "validated"
APPROVED = "approved"
REJECTED = "rejected"
PUBLISHED = "published"
SUPERSEDED = "superseded"

PROTECTED_STATUSES = frozenset({APPROVED, PUBLISHED, SUPERSEDED})

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    DRAFT: {VALIDATED},
    VALIDATED: {APPROVED, REJECTED, DRAFT},
    NEEDS_REVIEW: {APPROVED, REJECTED, DRAFT},
    APPROVED: {PUBLISHED, SUPERSEDED},
    PUBLISHED: {SUPERSEDED},
    REJECTED: {DRAFT},
    SUPERSEDED: set(),
}


def require_mutable_model_version(
    db: Session, model_version_id: str | None, for_update: bool = True
) -> ModelVersion | None:
    """Refuse modification if the model version is approved, published, superseded, or current."""
    if not model_version_id:
        return None
    query = db.query(ModelVersion).filter(ModelVersion.id == model_version_id)
    if for_update:
        query = query.with_for_update()
    version = query.first()
    if version is None:
        return None
    if version.status in PROTECTED_STATUSES or (version.is_current and version.published_at is not None):
        raise ServiceError(
            409,
            "MODEL_VERSION_IMMUTABLE",
            f"Model version '{version.version_label}' is immutable in state '{version.status}'; create a new version.",
            {"model_version_id": version.id, "status": version.status},
        )
    return version


def invalidate_model_validation(
    db: Session,
    model_version_id: str | None,
    actor_id: str | None = None,
) -> None:
    """Invalidate any existing validation on a model version after a material change.

    If the model version is in an immutable state, raises 409 MODEL_VERSION_IMMUTABLE.
    If the model version is in draft or validated/needs_review, resets status to draft,
    clears validation evidence, and records the material editor identity and timestamp.
    """
    if not model_version_id:
        return
    version = require_mutable_model_version(db, model_version_id)
    if version is None:
        return

    now = datetime.now(timezone.utc)
    if actor_id:
        version.last_material_modified_by = actor_id
        version.last_material_modified_at = now
        version.updated_by = actor_id

    # If previously validated or needs_review, reset to draft and clear validation evidence
    if version.status in {VALIDATED, NEEDS_REVIEW}:
        version.status = DRAFT
        version.validation_fingerprint = None
        version.validated_at = None
        version.validated_by = None

    db.flush()


def transition_model_version(
    version: ModelVersion,
    target_status: str,
) -> None:
    """Transition a model version to target_status or raise INVALID_MODEL_VERSION_TRANSITION."""
    current_status = version.status
    if current_status == target_status:
        return

    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise ServiceError(
            409,
            "INVALID_MODEL_VERSION_TRANSITION",
            f"Cannot transition model version from '{current_status}' to '{target_status}'.",
            {"current_status": current_status, "target_status": target_status},
        )
    version.status = target_status
