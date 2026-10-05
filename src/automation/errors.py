"""Error types for automation. Every failure is classified so the pipeline can decide what to do."""

from __future__ import annotations

from enum import StrEnum


class ErrorType(StrEnum):
    TEMPORARY_ERROR = "temporary_error"
    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMIT = "rate_limit"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    INTERNAL_ERROR = "internal_error"


RETRYABLE = frozenset(
    {ErrorType.TEMPORARY_ERROR, ErrorType.RATE_LIMIT, ErrorType.EXTERNAL_SERVICE_ERROR}
)


class AutomationError(Exception):
    def __init__(self, error_type: ErrorType, message: str) -> None:
        super().__init__(f"{error_type}: {message}")
        self.error_type = error_type
        self.message = message

    @property
    def retryable(self) -> bool:
        """Validation, authentication and internal errors stop the run. They are never retried."""
        return self.error_type in RETRYABLE
