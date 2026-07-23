"""Retry policy protocol (public API — stable after Phase 1)."""

from __future__ import annotations

from typing import Protocol


class RetryPolicy(Protocol):
    """Classifies whether an operation failure should be retried."""

    @property
    def max_retries(self) -> int: ...

    @property
    def base_delay_seconds(self) -> float: ...

    def is_retryable(self, exc: BaseException) -> bool: ...
