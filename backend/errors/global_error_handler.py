"""Global Exception Handler.

Mirrors Spring Boot's @ControllerAdvice / @ExceptionHandler. Intercepts all exceptions,
maps them to appropriate HTTP status codes, and packages them into the standardized
ApiErrorResponse envelope with camelCase keys.
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..common.schemas.response import ApiErrorResponse
from ..core.config import get_config
from .error_codes import ErrorCode
from .exceptions import AppException

logger = logging.getLogger(__name__)


def _format_pydantic_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert raw Pydantic validation errors into clean camelCase error details."""
    formatted = []
    for err in errors:
        loc = err.get("loc", [])
        # Skip 'body' prefix if present
        field_path = ".".join(str(item) for item in loc if item != "body")
        formatted.append({
            "field": field_path,
            "message": err.get("msg", "Invalid value"),
            "type": err.get("type", "value_error"),
        })
    return formatted


def register_global_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI application instance."""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        logger.warning(
            "Application exception: uri=%s code=%s status=%d message=%s",
            request.url.path,
            exc.error_code,
            exc.status_code,
            exc.message,
        )
        response_data = ApiErrorResponse.of(
            code=exc.error_code,
            message=exc.message,
            request_uri=request.url.path,
            details=exc.details,
            version=get_config().app.version,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=response_data.model_dump(by_alias=True, mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors = _format_pydantic_errors(exc.errors())
        logger.warning("Validation error on uri=%s: %s", request.url.path, field_errors)
        response_data = ApiErrorResponse.of(
            code=ErrorCode.VALIDATION_FAILED.value,
            message="Request validation failed. Check 'details' for field-level errors.",
            request_uri=request.url.path,
            details=field_errors,
            version=get_config().app.version,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=response_data.model_dump(by_alias=True, mode="json"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_map = {
            status.HTTP_400_BAD_REQUEST: ErrorCode.BAD_REQUEST.value,
            status.HTTP_401_UNAUTHORIZED: ErrorCode.UNAUTHORIZED.value,
            status.HTTP_403_FORBIDDEN: ErrorCode.FORBIDDEN.value,
            status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND.value,
            status.HTTP_409_CONFLICT: ErrorCode.CONFLICT.value,
        }
        error_code = code_map.get(exc.status_code, ErrorCode.INTERNAL_SERVER_ERROR.value)
        message = str(exc.detail) if exc.detail else "HTTP error"

        response_data = ApiErrorResponse.of(
            code=error_code,
            message=message,
            request_uri=request.url.path,
            version=get_config().app.version,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=response_data.model_dump(by_alias=True, mode="json"),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled internal exception on uri=%s: %s", request.url.path, exc)
        config = get_config()
        message = (
            f"Internal server error: {str(exc)}"
            if config.app.debug
            else "An unexpected internal server error occurred. Please try again later."
        )
        response_data = ApiErrorResponse.of(
            code=ErrorCode.INTERNAL_SERVER_ERROR.value,
            message=message,
            request_uri=request.url.path,
            version=config.app.version,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=response_data.model_dump(by_alias=True, mode="json"),
        )
