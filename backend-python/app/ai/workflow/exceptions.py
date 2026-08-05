"""Workflow subsystem exceptions (public API — stable after Phase 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.workflow.models import WorkflowNodeExecution


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


WORKFLOW_CONCURRENT_RUN_UPDATE_MSG = (
    "Workflow run checkpoint was modified concurrently; retry the update."
)


class WorkflowConcurrentUpdateError(WorkflowError):
    """Raised when an optimistic run update loses a concurrent write."""

    def __init__(
        self,
        message: str = WORKFLOW_CONCURRENT_RUN_UPDATE_MSG,
    ) -> None:
        super().__init__(message, code="workflow_concurrent_update")


class WorkflowDecisionConflictError(WorkflowError):
    """Raised when an approval decision conflicts with an existing decision."""

    def __init__(
        self,
        message: str = "Approval decision conflicts with an existing decision.",
    ) -> None:
        super().__init__(message, code="workflow_decision_conflict")


class WorkflowApprovalCasMissError(WorkflowError):
    """Raised when an approval CAS update finds the node already decided."""

    def __init__(
        self,
        execution: WorkflowNodeExecution,
        *,
        message: str = "Approval node is no longer waiting for a decision.",
    ) -> None:
        super().__init__(message, code="workflow_approval_cas_miss")
        self.execution = execution
