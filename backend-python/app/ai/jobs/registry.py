"""Job handler registry (stable public API after Phase 1)."""

from __future__ import annotations

from typing import Protocol

from app.ai.jobs.exceptions import JobHandlerNotFoundError
from app.ai.jobs.models import BackgroundJob, JobResult


class JobHandler(Protocol):
    """Async callable executed for one ``job_type``."""

    async def __call__(self, job: BackgroundJob) -> JobResult: ...


class JobHandlerRegistry:
    """In-memory ``job_type`` → handler map."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        """Register or replace a handler for ``job_type``."""
        self._handlers[job_type] = handler

    def resolve(self, job_type: str) -> JobHandler:
        handler = self._handlers.get(job_type)
        if handler is None:
            raise JobHandlerNotFoundError(
                f"No handler registered for job_type '{job_type}'."
            )
        return handler
