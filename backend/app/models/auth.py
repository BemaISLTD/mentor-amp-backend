"""Request and response contracts for authentication and users."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=12, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["actuary"])

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("A valid email address is required.")
        return value

    @field_validator("roles")
    @classmethod
    def roles_must_not_be_empty(cls, value: list[str]) -> list[str]:
        normalized = sorted({role.strip().lower() for role in value if role.strip()})
        if not normalized:
            raise ValueError("At least one role is required.")
        return normalized


class BootstrapAdminCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("A valid email address is required.")
        return value


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    roles: list[str]
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
