"""Workflow node retry classification (Phase 8)."""

from __future__ import annotations

import asyncio

from pydantic import ValidationError

from app.ai.workflow.nodes.base import WorkflowNodeExecutionError
from app.core.retry import is_retryable_exception

# Tool / node error codes eligible for workflow node retry (Part I retry table).
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "timeout",
        "rate_limit",
        "service_unavailable",
        "provider_error",
    }
)

# Failures that must never be retried at the workflow layer.
_NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        "invalid_config",
        "not_found",
        "forbidden",
        "unauthorized",
        "auth_error",
    }
)


def is_non_retryable_node_error(error_code: str | None) -> bool:
    """Return True when a normalized node error code must fail fast."""
    if error_code in _NON_RETRYABLE_ERROR_CODES:
        return True
    return False


def is_retryable_node_error(error_code: str | None) -> bool:
    """Return True when a normalized node error code may be retried."""
    if error_code in _RETRYABLE_ERROR_CODES:
        return True
    return False


def is_retryable_node_failure(exc: BaseException) -> bool:
    """Return True for transient node failures eligible for workflow retry."""
    if isinstance(exc, WorkflowNodeExecutionError):
        if is_non_retryable_node_error(exc.error_code):
            return False
        if is_retryable_node_error(exc.error_code):
            return True
        return False
    if isinstance(
        exc, (ValidationError, PermissionError, LookupError, FileNotFoundError)
    ):
        return False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    return is_retryable_exception(exc)
