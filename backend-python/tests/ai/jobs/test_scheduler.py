"""JobScheduler tests (Epic 10 Phase 2)."""

from __future__ import annotations

import asyncio
import datetime

import pytest

from app.ai.jobs.models import JobStatus, ScheduleStatus
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.ai.jobs.scheduler import JobScheduler, compute_advanced_next_run_at
from app.core.config import Settings
from tests.ai.jobs.conftest import (
    background_job_schedules_table_available,
    background_jobs_table_available,
    make_queue_session_factory,
)


@pytest.fixture
def scheduler_settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        background_jobs_default_max_attempts=3,
        background_jobs_scheduler_poll_interval_seconds=60,
    )


def _make_scheduler(
    db_session, settings: Settings
) -> tuple[JobScheduler, PostgresJobQueue, PostgresJobScheduleStore]:
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    store = PostgresJobScheduleStore(factory)
    scheduler = JobScheduler(queue=queue, store=store, settings=settings)
    return scheduler, queue, store


@pytest.mark.anyio
async def test_scheduler_tick_enqueues_job_and_advances_next_run_at(
    db_session,
    scheduler_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    scheduler, queue, store = _make_scheduler(db_session, scheduler_settings)
    due_at = datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.UTC)
    schedule = await store.insert_schedule(
        name="fixture-sweep",
        job_type="fixture_sweep",
        payload={"version": 1},
        interval_seconds=300,
        next_run_at=due_at,
    )

    now = due_at + datetime.timedelta(seconds=30)
    await scheduler._process_schedule(schedule, now)

    jobs = await queue.list(job_type="fixture_sweep")
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.QUEUED
    assert jobs[0].schedule_id == schedule.id
    assert jobs[0].idempotency_key == f"fixture-sweep:{due_at.isoformat()}"

    updated = await store.get(schedule.id)
    assert updated is not None
    assert updated.next_run_at == due_at + datetime.timedelta(seconds=300)
    assert updated.version == schedule.version + 1


@pytest.mark.anyio
async def test_scheduler_missed_ticks_enqueue_once_and_skip_intermediate(
    db_session,
    scheduler_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    scheduler, queue, store = _make_scheduler(db_session, scheduler_settings)
    due_at = datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.UTC)
    schedule = await store.insert_schedule(
        name="fixture-missed",
        job_type="fixture_missed",
        payload={"version": 1},
        interval_seconds=300,
        next_run_at=due_at,
    )

    now = datetime.datetime(2026, 1, 1, 9, 17, tzinfo=datetime.UTC)
    await scheduler._process_schedule(schedule, now)

    jobs = await queue.list(job_type="fixture_missed")
    assert len(jobs) == 1
    assert jobs[0].idempotency_key == f"fixture-missed:{due_at.isoformat()}"

    updated = await store.get(schedule.id)
    assert updated is not None
    expected = compute_advanced_next_run_at(
        current_next_run_at=due_at,
        interval_seconds=300,
        now=now,
    )
    assert updated.next_run_at == expected
    assert updated.next_run_at == datetime.datetime(
        2026, 1, 1, 9, 20, tzinfo=datetime.UTC
    )


@pytest.mark.anyio
async def test_scheduler_double_tick_enqueues_one_job(
    db_session,
    scheduler_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    scheduler, queue, store = _make_scheduler(db_session, scheduler_settings)
    due_at = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)
    schedule = await store.insert_schedule(
        name="fixture-concurrent",
        job_type="fixture_concurrent",
        payload={"version": 1},
        interval_seconds=600,
        next_run_at=due_at,
    )
    now = due_at + datetime.timedelta(seconds=1)

    await asyncio.gather(
        scheduler._process_schedule(schedule, now),
        scheduler._process_schedule(schedule, now),
    )

    jobs = await queue.list(job_type="fixture_concurrent")
    assert len(jobs) == 1

    updated = await store.get(schedule.id)
    assert updated is not None
    assert updated.version == schedule.version + 1


@pytest.mark.anyio
async def test_disabled_schedule_never_enqueues(
    db_session,
    scheduler_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    scheduler, queue, store = _make_scheduler(db_session, scheduler_settings)
    due_at = datetime.datetime(2026, 1, 1, 8, 0, tzinfo=datetime.UTC)
    await store.insert_schedule(
        name="fixture-disabled",
        job_type="fixture_disabled",
        payload={"version": 1},
        interval_seconds=300,
        next_run_at=due_at,
        status=ScheduleStatus.DISABLED,
    )

    await scheduler.tick_once()

    jobs = await queue.list(job_type="fixture_disabled")
    assert jobs == []


def test_compute_advanced_next_run_at_skips_missed_intervals() -> None:
    due_at = datetime.datetime(2026, 1, 1, 9, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2026, 1, 1, 9, 17, tzinfo=datetime.UTC)
    advanced = compute_advanced_next_run_at(
        current_next_run_at=due_at,
        interval_seconds=300,
        now=now,
    )
    assert advanced == datetime.datetime(2026, 1, 1, 9, 20, tzinfo=datetime.UTC)
