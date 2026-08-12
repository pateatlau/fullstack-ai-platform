"""JobWorker end-to-end tests (Epic 10 Phase 1)."""

from __future__ import annotations

import asyncio
import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.jobs.models import BackgroundJob, JobResult, JobStatus
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.retry import NonRetryableJobError
from app.ai.jobs.worker import JobWorker
from app.core.config import Settings
from tests.ai.jobs.conftest import (
    background_jobs_table_available,
    make_queue_session_factory,
)


@pytest.fixture
def worker_settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        background_jobs_default_max_attempts=3,
        background_jobs_worker_batch_size=10,
        background_jobs_claim_lease_seconds=300,
        background_jobs_handler_timeout_seconds=1,
        background_jobs_retry_base_delay_seconds=0.0,
        background_jobs_retry_max_delay_seconds=0.0,
        background_jobs_worker_poll_interval_seconds=60,
    )


@pytest.mark.anyio
async def test_worker_success_completes_job(
    db_session,
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, worker_settings)
    registry = JobHandlerRegistry()

    async def success_handler(job: BackgroundJob) -> JobResult:
        return JobResult(summary=f"handled {job.job_type}")

    registry.register("fixture_success", success_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=worker_settings)

    job = await queue.enqueue(job_type="fixture_success", payload={"version": 1})
    await worker.poll_once()

    updated = await queue.get(job.id)
    assert updated is not None
    assert updated.status is JobStatus.SUCCEEDED
    assert updated.result is not None
    assert updated.result["summary"] == "handled fixture_success"


@pytest.mark.anyio
async def test_worker_transient_failure_requeues_then_dead_letters(
    db_session,
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    settings = worker_settings.model_copy(
        update={"background_jobs_default_max_attempts": 2}
    )
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    registry = JobHandlerRegistry()
    calls = {"count": 0}

    async def flaky_handler(job: BackgroundJob) -> JobResult:
        del job
        calls["count"] += 1
        raise RuntimeError("transient")

    registry.register("fixture_fail", flaky_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=settings)

    job = await queue.enqueue(
        job_type="fixture_fail",
        payload={"version": 1},
        max_attempts=2,
        run_at=datetime.datetime.now(datetime.UTC),
    )

    await worker.poll_once()
    mid = await queue.get(job.id)
    assert mid is not None
    assert mid.status is JobStatus.QUEUED
    assert mid.attempt_count == 1

    await db_session.execute(
        text("UPDATE background_jobs SET run_at = now() WHERE id = :id"),
        {"id": job.id},
    )
    await db_session.commit()

    await worker.poll_once()
    final = await queue.get(job.id)
    assert final is not None
    assert final.status is JobStatus.DEAD_LETTER
    assert calls["count"] == 2


@pytest.mark.anyio
async def test_worker_non_retryable_dead_letters_immediately(
    db_session,
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, worker_settings)
    registry = JobHandlerRegistry()

    async def poison_handler(job: BackgroundJob) -> JobResult:
        del job
        raise NonRetryableJobError("invalid payload")

    registry.register("fixture_poison", poison_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=worker_settings)

    job = await queue.enqueue(job_type="fixture_poison", payload={"version": 1})
    await worker.poll_once()

    updated = await queue.get(job.id)
    assert updated is not None
    assert updated.status is JobStatus.DEAD_LETTER
    assert "invalid payload" in (updated.last_error or "")


@pytest.mark.anyio
async def test_worker_missing_handler_dead_letters(
    db_session,
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, worker_settings)
    registry = JobHandlerRegistry()
    worker = JobWorker(queue=queue, registry=registry, settings=worker_settings)

    job = await queue.enqueue(job_type="missing_handler", payload={"version": 1})
    await worker.poll_once()

    updated = await queue.get(job.id)
    assert updated is not None
    assert updated.status is JobStatus.DEAD_LETTER


@pytest.mark.anyio
async def test_worker_handler_timeout_retries_or_dead_letters(
    db_session,
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    settings = worker_settings.model_copy(
        update={
            "background_jobs_handler_timeout_seconds": 0,
            "background_jobs_default_max_attempts": 1,
        }
    )
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    registry = JobHandlerRegistry()

    async def slow_handler(job: BackgroundJob) -> JobResult:
        del job
        await asyncio.sleep(0.05)
        return JobResult(summary="late")

    registry.register("fixture_slow", slow_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=settings)

    job = await queue.enqueue(
        job_type="fixture_slow",
        payload={"version": 1},
        max_attempts=1,
    )
    await worker.poll_once()

    updated = await queue.get(job.id)
    assert updated is not None
    assert updated.status is JobStatus.DEAD_LETTER
    assert updated.last_error is not None
    assert "TimeoutError" in updated.last_error


@pytest.mark.anyio
async def test_claim_transaction_commits_before_handler_runs(
    db_session,
    worker_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    database_url = Settings().database_url
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = make_queue_session_factory(engine)
    queue = PostgresJobQueue(factory, worker_settings)
    registry = JobHandlerRegistry()
    observed: dict[str, object] = {}

    async def boundary_handler(job: BackgroundJob) -> JobResult:
        other_queue = PostgresJobQueue(factory, worker_settings)
        visible = await other_queue.get(job.id)
        observed["status"] = None if visible is None else visible.status.value
        observed["locked_by"] = None if visible is None else visible.locked_by
        return JobResult(summary="ok")

    registry.register("fixture_boundary", boundary_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=worker_settings)

    job = await queue.enqueue(
        job_type="fixture_boundary",
        payload={"version": 1},
    )
    await worker.poll_once()

    assert observed["status"] == JobStatus.RUNNING.value
    assert observed["locked_by"] == worker.worker_id

    final = await queue.get(job.id)
    assert final is not None
    assert final.status is JobStatus.SUCCEEDED

    await engine.dispose()
