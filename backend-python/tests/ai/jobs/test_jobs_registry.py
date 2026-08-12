"""Unit tests for JobHandlerRegistry."""

from __future__ import annotations

import pytest

from app.ai.jobs.exceptions import JobHandlerNotFoundError
from app.ai.jobs.models import BackgroundJob, JobResult, JobStatus
from app.ai.jobs.registry import JobHandlerRegistry
import datetime
import uuid


async def _noop_handler(job: BackgroundJob) -> JobResult:
    del job
    return JobResult(summary="ok")


async def _replacement_handler(job: BackgroundJob) -> JobResult:
    del job
    return JobResult(summary="replaced")


def _sample_job() -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=uuid.uuid4(),
        job_type="fixture",
        status=JobStatus.QUEUED,
        payload={"version": 1},
        attempt_count=0,
        max_attempts=3,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_register_and_resolve_handler() -> None:
    registry = JobHandlerRegistry()
    registry.register("fixture", _noop_handler)
    handler = registry.resolve("fixture")
    result = await handler(_sample_job())
    assert result.summary == "ok"


def test_resolve_unknown_handler_raises() -> None:
    registry = JobHandlerRegistry()
    with pytest.raises(JobHandlerNotFoundError, match="missing"):
        registry.resolve("missing")


@pytest.mark.anyio
async def test_reregister_replaces_handler() -> None:
    registry = JobHandlerRegistry()
    registry.register("fixture", _noop_handler)
    registry.register("fixture", _replacement_handler)
    result = await registry.resolve("fixture")(_sample_job())
    assert result.summary == "replaced"
