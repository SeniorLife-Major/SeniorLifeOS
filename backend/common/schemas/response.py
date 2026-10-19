"""Standardized API Response Envelopes.

Enforces:
- CamelCase JSON serialization for all API contracts.
- Typed ApiResponse[T] for success payloads (data: List[T] or T).
- Typed ApiErrorResponse for failure payloads (status, error: {code, message, details}, requestUri, meta).
- Metadata envelope (version, serverTime, requestId).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class CamelModel(BaseModel):
    """Base Pydantic model enforcing camelCase serialization for JSON outputs."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class MetaResponse(CamelModel):
    """Metadata block attached to every API response."""

    version: str = "1.0.0"
    server_time: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the server response",
    )
    request_id: str | None = Field(
        default=None,
        description="Trace/Request correlation ID",
    )


class ApiResponse(CamelModel, Generic[T]):
    """Standard success API envelope.
    
    JSON Output:
    {
      "status": "SUCCESS",
      "data": [...],
      "meta": {
        "version": "1.0.0",
        "serverTime": "2026-10-19T17:10:00Z"
      }
    }
    """

    status: Literal["SUCCESS"] = "SUCCESS"
    data: T
    meta: MetaResponse = Field(default_factory=MetaResponse)

    @classmethod
    def of(
        cls,
        data: T,
        meta: MetaResponse | None = None,
        version: str = "1.0.0",
        request_id: str | None = None,
    ) -> ApiResponse[T]:
        meta_obj = meta or MetaResponse(version=version, request_id=request_id)
        return cls(status="SUCCESS", data=data, meta=meta_obj)


class ErrorDetail(CamelModel):
    """Structured error payload specifying machine-readable code, message, and details."""

    code: str = Field(description="Domain-specific error code (e.g. CASE_NOT_FOUND, VALIDATION_ERROR)")
    message: str = Field(description="Human-readable explanation of the error")
    details: Any | None = Field(default=None, description="Optional granular field errors or context")


class ApiErrorResponse(CamelModel):
    """Standard error API envelope.
    
    JSON Output:
    {
      "status": "ERROR",
      "error": {
        "code": "RESOURCE_NOT_FOUND",
        "message": "Case not found",
        "details": null
      },
      "requestUri": "/api/v1/cases/123",
      "meta": {
        "version": "1.0.0",
        "serverTime": "2026-10-19T17:10:00Z"
      }
    }
    """

    status: Literal["ERROR"] = "ERROR"
    error: ErrorDetail
    request_uri: str = Field(description="Target URI where the request failed")
    meta: MetaResponse = Field(default_factory=MetaResponse)

    @classmethod
    def of(
        cls,
        code: str,
        message: str,
        request_uri: str,
        details: Any | None = None,
        version: str = "1.0.0",
        request_id: str | None = None,
    ) -> ApiErrorResponse:
        return cls(
            status="ERROR",
            error=ErrorDetail(code=code, message=message, details=details),
            request_uri=request_uri,
            meta=MetaResponse(version=version, request_id=request_id),
        )
