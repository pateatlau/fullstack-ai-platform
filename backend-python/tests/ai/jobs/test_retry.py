"""Unit tests for retry helpers."""

from __future__ import annotations

import pytest

from app.ai.jobs.retry import NonRetryableJobError, compute_backoff_seconds


def test_compute_backoff_exponential_and_capped() -> None:
    assert compute_backoff_seconds(0, base=5.0, cap=300.0) == 5.0
    assert compute_backoff_seconds(1, base=5.0, cap=300.0) == 10.0
    assert compute_backoff_seconds(2, base=5.0, cap=300.0) == 20.0
    assert compute_backoff_seconds(10, base=5.0, cap=300.0) == 300.0


def test_compute_backoff_rejects_negative_attempt_count() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        compute_backoff_seconds(-1, base=5.0, cap=300.0)


def test_non_retryable_job_error_is_exception() -> None:
    assert issubclass(NonRetryableJobError, Exception)
