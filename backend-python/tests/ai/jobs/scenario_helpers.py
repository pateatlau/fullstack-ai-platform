"""Shared helpers for Background Jobs reference and adversarial tests."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.jobs.models import BackgroundJob, JobResult, JobStatus
from app.ai.jobs.queue import PostgresJobQueue, generate_worker_id
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.worker import JobWorker
from app.core.config import Settings
from tests.ai.jobs.conftest import (
    background_job_schedules_table_available,
    background_jobs_table_available,
    make_queue_session_factory,
)


def job_settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "background_jobs_enabled": True,
        "background_jobs_default_max_attempts": 3,
        "background_jobs_worker_batch_size": 10,
        "background_jobs_claim_lease_seconds": 300,
        "background_jobs_handler_timeout_seconds": 5,
        "background_jobs_retry_base_delay_seconds": 0.0,
        "background_jobs_retry_max_delay_seconds": 0.0,
        "background_jobs_worker_poll_interval_seconds": 60,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def require_background_jobs_tables(db_session: AsyncSession) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")


async def require_schedule_tables(db_session: AsyncSession) -> None:
    await require_background_jobs_tables(db_session)
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")


def make_queue_worker(
    db_session: AsyncSession,
    settings: Settings,
    *,
    register_handlers: Callable[[JobHandlerRegistry], None] | None = None,
) -> tuple[PostgresJobQueue, JobWorker, async_sessionmaker[AsyncSession]]:
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    registry = JobHandlerRegistry()
    if register_handlers is not None:
        register_handlers(registry)
    worker = JobWorker(
        queue=queue,
        registry=registry,
        settings=settings,
        worker_id=generate_worker_id(),
    )
    return queue, worker, factory


def sample_background_job(
    *,
    job_type: str = "fixture_success",
    payload: dict[str, object] | None = None,
) -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=uuid.uuid4(),
        job_type=job_type,
        status=JobStatus.QUEUED,
        payload=payload or {"version": 1},
        attempt_count=0,
        max_attempts=3,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
    )


async def success_handler(job: BackgroundJob) -> JobResult:
    return JobResult(summary=f"handled {job.job_type}")
