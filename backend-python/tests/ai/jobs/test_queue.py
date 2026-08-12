"""PostgresJobQueue integration tests (Epic 10 Phase 1)."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.jobs.exceptions import JobConcurrencyError
from app.ai.jobs.models import JobResult, JobStatus
from app.ai.jobs.queue import PostgresJobQueue, generate_worker_id
from app.core.config import Settings
from tests.ai.jobs.conftest import (
    background_jobs_table_available,
    make_queue_session_factory,
)


@pytest.fixture
def job_settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        background_jobs_default_max_attempts=3,
        background_jobs_retry_base_delay_seconds=5.0,
        background_jobs_retry_max_delay_seconds=300.0,
        background_jobs_claim_lease_seconds=300,
    )


@pytest.mark.anyio
async def test_enqueue_and_get(db_session, job_settings: Settings) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)

    job = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1, "value": "a"},
    )
    assert job.status is JobStatus.QUEUED
    assert job.attempt_count == 0
    assert job.version == 1

    fetched = await queue.get(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.payload["value"] == "a"


@pytest.mark.anyio
async def test_idempotent_duplicate_enqueue(db_session, job_settings: Settings) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)

    first = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1},
        idempotency_key="dup-key",
    )
    second = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1, "ignored": True},
        idempotency_key="dup-key",
    )
    assert first.id == second.id
    assert second.payload.get("ignored") is None


@pytest.mark.anyio
async def test_claim_complete_flow(db_session, job_settings: Settings) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)
    worker_id = generate_worker_id()

    job = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1},
    )
    claimed = await queue.claim_due(
        worker_id=worker_id, batch_size=10, lease_seconds=300
    )
    assert len(claimed) == 1
    assert claimed[0].id == job.id
    assert claimed[0].status is JobStatus.RUNNING
    assert claimed[0].attempt_count == 1
    assert claimed[0].locked_by == worker_id

    completed = await queue.complete(
        job.id,
        result=JobResult(summary="done"),
        expected_version=claimed[0].version,
    )
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.result == {"summary": "done", "counts": {}, "ref_id": None}


@pytest.mark.anyio
async def test_fail_with_backoff_requeues(db_session, job_settings: Settings) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)
    worker_id = generate_worker_id()

    await queue.enqueue(job_type="fixture_fail", payload={"version": 1})
    claimed = await queue.claim_due(
        worker_id=worker_id, batch_size=10, lease_seconds=300
    )
    retry_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30)
    failed = await queue.fail(
        claimed[0].id,
        error="TransientError: boom",
        expected_version=claimed[0].version,
        retry_at=retry_at,
    )
    assert failed.status is JobStatus.QUEUED
    assert failed.last_error == "TransientError: boom"
    assert failed.locked_by is None
    assert failed.run_at == retry_at


@pytest.mark.anyio
async def test_fail_dead_letter_after_max_attempts(
    db_session, job_settings: Settings
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)
    worker_id = generate_worker_id()

    await queue.enqueue(
        job_type="fixture_fail",
        payload={"version": 1},
        max_attempts=1,
    )
    claimed = await queue.claim_due(
        worker_id=worker_id, batch_size=10, lease_seconds=300
    )
    dead = await queue.fail(
        claimed[0].id,
        error="PermanentError: nope",
        expected_version=claimed[0].version,
        dead_letter=True,
    )
    assert dead.status is JobStatus.DEAD_LETTER
    assert dead.finished_at is not None


@pytest.mark.anyio
async def test_concurrent_claim_never_double_claims(
    db_session,
    job_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    database_url = Settings().database_url
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = make_queue_session_factory(engine)
    setup_queue = PostgresJobQueue(factory, job_settings)

    for index in range(5):
        await setup_queue.enqueue(
            job_type="fixture_success",
            payload={"version": 1, "index": index},
        )

    async def claim_once(worker_suffix: str) -> list[uuid.UUID]:
        queue = PostgresJobQueue(factory, job_settings)
        claimed = await queue.claim_due(
            worker_id=f"worker-{worker_suffix}",
            batch_size=10,
            lease_seconds=300,
        )
        return [job.id for job in claimed]

    try:
        first_ids, second_ids = await asyncio.gather(
            claim_once("a"),
            claim_once("b"),
        )
    finally:
        await engine.dispose()

    all_claimed = first_ids + second_ids
    assert len(all_claimed) == len(set(all_claimed))
    assert len(all_claimed) >= 1


@pytest.mark.anyio
async def test_lease_expiry_reclaim(db_session, job_settings: Settings) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)
    worker_a = "worker-a:1:00000000-0000-0000-0000-000000000001"
    worker_b = "worker-b:2:00000000-0000-0000-0000-000000000002"

    job = await queue.enqueue(job_type="fixture_success", payload={"version": 1})
    claimed = await queue.claim_due(worker_id=worker_a, batch_size=1, lease_seconds=300)
    assert claimed[0].id == job.id

    await db_session.execute(
        text(
            """
            UPDATE background_jobs
            SET locked_at = now() - make_interval(secs => 400)
            WHERE id = :job_id
            """
        ),
        {"job_id": job.id},
    )
    await db_session.commit()

    reclaimed = await queue.claim_due(
        worker_id=worker_b, batch_size=1, lease_seconds=300
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].id == job.id
    assert reclaimed[0].locked_by == worker_b
    assert reclaimed[0].attempt_count == 2


@pytest.mark.anyio
async def test_complete_stale_version_raises_concurrency_error(
    db_session,
    job_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)
    worker_id = generate_worker_id()

    job = await queue.enqueue(job_type="fixture_success", payload={"version": 1})
    claimed = await queue.claim_due(
        worker_id=worker_id, batch_size=1, lease_seconds=300
    )

    with pytest.raises(JobConcurrencyError):
        await queue.complete(
            job.id,
            result=JobResult(summary="stale"),
            expected_version=claimed[0].version - 1,
        )


@pytest.mark.anyio
async def test_cancel_queued_job_prevents_claim(
    db_session,
    job_settings: Settings,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)

    job = await queue.enqueue(job_type="fixture_success", payload={"version": 1})
    cancelled = await queue.cancel(job_id=job.id, expected_version=job.version)
    assert cancelled is not None
    assert cancelled.status is JobStatus.CANCELLED

    claimed = await queue.claim_due(
        worker_id=generate_worker_id(), batch_size=10, lease_seconds=300
    )
    assert all(row.id != job.id for row in claimed)


@pytest.mark.anyio
async def test_cancel_running_job_is_noop(db_session, job_settings: Settings) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, job_settings)
    worker_id = generate_worker_id()

    job = await queue.enqueue(job_type="fixture_success", payload={"version": 1})
    claimed = await queue.claim_due(
        worker_id=worker_id, batch_size=1, lease_seconds=300
    )
    result = await queue.cancel(
        job_id=claimed[0].id,
        expected_version=claimed[0].version,
    )
    assert result is None

    still_running = await queue.get(job.id)
    assert still_running is not None
    assert still_running.status is JobStatus.RUNNING
