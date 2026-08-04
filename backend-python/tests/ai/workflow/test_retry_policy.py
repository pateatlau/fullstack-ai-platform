"""Tests for workflow ``RetryPolicy`` defaults."""

from __future__ import annotations

from app.ai.workflow.models import NodeRetryPolicy
from app.ai.workflow.retry.policy import RetryPolicy
from app.core.config import Settings


def test_retry_policy_uses_node_policy_when_provided() -> None:
    policy = RetryPolicy(NodeRetryPolicy(max_retries=5, base_delay_seconds=2.0))

    assert policy.max_retries() == 5
    assert policy.base_delay_seconds() == 2.0


def test_retry_policy_falls_back_to_settings_defaults() -> None:
    settings = Settings(
        openai_api_key="test-key",
        workflow_max_node_retries=4,
        workflow_node_retry_base_delay_seconds=0.5,
    )
    policy = RetryPolicy(settings=settings)

    assert policy.max_retries() == 4
    assert policy.base_delay_seconds() == 0.5


def test_retry_policy_falls_back_to_canonical_model_defaults() -> None:
    policy = RetryPolicy()

    assert policy.max_retries() == 3
    assert policy.base_delay_seconds() == 1.0
