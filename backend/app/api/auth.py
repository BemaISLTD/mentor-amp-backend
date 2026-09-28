"""Token issuance, initial admin bootstrap, and current-user endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.database import get_db
from app.db.models.user import Permission, Role, User
from app.models.auth import BootstrapAdminCreate, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

BUILTIN_PERMISSIONS = {
    "projects:read": "View projects.",
    "projects:write": "Create and modify projects.",
    "registries:read": "View actuarial registries.",
    "registries:write": "Modify actuarial registries.",
    "imports:write": "Upload actuarial data files.",
    "runs:execute": "Start projection runs.",
}


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=sorted(role.name for role in user.roles),
        created_at=user.created_at,
    )


def initialize_builtin_roles(db: Session) -> dict[str, Role]:
    permissions = {permission.name: permission for permission in db.query(Permission).all()}
    for name, description in BUILTIN_PERMISSIONS.items():
        if name not in permissions:
            permission = Permission(name=name, description=description)
            db.add(permission)
            permissions[name] = permission

    roles = {role.name: role for role in db.query(Role).all()}
    if "admin" not in roles:
        roles["admin"] = Role(name="admin", description="Full platform access.")
        db.add(roles["admin"])
    if "actuary" not in roles:
        roles["actuary"] = Role(
            name="actuary", description="Actuarial data and execution access."
        )
        db.add(roles["actuary"])

    db.flush()
    roles["admin"].permissions = list(permissions.values())
    roles["actuary"].permissions = [
        permission
        for name, permission in permissions.items()
        if name != "projects:write"
    ]
    return roles


@router.post(
    "/bootstrap",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def bootstrap_admin(payload: BootstrapAdminCreate, db: Session = Depends(get_db)):
    """Create the first administrator; permanently closes after the first user."""
    if db.query(User).count() != 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Administrator bootstrap has already been completed.",
        )

    roles = initialize_builtin_roles(db)
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        roles=[roles["admin"]],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_response(user)


@router.post("/token", response_model=TokenResponse)
def issue_token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
):
    email = form.username.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.is_active or not verify_password(
        form.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(
        access_token=create_access_token(
            user.id, {"roles": sorted(role.name for role in user.roles)}
        )
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: Annotated[User, Depends(get_current_user)]):
    return _user_response(current_user)
