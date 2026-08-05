"""Node-level retry policy (Phase 8)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from app.ai.workflow.models import NodeRetryPolicy
from app.ai.workflow.retry.classifier import is_retryable_node_failure
from app.core.retry import retry_async

if TYPE_CHECKING:
    from app.core.config import Settings

T = TypeVar("T")


class RetryPolicy:
    """Wraps ``app/core/retry.py`` for workflow node retries (Phase 8)."""

    def __init__(
        self,
        policy: NodeRetryPolicy | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        if policy is not None:
            self._effective = policy
        elif settings is not None:
            self._effective = NodeRetryPolicy(
                max_retries=settings.workflow_max_node_retries,
                base_delay_seconds=settings.workflow_node_retry_base_delay_seconds,
            )
        else:
            self._effective = NodeRetryPolicy()

    def max_retries(self) -> int:
        """Return configured max retries for a node."""
        return self._effective.max_retries

    def base_delay_seconds(self) -> float:
        """Return configured base delay between retries for a node."""
        return self._effective.base_delay_seconds

    def max_attempts(self) -> int:
        """Return total attempts including the first try (1 + max_retries)."""
        return self._effective.max_retries + 1

    def is_retryable(self, exc: BaseException) -> bool:
        """Classify whether a node failure should be retried."""
        return is_retryable_node_failure(exc)

    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        on_retry: Callable[[int, BaseException], Awaitable[None] | None] | None = None,
    ) -> T:
        """Execute ``operation`` with workflow retry/backoff semantics."""
        attempt = 0

        async def _attempt() -> T:
            nonlocal attempt
            attempt += 1
            try:
                return await operation()
            except BaseException as exc:
                if on_retry is not None and self.is_retryable(exc):
                    maybe = on_retry(attempt, exc)
                    if maybe is not None:
                        await maybe
                raise

        return await retry_async(
            _attempt,
            max_attempts=self.max_attempts(),
            base_delay_seconds=self.base_delay_seconds(),
            is_retryable=self.is_retryable,
        )
