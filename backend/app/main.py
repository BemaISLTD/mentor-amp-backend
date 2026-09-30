from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.audit_logs import router as audit_logs_router
from app.api.assets import router as assets_router
from app.api.dashboard import router as dashboard_router
from app.api.dependencies import get_current_user, require_permissions
from app.api.errors import register_exception_handlers
from app.api.formulas import router as formulas_router
from app.api.imports import router as imports_router
from app.api.projects import router as projects_router
from app.api.products import router as products_router
from app.api.reconciliation import router as reconciliation_router
from app.api.results import router as results_router
from app.api.runs import router as runs_router
from app.api.trace import router as trace_router
from app.api.users import router as users_router
from app.api.variables import router as variables_router
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "http://localhost:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

API_PREFIX = "/v1"
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(users_router, prefix=API_PREFIX)
app.include_router(audit_logs_router, prefix=API_PREFIX)
app.include_router(
    projects_router,
    prefix=API_PREFIX,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    imports_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("imports:read"))],
)
app.include_router(
    variables_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("registries:read"))],
)
app.include_router(
    formulas_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("registries:read"))],
)
app.include_router(
    runs_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("runs:read"))],
)
app.include_router(
    results_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("runs:read"))],
)
app.include_router(
    trace_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("runs:read"))],
)
app.include_router(
    reconciliation_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("runs:read"))],
)
app.include_router(
    products_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("registries:read"))],
)
app.include_router(
    assets_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("registries:read"))],
)
app.include_router(
    dashboard_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("projects:read"))],
)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
