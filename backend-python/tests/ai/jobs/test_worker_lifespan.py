"""Background Jobs lifespan wiring tests (Epic 10 Phase 2)."""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.jobs.background import start_background_jobs, stop_background_jobs
from app.ai.jobs.models import JobStatus
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.ai.jobs.worker import JobWorker
from app.core.config import Settings
from tests.ai.jobs.conftest import (
    background_job_schedules_table_available,
    background_jobs_table_available,
    make_queue_session_factory,
)


@pytest.mark.anyio
async def test_background_jobs_flag_off_does_not_start_tasks() -> None:
    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=False,
    )
    runtime = await start_background_jobs(settings)
    assert runtime is None


@pytest.mark.anyio
async def test_background_jobs_flag_on_starts_worker_and_scheduler(
    db_session,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        background_jobs_worker_poll_interval_seconds=3600,
        background_jobs_scheduler_poll_interval_seconds=3600,
        background_jobs_handler_timeout_seconds=30,
        background_jobs_claim_lease_seconds=300,
    )

    factory = make_queue_session_factory(db_session.bind)
    store = PostgresJobScheduleStore(factory)
    due_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)
    schedule = await store.insert_schedule(
        name="lifespan-fixture",
        job_type="hitl_approval_expiry_sweep",
        payload={"version": 1},
        interval_seconds=300,
        next_run_at=due_at,
    )

    with patch("app.ai.jobs.background.get_sessionmaker", return_value=factory):
        runtime = await start_background_jobs(settings)

    assert runtime is not None
    assert len(runtime.tasks) == 2
    assert not runtime.tasks[0].done()
    assert not runtime.tasks[1].done()
    try:
        await runtime.scheduler.tick_once()
        queue = PostgresJobQueue(factory, settings)
        jobs = await queue.list(job_type="hitl_approval_expiry_sweep")
        assert len(jobs) == 1
        assert jobs[0].schedule_id == schedule.id
        assert jobs[0].status is JobStatus.QUEUED
    finally:
        await stop_background_jobs(runtime)


@pytest.mark.anyio
async def test_background_jobs_shutdown_stops_claim_activity(
    db_session,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        background_jobs_worker_poll_interval_seconds=3600,
        background_jobs_scheduler_poll_interval_seconds=3600,
        background_jobs_handler_timeout_seconds=30,
        background_jobs_claim_lease_seconds=300,
    )
    factory = make_queue_session_factory(db_session.bind)
    poll_mock = AsyncMock(return_value=None)

    with patch("app.ai.jobs.background.get_sessionmaker", return_value=factory):
        with patch.object(JobWorker, "poll_once", poll_mock):
            runtime = await start_background_jobs(settings)
            assert runtime is not None

            for _ in range(50):
                if poll_mock.call_count >= 1:
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("worker poll_once was never invoked")

            calls_at_stop = poll_mock.call_count
            await stop_background_jobs(runtime)

            await asyncio.sleep(0.1)
            assert poll_mock.call_count == calls_at_stop
