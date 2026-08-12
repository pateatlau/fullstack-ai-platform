"""Retry/backoff helpers for Background Jobs (stable public API after Phase 1)."""

from __future__ import annotations


class NonRetryableJobError(Exception):
    """Signal a handler failure that must dead-letter immediately."""


def compute_backoff_seconds(
    attempt_count: int,
    *,
    base: float,
    cap: float,
) -> float:
    """Exponential backoff capped at ``cap`` seconds."""
    if attempt_count < 0:
        raise ValueError("attempt_count must be non-negative.")
    if base < 0:
        raise ValueError("base must be non-negative.")
    if cap < 0:
        raise ValueError("cap must be non-negative.")
    return min(base * (2**attempt_count), cap)
