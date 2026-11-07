"""Dagster Workflow Orchestration for MentorAMP (Work Package M5D).

Authoritative Architecture: CAS §9 and §15.8.
Pipeline Sequence:
    Inforce Import -> Validation -> Projection -> Aggregation ->
    Valuation -> Rollforward -> Reconciliation -> Reporting

Invariant: Dagster assets orchestrate backend services; they contain ZERO actuarial formulas.
"""

from app.orchestration.dagster.definitions import defs

__all__ = ["defs"]
