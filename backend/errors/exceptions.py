"""Domain and Application Exceptions.

Follows Spring Boot custom exception architecture where services throw typed exceptions
and a GlobalErrorHandler intercepts them to render standardized ApiErrorResponse envelopes.
"""

from __future__ import annotations

from typing import Any
from .error_codes import ErrorCode


class AppException(Exception):
    """Base application exception with HTTP status code, error code, and optional details."""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode | str = ErrorCode.INTERNAL_SERVER_ERROR,
        status_code: int = 500,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code.value if isinstance(error_code, ErrorCode) else error_code
        self.status_code = status_code
        self.details = details


class BadRequestException(AppException):
    """HTTP 400 Bad Request."""

    def __init__(
        self,
        message: str = "Bad request",
        error_code: ErrorCode | str = ErrorCode.BAD_REQUEST,
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, status_code=400, details=details)


class UnauthorizedException(AppException):
    """HTTP 401 Unauthorized."""

    def __init__(
        self,
        message: str = "Authentication required",
        error_code: ErrorCode | str = ErrorCode.UNAUTHORIZED,
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, status_code=401, details=details)


class ForbiddenException(AppException):
    """HTTP 403 Forbidden / Access Denied."""

    def __init__(
        self,
        message: str = "Access denied",
        error_code: ErrorCode | str = ErrorCode.FORBIDDEN,
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, status_code=403, details=details)


class NotFoundException(AppException):
    """HTTP 404 Not Found."""

    def __init__(
        self,
        message: str = "Resource not found",
        error_code: ErrorCode | str = ErrorCode.NOT_FOUND,
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, status_code=404, details=details)


class ConflictException(AppException):
    """HTTP 409 Conflict (e.g. optimistic locking, version mismatch)."""

    def __init__(
        self,
        message: str = "Resource state conflict",
        error_code: ErrorCode | str = ErrorCode.CONFLICT,
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, status_code=409, details=details)


class ValidationException(AppException):
    """HTTP 422 Unprocessable Entity / Validation failure."""

    def __init__(
        self,
        message: str = "Validation failed",
        error_code: ErrorCode | str = ErrorCode.VALIDATION_FAILED,
        details: Any | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, status_code=422, details=details)


# Domain-Specific Exceptions
class CaseNotFoundException(NotFoundException):
    def __init__(self, case_id: str) -> None:
        super().__init__(
            message=f"Case with ID '{case_id}' was not found",
            error_code=ErrorCode.CASE_NOT_FOUND,
            details={"caseId": case_id},
        )


class CaseVersionConflictException(ConflictException):
    def __init__(self, case_id: str, current_version: int, expected_version: int) -> None:
        super().__init__(
            message=f"Case '{case_id}' has been modified by another process. Current version: {current_version}, expected: {expected_version}",
            error_code=ErrorCode.CASE_VERSION_CONFLICT,
            details={
                "caseId": case_id,
                "currentVersion": current_version,
                "expectedVersion": expected_version,
            },
        )


class DocumentNotFoundException(NotFoundException):
    def __init__(self, document_id: str) -> None:
        super().__init__(
            message=f"Document with ID '{document_id}' was not found",
            error_code=ErrorCode.DOCUMENT_NOT_FOUND,
            details={"documentId": document_id},
        )


class ConsentDeniedException(ForbiddenException):
    def __init__(self, actor_id: str, resource_id: str, required_scope: str) -> None:
        super().__init__(
            message=f"Actor '{actor_id}' does not have '{required_scope}' permission for resource '{resource_id}'",
            error_code=ErrorCode.CONSENT_DENIED,
            details={
                "actorId": actor_id,
                "resourceId": resource_id,
                "requiredScope": required_scope,
            },
        )
