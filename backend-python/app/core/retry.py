"""Shared async retry utility for external HTTP/API calls."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0


def is_retryable_http_status(status_code: int) -> bool:
    """Return whether an HTTP status code should trigger a retry."""
    return status_code in {429, 503}


def is_retryable_exception(exc: BaseException) -> bool:
    """Return whether an exception should trigger a retry."""
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_http_status(exc.response.status_code)
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return True
    return False


def _backoff_delay(attempt: int, base_delay: float) -> float:
    """Exponential backoff with jitter (attempt is 0-based)."""
    delay = base_delay * (2**attempt)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    is_retryable: Callable[[BaseException], bool] | None = None,
) -> T:
    """Run ``operation`` with retries on transient external-service failures."""
    retryable = is_retryable or is_retryable_exception
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return await operation()
        except BaseException as exc:
            last_exc = exc
            if attempt >= max_attempts - 1 or not retryable(exc):
                raise
            await asyncio.sleep(_backoff_delay(attempt, base_delay_seconds))

    assert last_exc is not None
    raise last_exc
