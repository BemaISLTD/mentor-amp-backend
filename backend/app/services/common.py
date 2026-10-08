"""Shared helpers for services: errors, serialisation, fingerprints, small lookups."""

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.models.user import User

PRODUCT_NAMES = {
    "SPIA": "Single Premium Immediate Annuity",
    "MYGA": "Multi-Year Guaranteed Annuity",
    "FIA": "Fixed Indexed Annuity",
    "RILA": "Registered Index-Linked Annuity",
    "IDI": "Individual Disability Income",
    "VA": "Variable Annuity",
    "PRT": "Pension Risk Transfer",
}

TERMINAL_RUN_STATUSES = {"success", "partial_success", "failed", "cancelled"}


class ServiceError(Exception):
    """An error with an HTTP status, stable code, readable message and optional details."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def not_found(message: str, **details: Any) -> ServiceError:
    return ServiceError(404, "NOT_FOUND", message, details or None)


def conflict(message: str, **details: Any) -> ServiceError:
    return ServiceError(409, "CONFLICT", message, details or None)


def bad_request(message: str, **details: Any) -> ServiceError:
    return ServiceError(400, "BAD_REQUEST", message, details or None)


def register_service_error_handler(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        del request
        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            error["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content={"error": error})


def iso(value: Any) -> str | None:
    """ISO 8601 text for dates and datetimes (UTC datetimes end in 'Z')."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    """SHA-256 of the canonical JSON form of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def user_ref(db: Session, user_id: str | None, current_user_id: str | None = None) -> dict | None:
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None:
        return None
    ref: dict[str, Any] = {"id": user.id, "full_name": user.full_name}
    if current_user_id is not None:
        ref["is_current_user"] = user.id == current_user_id
    return ref


def unwrap_value(stored: Any) -> Any:
    """Values in JSON columns are stored as {"value": x}; return x."""
    if isinstance(stored, dict) and "value" in stored:
        return stored["value"]
    return stored


def product_name(code: str | None) -> str | None:
    if code is None:
        return None
    return PRODUCT_NAMES.get(code.upper(), code)
