"""Background Jobs exception hierarchy (stable public API after Phase 1)."""

from __future__ import annotations


class JobsError(Exception):
    """Base class for Background Jobs errors."""


class JobNotFoundError(JobsError):
    """Raised when a requested job row does not exist."""


class JobHandlerNotFoundError(JobsError):
    """Raised when no handler is registered for a ``job_type``."""


class ScheduleNotFoundError(JobsError):
    """Raised when a requested schedule row does not exist."""


class JobConcurrencyError(JobsError):
    """Raised when an optimistic-concurrency update affects zero rows."""
