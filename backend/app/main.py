from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.auth import router as auth_router
from app.api.dependencies import get_current_user, require_roles
from app.api.errors import register_exception_handlers
from app.api.execution import router as execution_router
from app.api.formulas import router as formulas_router
from app.api.imports import router as imports_router
from app.api.inputs import router as inputs_router
from app.api.modeling import router as modeling_router
from app.api.projection_sets import router as projection_sets_router
from app.api.projects import router as projects_router
from app.api.scenarios import router as scenarios_router
from app.api.system import router as system_router
from app.api.users import router as users_router
from app.api.variables import router as variables_router
from app.config import settings
from app.products.registry import register_all_products
from app.services.common import register_service_error_handler

# Formula functions are code: register them once at startup (contract §F.4).
register_all_products()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-MentorAmp-Run-Id", "X-MentorAmp-Illustrative"],
)

register_exception_handlers(app)
register_service_error_handler(app)

API_PREFIX = "/v1"
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(users_router, prefix=API_PREFIX)
app.include_router(
    projects_router,
    prefix=API_PREFIX,
    dependencies=[Depends(get_current_user)],
)
actuarial_access = [Depends(require_roles("admin", "actuary"))]
app.include_router(imports_router, prefix=API_PREFIX, dependencies=actuarial_access)
app.include_router(variables_router, prefix=API_PREFIX, dependencies=actuarial_access)
# Milestone 1 routers (permission checks are declared on each endpoint).
app.include_router(modeling_router, prefix=API_PREFIX)
app.include_router(formulas_router, prefix=API_PREFIX, dependencies=actuarial_access)
app.include_router(system_router, prefix=API_PREFIX)
app.include_router(inputs_router, prefix=API_PREFIX)
app.include_router(scenarios_router, prefix=API_PREFIX)
app.include_router(projection_sets_router, prefix=API_PREFIX)
app.include_router(execution_router, prefix=API_PREFIX)
app.include_router(analysis_router, prefix=API_PREFIX)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
