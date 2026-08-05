"""Tests for workflow node retry classification and policy (Phase 8)."""

from __future__ import annotations

import httpx
import pytest

from app.ai.workflow.models import NodeRetryPolicy
from app.ai.workflow.nodes.base import WorkflowNodeExecutionError
from app.ai.workflow.retry.classifier import (
    is_non_retryable_node_error,
    is_retryable_node_error,
    is_retryable_node_failure,
)
from app.ai.workflow.retry.policy import RetryPolicy
from app.core.config import Settings


def test_retryable_error_codes() -> None:
    assert is_retryable_node_error("timeout")
    assert is_retryable_node_error("rate_limit")
    assert is_retryable_node_error("service_unavailable")
    assert is_retryable_node_error("provider_error")


def test_non_retryable_error_codes_fail_fast() -> None:
    assert is_non_retryable_node_error("invalid_config")
    assert is_non_retryable_node_error("not_found")
    assert is_non_retryable_node_error("forbidden")


def test_retryable_node_failure_classifies_transient_exceptions() -> None:
    assert is_retryable_node_failure(httpx.TimeoutException("timeout"))
    assert is_retryable_node_failure(
        WorkflowNodeExecutionError("rate limited", error_code="rate_limit")
    )


def test_non_retryable_node_failure_classifies_validation_errors() -> None:
    assert not is_retryable_node_failure(
        WorkflowNodeExecutionError("bad config", error_code="invalid_config")
    )
    assert not is_retryable_node_failure(
        WorkflowNodeExecutionError("missing tool", error_code="not_found")
    )


@pytest.mark.anyio
async def test_retry_policy_retries_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.core.retry.asyncio.sleep", fake_sleep)

    policy = RetryPolicy(NodeRetryPolicy(max_retries=2, base_delay_seconds=0.01))
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise WorkflowNodeExecutionError("temporary", error_code="timeout")
        return "ok"

    result = await policy.run(flaky)

    assert result == "ok"
    assert attempts == 3
    assert len(sleeps) == 2


@pytest.mark.anyio
async def test_retry_policy_exhaustion_fails_fast() -> None:
    policy = RetryPolicy(NodeRetryPolicy(max_retries=1, base_delay_seconds=0.0))

    async def always_fail() -> None:
        raise WorkflowNodeExecutionError("temporary", error_code="timeout")

    with pytest.raises(WorkflowNodeExecutionError):
        await policy.run(always_fail)


def test_retry_policy_max_attempts_includes_first_try() -> None:
    policy = RetryPolicy(NodeRetryPolicy(max_retries=3, base_delay_seconds=1.0))
    assert policy.max_attempts() == 4


def test_retry_policy_uses_settings_defaults() -> None:
    settings = Settings(
        openai_api_key="test-key",
        workflow_max_node_retries=4,
        workflow_node_retry_base_delay_seconds=0.5,
    )
    policy = RetryPolicy(settings=settings)

    assert policy.max_retries() == 4
    assert policy.base_delay_seconds() == 0.5
