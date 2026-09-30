"""Lifecycle statuses for inputs, models and Projection Sets, and which ones may be executed.

A status only becomes ``validated`` when a validation process actually ran and passed (an
import validator, the seed's validators, or an explicit validation endpoint). New rows start as
``uploaded`` (data) or ``draft`` (definitions). Rows that existed before these rules and were
never fingerprinted are ``legacy_unvalidated`` and cannot be executed until re-validated.
"""

UPLOADED = "uploaded"
DRAFT = "draft"
NEEDS_REVIEW = "needs_review"
VALIDATED = "validated"
APPROVED = "approved"
REJECTED = "rejected"
SUPERSEDED = "superseded"
LEGACY_UNVALIDATED = "legacy_unvalidated"

INPUT_STATUSES: tuple[str, ...] = (
    UPLOADED, DRAFT, NEEDS_REVIEW, VALIDATED, APPROVED, REJECTED, SUPERSEDED, LEGACY_UNVALIDATED,
)

# Inforce files, assumption tables, factor tables and scenarios a run may use.
RUNNABLE_INPUT_STATUSES = frozenset({VALIDATED, APPROVED})
# Model versions and their formulas a run may use.
RUNNABLE_MODEL_STATUSES = frozenset({VALIDATED, APPROVED})
RUNNABLE_FORMULA_STATUSES = frozenset({VALIDATED, APPROVED})
# Projection Sets: needs_review means "validated with warnings only"; warnings do not block.
RUNNABLE_PROJECTION_SET_STATUSES = frozenset({VALIDATED, NEEDS_REVIEW})
