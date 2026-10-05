from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.auth import router as auth_router
from app.api.audit_logs import router as audit_logs_router
from app.api.assets import router as assets_router
from app.api.dashboard import router as dashboard_router
from app.api.dependencies import get_current_user, require_permissions, require_roles
from app.api.errors import register_exception_handlers
from app.api.execution import router as execution_router
from app.api.formulas import router as formulas_router
from app.api.imports import router as imports_router
from app.api.inputs import router as inputs_router
from app.api.modeling import router as modeling_router
from app.api.projection_sets import router as projection_sets_router
from app.api.projects import router as projects_router
from app.api.products import router as products_router
from app.api.reconciliation import router as reconciliation_router
from app.api.results import router as legacy_results_router
from app.api.runs import router as legacy_runs_router
from app.api.trace import router as legacy_trace_router
from app.api.scenarios import router as scenarios_router
from app.api.system import router as system_router
from app.api.users import router as users_router
from app.api.variables import router as variables_router
from app.config import settings
from app.products.registry import register_all_products
from app.services.build_info import build_identity
from app.services.common import register_service_error_handler
from app.version import APP_VERSION

# Formula functions are code: register them once at startup (contract §F.4).
register_all_products()
# Fix this process's build identity at startup, from the code it actually loaded.
build_identity()

app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-MentorAmp-Run-Id", "X-MentorAmp-Illustrative", "X-MentorAmp-Complete"],
)

register_exception_handlers(app)
register_service_error_handler(app)

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
    reconciliation_router,
    prefix=API_PREFIX,
    dependencies=[Depends(require_permissions("runs:read")), Depends(require_roles("admin"))],
)
for legacy_router in (legacy_runs_router, legacy_results_router, legacy_trace_router):
    app.include_router(
        legacy_router,
        prefix=API_PREFIX,
        dependencies=[Depends(require_permissions("runs:read")), Depends(require_roles("admin"))],
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
# Consolidated run endpoints own /runs/{id}/summary, /results, and /events.
app.include_router(modeling_router, prefix=API_PREFIX)
app.include_router(system_router, prefix=API_PREFIX)
app.include_router(inputs_router, prefix=API_PREFIX)
app.include_router(scenarios_router, prefix=API_PREFIX)
app.include_router(projection_sets_router, prefix=API_PREFIX)
app.include_router(execution_router, prefix=API_PREFIX)
app.include_router(analysis_router, prefix=API_PREFIX)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
