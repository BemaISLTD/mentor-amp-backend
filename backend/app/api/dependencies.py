"""Authentication and role-based authorization dependencies."""

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.database import get_db
from app.db.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token")


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Session = Depends(get_db),
) -> User:
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
