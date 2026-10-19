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
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from backend.api import demo_router
from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config
from backend.errors import register_global_error_handlers
from backend.graphs.benefits_graph import build_benefits_graph
from backend.graphs.document_graph import build_document_graph
from pyctuator.pyctuator import Pyctuator

import sys
from uvicorn.logging import ColourizedFormatter

# Configure aligned, colourized logging matching Uvicorn / FastAPI server logs
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(
    ColourizedFormatter(fmt="%(levelprefix)s %(message)s", use_colors=True)
)
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Register Global Error Handler (@ControllerAdvice)
register_global_error_handlers(app)

# 3. Spring Boot Actuator Endpoints via Pyctuator (/actuator, /actuator/health, /actuator/info, /actuator/metrics, etc.)
if getattr(config, "actuator", None) and config.actuator.enabled:
    Pyctuator(
        app,
        app_name=config.app.name,
        app_url=f"http://{config.server.host}:{config.server.port}",
        pyctuator_endpoint_url=f"http://{config.server.host}:{config.server.port}/actuator",
        registration_url=None,
        app_description="SeniorLife OS Backend",
        metadata={
            "environment": config.app.environment,
            "version": config.app.version,
        },
        additional_app_info={
            "build": {
                "version": config.app.version,
            },
            "environment": config.app.environment,
        },
    )
    logger.info("Spring Boot Actuator active at /actuator via Pyctuator")

# 4. Conditional Demo Mode Router
if config.app.demo_mode:
    logger.info("Demo mode is ACTIVE. Registering /api/v1/demo endpoints.")
    app.include_router(demo_router)


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
        actuator_enabled=config.actuator.enabled if getattr(config, "actuator", None) else True,
        metrics_enabled=False,
    )
    return ApiResponse.of(data=dto, version=config.app.version)


@app.post(
    "/api/v1/workflows/document-intake/run",
    response_model=ApiResponse[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="Execute Document Intake Graph",
)
def run_document_intake(request: WorkflowTriggerRequest) -> ApiResponse[dict[str, Any]]:
    """Execute Document Intake LangGraph workflow and return confirmed vault record or status."""
    logger.info("Executing Document Intake workflow")
    graph = build_document_graph()
    result = graph.invoke(request.state)
    return ApiResponse.of(data=result, version=config.app.version)


@app.post(
    "/api/v1/workflows/benefits/run",
    response_model=ApiResponse[dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="Execute Benefits Case Assessment Graph",
)
def run_benefits_case(request: WorkflowTriggerRequest) -> ApiResponse[dict[str, Any]]:
    """Execute Benefits Case Manager LangGraph workflow and return assessment results."""
    logger.info("Executing Benefits Case workflow")
    graph = build_benefits_graph()
    result = graph.invoke(request.state)
    return ApiResponse.of(data=result, version=config.app.version)
