"""Authentication and role-based authorization dependencies."""

import secrets
from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import decode_access_token, hash_password
from app.db.database import get_db
from app.db.models.user import User

# auto_error=False lets demo mode accept requests without a token; jwt mode checks below.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_demo_user(db: Session) -> User:
    """Return the demo administrator, creating it (and the built-in roles) on first use."""
    user = db.query(User).filter(User.email == settings.demo_user_email).first()
    if user is not None:
        return user

    from app.api.auth import initialize_builtin_roles  # local import: auth imports this module

    roles = initialize_builtin_roles(db)
    user = User(
        email=settings.demo_user_email,
        full_name=settings.demo_user_name,
        # Unusable random password: the demo user never logs in with a password.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        roles=[roles["admin"]],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Session = Depends(get_db),
) -> User:
    if settings.auth_mode == "disabled":
        return get_demo_user(db)
    if not token:
        raise _credentials_error()
    try:
        subject = decode_access_token(token)["sub"]
    except jwt.InvalidTokenError as exc:
        raise _credentials_error() from exc

    user = db.query(User).filter(User.id == subject).first()
    if user is None or not user.is_active:
        raise _credentials_error()
    return user


def require_roles(*allowed_roles: str) -> Callable[..., User]:
    allowed = set(allowed_roles)

    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        assigned = {role.name for role in current_user.roles}
        if assigned.isdisjoint(allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return current_user

    return dependency


def require_permissions(*required_permissions: str) -> Callable[..., User]:
    required = set(required_permissions)

    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        assigned = {
            permission.name
            for role in current_user.roles
            for permission in role.permissions
        }
        if not required.issubset(assigned):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return current_user

    return dependency
