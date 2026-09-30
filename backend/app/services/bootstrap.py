"""Explicit development setup: built-in roles and the demo administrator.

The demo user is provisioned here — by the demo seed or another deliberate setup step — and
never by request authentication, which only looks users up.
"""

import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_password
from app.db.models.project_member import OWNER, ProjectMember
from app.db.models.user import User


def find_demo_user(db: Session) -> User | None:
    return db.query(User).filter(User.email == settings.demo_user_email).first()


def ensure_demo_user(db: Session) -> User:
    """Return the demo administrator, creating it (and the built-in roles) if missing.

    Safe to call from several processes: a concurrent insert of the same e-mail loses on the
    unique constraint and the existing row is returned.
    """
    user = find_demo_user(db)
    if user is not None:
        return user

    from app.api.auth import initialize_builtin_roles  # local import: auth imports dependencies

    roles = initialize_builtin_roles(db)
    user = User(
        email=settings.demo_user_email,
        full_name=settings.demo_user_name,
        # Unusable random password: the demo user never logs in with a password.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        roles=[roles["admin"]],
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = find_demo_user(db)
        if existing is None:
            raise
        return existing
    db.refresh(user)
    return user


def ensure_member(db: Session, project_id: str, user_id: str, role: str = OWNER) -> ProjectMember:
    member = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .first()
    )
    if member is None:
        member = ProjectMember(project_id=project_id, user_id=user_id, role=role)
        db.add(member)
        db.flush()
    return member
