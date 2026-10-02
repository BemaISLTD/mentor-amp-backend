"""System status for the sidebar card and Settings (contract §E.0.2)."""

import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import settings
from app.core.projection_engine.engine import ENGINE_VERSION
from app.db.database import get_db
from app.products.registry import REGISTERED_PRODUCTS, registered_function_count
from app.services.common import iso, now_utc
from app.version import APP_VERSION

router = APIRouter(prefix="/system", tags=["system"])
SLOW_DATABASE_MS = 2000


@router.get("/status", dependencies=[Depends(get_current_user)])
def system_status(db: Session = Depends(get_db)):
    database = {"status": "ok", "latency_ms": None}
    started = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        database["latency_ms"] = round((time.perf_counter() - started) * 1000)
    except Exception:  # noqa: BLE001 - report, never raise
        database["status"] = "error"
    functions = registered_function_count()
    degraded = (
        database["status"] != "ok"
        or (database["latency_ms"] or 0) > SLOW_DATABASE_MS
        or functions == 0
    )
    return {
        "status": "degraded" if degraded else "ok",
        "app": settings.app_name,
        "version": APP_VERSION,
        "app_env": settings.app_env,
        "code_version": settings.code_version,
        "auth_mode": settings.auth_mode,
        "database": database,
        "engine": {
            "registered_functions": functions,
            "products": list(REGISTERED_PRODUCTS),
            "execution_backend": "cpu",
            "engine_version": ENGINE_VERSION,
        },
        "time": iso(now_utc()),
    }
