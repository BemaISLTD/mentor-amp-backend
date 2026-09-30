"""Token issuance, initial admin bootstrap, and current-user endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.audit import record_audit
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
    "imports:read": "View imported actuarial data.",
    "imports:write": "Upload actuarial data files.",
    "runs:read": "View projection runs and results.",
    "runs:execute": "Start projection runs.",
}

BUILTIN_ROLES = {
    "admin": {
        "description": "Full platform access.",
        "permissions": set(BUILTIN_PERMISSIONS),
    },
    "actuary": {
        "description": "Actuarial data, model, and execution access.",
        "permissions": set(BUILTIN_PERMISSIONS) - {"projects:write"},
    },
    "model_developer": {
        "description": "Model configuration, data import, and test execution access.",
        "permissions": {
            "projects:read",
            "registries:read",
            "registries:write",
            "imports:read",
            "imports:write",
            "runs:read",
            "runs:execute",
        },
    },
    "reviewer": {
        "description": "Read-only review access to models, data, runs, and results.",
        "permissions": {
            "projects:read",
            "registries:read",
            "imports:read",
            "runs:read",
        },
    },
    "read_only": {
        "description": "Read-only access to projects and published actuarial information.",
        "permissions": {
            "projects:read",
            "registries:read",
            "imports:read",
            "runs:read",
        },
    },
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
    for name, definition in BUILTIN_ROLES.items():
        if name not in roles:
            roles[name] = Role(name=name, description=definition["description"])
            db.add(roles[name])

    db.flush()
    for role_name, definition in BUILTIN_ROLES.items():
        roles[role_name].description = definition["description"]
        roles[role_name].permissions = [
            permissions[name] for name in sorted(definition["permissions"])
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
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        after_state={
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "roles": ["admin"],
        },
        context={"source": "bootstrap"},
    )
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
