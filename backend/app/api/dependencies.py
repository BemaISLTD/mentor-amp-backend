"""Authentication, role/permission checks and project-scoped object authorization."""

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import decode_access_token
from app.db.database import get_db
from app.db.models.user import User
from app.services import access
from app.services.bootstrap import find_demo_user

# auto_error=False lets demo mode accept requests without a token; jwt mode checks below.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Session = Depends(get_db),
) -> User:
    """Authenticate the request. Never creates or changes database rows."""
    if settings.auth_mode == "disabled":
        # Re-checked per request: demo mode must never serve outside local/test.
        if not settings.demo_auth_permitted:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Demo authentication is not permitted in this environment.",
            )
        user = find_demo_user(db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The demo user is not provisioned. Run backend/scripts/seed_demo.py.",
            )
        return user
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


def authorize_path(
    kind: str, param: str, *permissions: str, write: bool = False
) -> Callable[..., User]:
    """Permission check plus project-scoped access to the object named by a path parameter.

    Example: ``Annotated[User, Depends(authorize_path("run", "run_id", "projects:read"))]``.
    """
    permission_check = require_permissions(*permissions)

    def dependency(
        request: Request,
        current_user: Annotated[User, Depends(permission_check)],
        db: Session = Depends(get_db),
    ) -> User:
        access.require_object_access(db, current_user, kind, request.path_params[param], write=write)
        return current_user

    return dependency
