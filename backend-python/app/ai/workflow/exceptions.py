"""Workflow subsystem exceptions (public API — stable after Phase 1)."""

from __future__ import annotations


class WorkflowError(Exception):
    """Base exception for Workflow subsystem errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class WorkflowNotFoundError(WorkflowError):
    """Raised when a requested workflow definition or run does not exist."""

    def __init__(self, message: str = "Workflow resource not found.") -> None:
        super().__init__(message, code="workflow_not_found")


class WorkflowAccessDeniedError(WorkflowError):
    """Raised when a caller attempts to access a workflow they do not own."""

    def __init__(self, message: str = "Access to this workflow is denied.") -> None:
        super().__init__(message, code="workflow_access_denied")


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow definition or run request fails validation."""

    def __init__(self, message: str = "Workflow validation failed.") -> None:
        super().__init__(message, code="workflow_validation_error")
