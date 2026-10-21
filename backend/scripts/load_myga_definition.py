"""Load the governed MYGA definition into a caller-owned database transaction.

This module deliberately has no command-line entry point, migration call, session factory, or
commit.  An authorized workflow must supply the project, actor, and transaction explicitly.
"""

from sqlalchemy.orm import Session

from app.db.models.project import Project
from app.db.models.user import User
from app.products.myga_lite import config as myga
from scripts import seed_demo


def load_myga_definition(db: Session, project: Project, actor: User) -> dict:
    """Idempotently load and validate MYGA, leaving commit/rollback to the caller."""
    model, version, group = seed_demo.upsert_model(db, project, actor, myga)
    seed_demo.upsert_variables(db, myga)
    seed_demo.upsert_definitions(db, version, myga)
    seed_demo.upsert_formulas(db, version, group, myga)
    seed_demo.upsert_published_outputs(db, version, myga)
    validation = seed_demo.validate_model(db, model, version)
    return {"model": model, "version": version, "validation": validation}
