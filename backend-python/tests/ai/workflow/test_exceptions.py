"""Tests for Workflow subsystem exceptions."""

from __future__ import annotations

from app.ai.workflow.exceptions import (
    WorkflowAccessDeniedError,
    WorkflowConcurrentUpdateError,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)


class TestWorkflowExceptions:
    def test_base_error_carries_code(self) -> None:
        error = WorkflowError("boom", code="workflow_error")

        assert str(error) == "boom"
        assert error.code == "workflow_error"

    def test_not_found_defaults(self) -> None:
        error = WorkflowNotFoundError()

        assert error.code == "workflow_not_found"

    def test_access_denied_defaults(self) -> None:
        error = WorkflowAccessDeniedError()

        assert error.code == "workflow_access_denied"

    def test_validation_error_defaults(self) -> None:
        error = WorkflowValidationError()

        assert error.code == "workflow_validation_error"

    def test_concurrent_update_defaults(self) -> None:
        error = WorkflowConcurrentUpdateError()

        assert error.code == "workflow_concurrent_update"
