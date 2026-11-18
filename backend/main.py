"""SeniorLife OS — Main FastAPI Application Entrypoint.

Configures:
- FastAPI app instance with metadata from application.yml
- Global exception handlers (Spring @ControllerAdvice equivalent)
- CORS middleware for React / Next.js frontend
- Standardized API routes (/api/v1/health, /api/v1/workflows/*)
- Demo Mode scenario routes (/api/v1/demo/*) when app.demo_mode=true
- Conditional Prometheus metrics (/metrics) when metrics.enabled=true
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from backend.api import auth_router, demo_router, documents_router, health_requests_router, profile_router
from backend.api.deps import is_production
from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config
from backend.errors import BadRequestException, NotFoundException, register_global_error_handlers
from backend.graphs.benefits_graph import build_benefits_graph
from backend.graphs.document_graph import build_document_graph
from pyctuator.environment.scrubber import SecretScrubber, default_keys_to_scrub
from pyctuator.pyctuator import Pyctuator

from backend.common.request_id import RequestIdLogFilter, RequestIdMiddleware

import re
import sys
from uvicorn.logging import ColourizedFormatter

# Configure aligned, colourized logging matching Uvicorn / FastAPI server logs; each line shows
# its request's ID ("-" outside a request), the same as the X-Request-ID header.
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(
    ColourizedFormatter(fmt="%(levelprefix)s [%(request_id)s] %(message)s", use_colors=True)
)
_log_handler.addFilter(RequestIdLogFilter())
logging.root.handlers = [_log_handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger(__name__)

config = get_config()

app = FastAPI(
    title=config.app.name,
    version=config.app.version,
    description="Accessible, consent-based case manager and workflow assistant for independent living.",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 1. CORS Middleware (Permits Frontend requests)
def install_cors(target: FastAPI, app_config: Any) -> None:
    """Only the web app's own origins (cors.allowed_origins, CORS_ALLOWED_ORIGINS) may call the API
    from a browser. Sign-in is a Bearer token in a header, never a cookie, so no credentials
    (fastapi-backend Decision 6)."""
    target.add_middleware(
        CORSMiddleware,
        allow_origins=app_config.cors.allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


install_cors(app, config)
app.add_middleware(RequestIdMiddleware)  # added last, so it is outside CORS: every answer has the ID

# 2. Register Global Error Handler (@ControllerAdvice)
register_global_error_handlers(app)

# 3. Spring Boot Actuator Endpoints via Pyctuator (/actuator, /actuator/health, /actuator/info, /actuator/metrics, etc.)
# Pyctuator hides values whose names hold key/secret/password/token; also hide *api* (APIFY_API)
# and *dsn* / *credential* names.
ACTUATOR_HIDDEN_NAMES = re.compile(default_keys_to_scrub.pattern + "|.*api.*|.*dsn.*|.*credential.*", re.IGNORECASE)


def install_actuator(target: FastAPI, app_config: Any) -> bool:
    """Add /actuator to target, unless it is switched off or this is production. /actuator has no
    sign-in: it shows environment values, request URLs (names sit in query strings) and lets
    anyone change log levels, so production never gets it (fastapi-backend Decision 5)."""
    actuator = getattr(app_config, "actuator", None)
    if not (actuator and actuator.enabled) or is_production(app_config):
        return False
    pyctuator = Pyctuator(
        target,
        app_name=app_config.app.name,
        app_url=f"http://{app_config.server.host}:{app_config.server.port}",
        pyctuator_endpoint_url=f"http://{app_config.server.host}:{app_config.server.port}/actuator",
        registration_url=None,
        app_description="SeniorLife OS Backend",
        metadata={
            "environment": app_config.app.environment,
            "version": app_config.app.version,
        },
        additional_app_info={
            "build": {
                "version": app_config.app.version,
            },
            "environment": app_config.app.environment,
        },
    )
    pyctuator.set_secret_scrubber(SecretScrubber(ACTUATOR_HIDDEN_NAMES).scrub_secrets)
    return True


ACTUATOR_ON = install_actuator(app, config)
if ACTUATOR_ON:
    logger.info("Spring Boot Actuator active at /actuator via Pyctuator")

# 4. Conditional Demo Mode Router
if config.app.demo_mode:
    logger.info("Demo mode is ACTIVE. Registering /api/v1/demo endpoints.")
    app.include_router(demo_router)


# 4b. Sign-in and profile (needs DATABASE_URL and JWT_SECRET), then the document vault (upload,
# read, review; needs DATABASE_URL and storage keys), see backend/README.md
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(documents_router)
# 4c. Doctor search (needs DATABASE_URL and SENIORLIFE_CHECKPOINT_KEY; GEOAPIFY_API_KEY to look places up)
app.include_router(health_requests_router)


# 5. DTO Schemas (camelCase via CamelModel)
class HealthStatusDto(CamelModel):
    service_status: str
    environment: str
    app_version: str
    demo_mode: bool
    actuator_enabled: bool
    metrics_enabled: bool = False


class WorkflowTriggerRequest(CamelModel):
    state: dict[str, Any]


def raw_workflows_allowed() -> None:
    """The /workflows/*/run endpoints run whatever state they are sent: the actor, a review
    decision, consent flags. So they are for local demos only: demo mode on and not production
    (fastapi-backend Decision 3). Anywhere else they answer 404."""
    app_config = get_config()
    if not app_config.app.demo_mode or is_production(app_config):
        raise NotFoundException("Not found")


def page_fixtures_only(state: dict[str, Any]) -> None:
    """A state naming a file would make OCR read any PDF or image on this server and return its
    text. Files come in only through POST /api/v1/documents, which stores them itself."""
    document = state.get("document")
    if isinstance(document, dict) and "file_path" in document:
        raise BadRequestException("This demo endpoint reads page fixtures only (document.pages); "
                                  "upload a file with POST /api/v1/documents")


RAW_WORKFLOWS_LISTED = config.app.demo_mode and not is_production(config)


# 6. Standard Routes
@app.get("/", include_in_schema=False)
def root():
    """Redirect root to Swagger OpenAPI documentation."""
    return RedirectResponse(url="/docs")


@app.get(
    "/api/v1/health",
    response_model=ApiResponse[HealthStatusDto],
    status_code=status.HTTP_200_OK,
    summary="Application Health Check",
)
def health_check() -> ApiResponse[HealthStatusDto]:
    """Returns application health status in standardized ApiResponse envelope."""
    dto = HealthStatusDto(
        service_status="UP",
        environment=config.app.environment,
        app_version=config.app.version,
        demo_mode=config.app.demo_mode,
        actuator_enabled=ACTUATOR_ON,
        metrics_enabled=False,
    )
    return ApiResponse.of(data=dto, version=config.app.version)


@app.post(
    "/api/v1/workflows/document-intake/run",
    response_model=ApiResponse[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="Execute Document Intake Graph (local demo only)",
    dependencies=[Depends(raw_workflows_allowed)],
    include_in_schema=RAW_WORKFLOWS_LISTED,
)
def run_document_intake(request: WorkflowTriggerRequest) -> ApiResponse[dict[str, Any]]:
    """Execute Document Intake LangGraph workflow and return confirmed vault record or status."""
    page_fixtures_only(request.state)
    logger.info("Executing Document Intake workflow")
    graph = build_document_graph()
    result = graph.invoke(request.state)
    return ApiResponse.of(data=result, version=config.app.version)


@app.post(
    "/api/v1/workflows/benefits/run",
    response_model=ApiResponse[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="Execute Benefits Case Assessment Graph (local demo only)",
    dependencies=[Depends(raw_workflows_allowed)],
    include_in_schema=RAW_WORKFLOWS_LISTED,
)
def run_benefits_case(request: WorkflowTriggerRequest) -> ApiResponse[dict[str, Any]]:
    """Execute Benefits Case Manager LangGraph workflow and return assessment results."""
    logger.info("Executing Benefits Case workflow")
    graph = build_benefits_graph()
    result = graph.invoke(request.state)
    return ApiResponse.of(data=result, version=config.app.version)
