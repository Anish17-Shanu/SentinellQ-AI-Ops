"""Typed API errors shared by every transport."""

from __future__ import annotations


class ApiError(Exception):
    status = 500
    code = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        field: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.field = field
        self.details = details or {}

    def to_payload(self, request_id: str) -> dict:
        error: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "request_id": request_id,
        }
        if self.field:
            error["field"] = self.field
        if self.details:
            error["details"] = self.details
        return {"error": error}


class ValidationError(ApiError, ValueError):
    status = 400
    code = "validation_error"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message, field=field)


class AuthError(ApiError):
    status = 401
    code = "unauthorized"


class ForbiddenError(ApiError):
    status = 403
    code = "forbidden"


class NotFoundError(ApiError):
    status = 404
    code = "not_found"


class ConflictError(ApiError):
    status = 409
    code = "conflict"


class PayloadTooLargeError(ApiError):
    status = 413
    code = "payload_too_large"


class RateLimitError(ApiError):
    status = 429
    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__("Rate limit exceeded", details={"retry_after": retry_after})
        self.retry_after = retry_after


class ConfigError(RuntimeError):
    """Raised at startup when configuration is invalid."""
