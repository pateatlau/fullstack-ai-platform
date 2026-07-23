"""Retry executor wrapping the shared ``retry_async`` utility (Phase 4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.ai.agent.interfaces.retry import RetryPolicy
from app.core.retry import retry_async

T = TypeVar("T")


async def retry_operation(
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
) -> T:
    """Run ``operation`` with retries governed by ``policy``."""
    return await retry_async(
        operation,
        max_attempts=max(1, policy.max_retries),
        base_delay_seconds=policy.base_delay_seconds,
        is_retryable=policy.is_retryable,
    )
