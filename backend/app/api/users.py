"""Administrator-managed user endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import _user_response
from app.api.dependencies import require_roles
from app.core.security import hash_password
from app.db.database import get_db
from app.db.models.user import Role, User
from app.models.auth import UserCreate, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=list[UserResponse])
def list_users(
    current_admin: Annotated[User, Depends(require_roles("admin"))],
    db: Session = Depends(get_db),
):
    del current_admin
    return [_user_response(user) for user in db.query(User).order_by(User.email).all()]


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    current_admin: Annotated[User, Depends(require_roles("admin"))],
    db: Session = Depends(get_db),
):
    del current_admin
    if db.query(User).filter(User.email == payload.email).first() is not None:
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    roles = db.query(Role).filter(Role.name.in_(payload.roles)).all()
    found = {role.name for role in roles}
    missing = sorted(set(payload.roles) - found)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"message": "Unknown roles.", "roles": missing},
        )

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        roles=roles,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_response(user)
