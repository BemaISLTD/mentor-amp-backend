"""Project-scoped authorization: the single place that decides who may touch which object.

Rules
- Platform administrators (role ``admin``) may access every project.
- Everyone else may access a project only through a ``project_members`` row. Reading needs any
  membership role; writing or executing needs ``owner`` or ``editor``.
- Every project-owned object is authorized through the project that owns it. An object the
  user may not see is reported as "not found" (404), exactly like an object that does not
  exist, so IDs of other projects' objects cannot be probed.

Permission checks (``require_permissions``) still apply on top: membership decides *which*
projects, permissions decide *what kind* of action.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.assumption import AssumptionSet, AssumptionTable
from app.db.models.factor import FactorSet, FactorTable
from app.db.models.formula import FormulaRegistry
from app.db.models.inforce import InforceFile
from app.db.models.modeling import Model, ModelVersion
from app.db.models.project import Project
from app.db.models.project_member import WRITE_ROLES, ProjectMember
from app.db.models.projection import ProjectionSet, RunSet
from app.db.models.product import AssetPosition, Product
from app.db.models.run import Run
from app.db.models.scenario import ScenarioSet, ScenarioTable
from app.services.common import ServiceError, not_found

# Returned by a resolver for objects that belong to no project (e.g. legacy global formulas).
GLOBAL = "__global__"


def is_platform_admin(user: Any) -> bool:
    return any(role.name == "admin" for role in getattr(user, "roles", []) or [])


def accessible_project_ids(db: Session, user: Any) -> set[str] | None:
    """Project IDs the user may read; ``None`` means all projects (administrator)."""
    if is_platform_admin(user):
        return None
    return {
        row[0]
        for row in db.query(ProjectMember.project_id).filter(ProjectMember.user_id == user.id).all()
    }


def _membership_role(db: Session, user: Any, project_id: str) -> str | None:
    row = (
        db.query(ProjectMember.role)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        .first()
    )
    return row[0] if row else None


def require_project_access(db: Session, user: Any, project_id: str, write: bool = False) -> Project:
    """Return the project if ``user`` may access it; otherwise raise 404 (or 403 for read-only)."""
    project = db.get(Project, project_id) if project_id else None
    if project is None:
        raise not_found(f"Project '{project_id}' not found.")
    if is_platform_admin(user):
        return project
    role = _membership_role(db, user, project_id)
    if role is None:
        raise not_found(f"Project '{project_id}' not found.")
    if write and role not in WRITE_ROLES:
        raise ServiceError(
            403, "FORBIDDEN",
            "Your role in this project does not allow changes.",
            {"project_role": role},
        )
    return project


# -- object -> owning project ----------------------------------------------------------------

def _model_project(db: Session, object_id: str) -> str | None:
    model = db.get(Model, object_id)
    return model.project_id if model else None


def _model_version_project(db: Session, object_id: str) -> str | None:
    row = (
        db.query(Model.project_id)
        .join(ModelVersion, ModelVersion.model_id == Model.id)
        .filter(ModelVersion.id == object_id)
        .first()
    )
    return row[0] if row else None


def _formula_project(db: Session, object_id: str) -> str | None:
    formula = db.get(FormulaRegistry, object_id)
    if formula is None:
        return None
    if formula.model_version_id is None:
        return GLOBAL
    return _model_version_project(db, formula.model_version_id)


def _projection_set_project(db: Session, object_id: str) -> str | None:
    row = db.get(ProjectionSet, object_id)
    return row.project_id if row else None


def _run_project(db: Session, object_id: str) -> str | None:
    row = db.get(Run, object_id)
    return row.project_id if row else None


def _run_set_project(db: Session, object_id: str) -> str | None:
    row = db.get(RunSet, object_id)
    return row.project_id if row else None


def _scenario_project(db: Session, object_id: str) -> str | None:
    row = (
        db.query(ScenarioSet.project_id)
        .join(ScenarioTable, ScenarioTable.set_id == ScenarioSet.id)
        .filter(ScenarioTable.id == object_id)
        .first()
    )
    return row[0] if row else None


def _inforce_project(db: Session, object_id: str) -> str | None:
    row = db.get(InforceFile, object_id)
    return row.project_id if row else None


def _assumption_table_project(db: Session, object_id: str) -> str | None:
    row = (
        db.query(AssumptionSet.project_id)
        .join(AssumptionTable, AssumptionTable.set_id == AssumptionSet.id)
        .filter(AssumptionTable.id == object_id)
        .first()
    )
    return row[0] if row else None


def _factor_table_project(db: Session, object_id: str) -> str | None:
    row = (
        db.query(FactorSet.project_id)
        .join(FactorTable, FactorTable.set_id == FactorSet.id)
        .filter(FactorTable.id == object_id)
        .first()
    )
    return row[0] if row else None


def _product_project(db: Session, object_id: str) -> str | None:
    row = db.get(Product, object_id)
    return row.project_id if row and row.deleted_at is None else None


def _asset_position_project(db: Session, object_id: str) -> str | None:
    row = db.get(AssetPosition, object_id)
    return row.project_id if row and row.deleted_at is None else None


def _project_itself(db: Session, object_id: str) -> str | None:
    return object_id if db.get(Project, object_id) is not None else None


RESOLVERS: dict[str, Callable[[Session, str], str | None]] = {
    "project": _project_itself,
    "model": _model_project,
    "model_version": _model_version_project,
    "formula": _formula_project,
    "projection_set": _projection_set_project,
    "run": _run_project,
    "run_set": _run_set_project,
    "scenario": _scenario_project,
    "inforce_file": _inforce_project,
    "assumption_table": _assumption_table_project,
    "factor_table": _factor_table_project,
    "product": _product_project,
    "asset_position": _asset_position_project,
}

LABELS = {
    "project": "Project", "model": "Model", "model_version": "Model version",
    "formula": "Formula", "projection_set": "Projection Set", "run": "Run", "run_set": "Run Set",
    "scenario": "Scenario", "inforce_file": "Inforce file", "assumption_table": "Table",
    "factor_table": "Table",
    "product": "Product", "asset_position": "Asset position",
}


def project_id_of(db: Session, kind: str, object_id: str) -> str | None:
    """The owning project's ID, ``GLOBAL`` for unowned catalog objects, or ``None`` if missing."""
    return RESOLVERS[kind](db, object_id)


def require_object_access(
    db: Session, user: Any, kind: str, object_id: str, write: bool = False
) -> str | None:
    """Authorize access to one object through its project; return the project ID (or None)."""
    project_id = project_id_of(db, kind, object_id)
    if project_id is None:
        raise not_found(f"{LABELS[kind]} '{object_id}' not found.")
    if project_id == GLOBAL:
        return None
    try:
        require_project_access(db, user, project_id, write=write)
    except ServiceError as error:
        if error.status_code == 404:
            raise not_found(f"{LABELS[kind]} '{object_id}' not found.") from None
        raise
    return project_id
