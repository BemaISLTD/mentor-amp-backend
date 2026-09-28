"""Application-wide API error responses."""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


def _error_code(status_code: int) -> str:
    """Return a stable, frontend-friendly code for an HTTP status."""
    try:
        return HTTPStatus(status_code).name
    except ValueError:
        return "HTTP_ERROR"


def _payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def register_exception_handlers(app: FastAPI) -> None:
    """Register the public error contract in one place."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        del request
        if isinstance(exc.detail, str):
            message = exc.detail
            details = None
        else:
            message = HTTPStatus(exc.status_code).phrase
            details = exc.detail

        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(_error_code(exc.status_code), message, details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=422,
            content=_payload(
                "VALIDATION_ERROR",
                "Request validation failed.",
                exc.errors(),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=500,
            content=_payload(
                "INTERNAL_SERVER_ERROR",
                "An unexpected error occurred.",
            ),
        )
