"""Background Jobs adversarial and edge-case scenario tests (Epic 10 Phase 9)."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.deps import get_job_queue
from app.ai.hitl.exceptions import ApprovalExpiredError
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalResult,
    ApprovalStatus,
    ProposedToolCall,
)
from app.ai.hitl.store import AgentToolApprovalStore
from app.ai.jobs.exceptions import JobConcurrencyError
from app.ai.jobs.models import BackgroundJob, JobResult, JobStatus
from app.ai.jobs.queue import PostgresJobQueue, generate_worker_id
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.retry import NonRetryableJobError
from app.ai.jobs.scheduler import JobScheduler
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.ai.jobs.worker import JobWorker
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.db.models import AgentToolApprovalRecord, ChatSession, User
from app.routers import jobs as jobs_router
from tests.ai.jobs.conftest import make_queue_session_factory
from tests.ai.jobs.scenario_helpers import (
    job_settings,
    make_queue_worker,
    require_background_jobs_tables,
    require_schedule_tables,
    success_handler,
)


@pytest.mark.anyio
async def test_retry_exhaustion_manual_retry_then_succeeds(
    db_session,
) -> None:
    await require_background_jobs_tables(db_session)
    settings = job_settings(background_jobs_default_max_attempts=2)
    calls = {"count": 0}

    async def flaky_then_success(job: BackgroundJob) -> JobResult:
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("transient")
        return JobResult(summary="recovered")

    queue, worker, _factory = make_queue_worker(
        db_session,
        settings,
        register_handlers=lambda registry: registry.register(
            "fixture_flaky", flaky_then_success
        ),
    )

    job = await queue.enqueue(
        job_type="fixture_flaky",
        payload={"version": 1},
        max_attempts=2,
        run_at=datetime.datetime.now(datetime.UTC),
    )

    await worker.poll_once()
    mid = await queue.get(job.id)
    assert mid is not None
    assert mid.status is JobStatus.QUEUED

    await db_session.execute(
        text("UPDATE background_jobs SET run_at = now() WHERE id = :id"),
        {"id": job.id},
    )
    await db_session.commit()

    await worker.poll_once()
    dead = await queue.get(job.id)
    assert dead is not None
    assert dead.status is JobStatus.DEAD_LETTER
    assert calls["count"] == 2

    retried = await queue.retry_dead_letter(job.id)
    assert retried is not None
    assert retried.status is JobStatus.QUEUED
    assert retried.attempt_count == 0

    await worker.poll_once()
    final = await queue.get(job.id)
    assert final is not None
    assert final.status is JobStatus.SUCCEEDED
    assert calls["count"] == 3


@pytest.mark.anyio
async def test_worker_crash_mid_job_reclaims_and_completes_once(
    db_session,
) -> None:
    await require_background_jobs_tables(db_session)
    settings = job_settings(background_jobs_claim_lease_seconds=300)
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    side_effects = {"count": 0}

    async def counting_handler(job: BackgroundJob) -> JobResult:
        side_effects["count"] += 1
        return JobResult(summary="done")

    registry = JobHandlerRegistry()
    registry.register("fixture_count", counting_handler)
    worker_b = JobWorker(queue=queue, registry=registry, settings=settings)

    job = await queue.enqueue(job_type="fixture_count", payload={"version": 1})
    claimed = await queue.claim_due(
        worker_id="crashed-worker",
        batch_size=1,
        lease_seconds=300,
    )
    assert len(claimed) == 1

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

    await worker_b.poll_once()
    final = await queue.get(job.id)
    assert final is not None
    assert final.status is JobStatus.SUCCEEDED
    assert side_effects["count"] == 1


@pytest.mark.anyio
async def test_concurrent_claim_completes_each_job_once(
    db_session,
) -> None:
    await require_background_jobs_tables(db_session)
    settings = job_settings()
    database_url = Settings().database_url
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = make_queue_session_factory(engine)
    setup_queue = PostgresJobQueue(factory, settings)
    side_effects = {"count": 0}

    async def counting_handler(job: BackgroundJob) -> JobResult:
        side_effects["count"] += 1
        return JobResult(summary="done")

    for index in range(8):
        await setup_queue.enqueue(
            job_type="fixture_parallel",
            payload={"version": 1, "index": index},
        )

    async def worker_poll(worker_suffix: str) -> None:
        queue = PostgresJobQueue(factory, settings)
        registry = JobHandlerRegistry()
        registry.register("fixture_parallel", counting_handler)
        worker = JobWorker(
            queue=queue,
            registry=registry,
            settings=settings,
            worker_id=f"worker-{worker_suffix}",
        )
        for _ in range(20):
            await worker.poll_once()
            jobs = await queue.list(job_type="fixture_parallel")
            if len(jobs) == 8 and all(
                job.status is JobStatus.SUCCEEDED for job in jobs
            ):
                break

    try:
        await asyncio.gather(
            worker_poll("a"),
            worker_poll("b"),
            worker_poll("c"),
        )
    finally:
        await engine.dispose()

    jobs = await setup_queue.list(job_type="fixture_parallel")
    assert len(jobs) == 8
    assert all(job.status is JobStatus.SUCCEEDED for job in jobs)
    assert side_effects["count"] == 8


@pytest.mark.anyio
async def test_scheduler_double_tick_enqueues_one_job(
    db_session,
) -> None:
    await require_schedule_tables(db_session)
    settings = job_settings()
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    store = PostgresJobScheduleStore(factory)
    scheduler = JobScheduler(queue=queue, store=store, settings=settings)
    due_at = datetime.datetime(2026, 2, 1, 12, 0, tzinfo=datetime.UTC)
    schedule = await store.insert_schedule(
        name=f"adv-concurrent-{uuid.uuid4().hex[:8]}",
        job_type="fixture_scheduler",
        payload={"version": 1},
        interval_seconds=600,
        next_run_at=due_at,
    )
    now = due_at + datetime.timedelta(seconds=1)

    await asyncio.gather(
        scheduler._process_schedule(schedule, now),
        scheduler._process_schedule(schedule, now),
        return_exceptions=True,
    )

    jobs = await queue.list(job_type="fixture_scheduler")
    assert len(jobs) == 1
    updated = await store.get(schedule.id)
    assert updated is not None
    assert updated.version == schedule.version + 1


@pytest.mark.anyio
async def test_expiry_sweep_race_with_decide_only_one_wins(db_session) -> None:
    from sqlalchemy import update

    from app.ai.deps import build_agent_approval_service_for_session

    result = await db_session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result:
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"jobs-race-{uuid.uuid4().hex}",
        email=f"jobs-race-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=1)
    db_session.add(chat_session)
    await db_session.flush()

    store = AgentToolApprovalStore(db_session)
    approval = await store.create(
        session_id=chat_session.id,
        owner_id=user.id,
        execution_id="exec-jobs-race",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "x"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"status": "waiting_approval"},
    )
    await db_session.execute(
        update(AgentToolApprovalRecord)
        .where(AgentToolApprovalRecord.id == approval.id)
        .values(
            requested_at=datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(hours=2)
        )
    )
    await db_session.commit()

    settings = Settings(
        openai_api_key="test-key",
        hitl_enabled=True,
        hitl_client_audit_retention_days=90,
    )
    factory = make_queue_session_factory(db_session.bind)
    barrier = asyncio.Barrier(2)

    async def decide_task() -> ApprovalResult | ApprovalExpiredError:
        async with factory() as session:
            await barrier.wait()
            service = build_agent_approval_service_for_session(session, settings)
            try:
                result = await service.decide(
                    approval.id,
                    decider_id=user.id,
                    decision="rejected",
                )
            except ApprovalExpiredError:
                await session.rollback()
                raise
            await session.commit()
            return result

    async def expire_task():
        async with factory() as session:
            await barrier.wait()
            approval_store = AgentToolApprovalStore(
                session,
                client_audit_retention_days=settings.hitl_client_audit_retention_days,
            )
            expired = await approval_store.cas_expire_pending_sweep(approval.id)
            await session.commit()
            return expired

    results = await asyncio.gather(
        decide_task(),
        expire_task(),
        return_exceptions=True,
    )
    for item in results:
        if isinstance(item, Exception) and not isinstance(item, ApprovalExpiredError):
            raise item

    transitioned = sum(
        1
        for item in results
        if isinstance(item, ApprovalResult)
        or (
            isinstance(item, AgentToolApproval)
            and item.status is ApprovalStatus.EXPIRED
        )
    )
    assert transitioned == 1


@pytest.mark.anyio
async def test_orphan_sweep_grace_period_leaves_row_untouched(db_session) -> None:
    from app.ai.deps import (
        build_agent_approval_service_for_session,
        build_hitl_resume_executor,
    )
    from app.ai.jobs.handlers.hitl_orphan_sweep import hitl_orphaned_snapshot_sweep
    from tests.ai.jobs.handlers.test_hitl_orphan_sweep import _sample_job

    result = await db_session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    if not result:
        pytest.skip("agent_tool_approvals not available — run alembic upgrade head")

    user = User(
        auth_provider="google",
        external_auth_id=f"grace-{uuid.uuid4().hex}",
        email=f"grace-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=1)
    db_session.add(chat_session)
    await db_session.flush()

    store = AgentToolApprovalStore(db_session)
    approval = await store.create(
        session_id=chat_session.id,
        owner_id=user.id,
        execution_id="exec-grace",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "keep"}],
        paused_state={"execution_id": "exec-grace", "status": "waiting_approval"},
    )
    await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.APPROVED,
        decided_by=user.id,
    )
    await db_session.commit()

    settings = job_settings(hitl_orphan_sweep_grace_seconds=3600)
    factory = make_queue_session_factory(db_session.bind)
    sweep_result = await hitl_orphaned_snapshot_sweep(
        _sample_job(),
        settings=settings,
        session_factory=factory,
        build_approval_service=lambda session: build_agent_approval_service_for_session(
            session, settings
        ),
        build_resume_executor=lambda _session, _service: build_hitl_resume_executor(
            settings
        ),
    )
    assert sweep_result.counts["resumed"] == 0

    async with factory() as session:
        refreshed = await AgentToolApprovalStore(session).get(approval.id)
    assert refreshed is not None
    assert refreshed.paused_scratchpad != []


@pytest.mark.anyio
async def test_handler_db_failure_retries_then_succeeds(db_session) -> None:
    await require_background_jobs_tables(db_session)
    settings = job_settings(background_jobs_default_max_attempts=3)
    calls = {"count": 0}

    async def flaky_db_handler(job: BackgroundJob) -> JobResult:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("connection dropped")
        return JobResult(summary="ok")

    queue, worker, _factory = make_queue_worker(
        db_session,
        settings,
        register_handlers=lambda registry: registry.register(
            "fixture_db_flaky", flaky_db_handler
        ),
    )
    job = await queue.enqueue(
        job_type="fixture_db_flaky",
        payload={"version": 1},
        run_at=datetime.datetime.now(datetime.UTC),
    )

    await worker.poll_once()
    mid = await queue.get(job.id)
    assert mid is not None
    assert mid.status is JobStatus.QUEUED

    await db_session.execute(
        text("UPDATE background_jobs SET run_at = now() WHERE id = :id"),
        {"id": job.id},
    )
    await db_session.commit()

    await worker.poll_once()
    final = await queue.get(job.id)
    assert final is not None
    assert final.status is JobStatus.SUCCEEDED
    assert calls["count"] == 2


@pytest.mark.anyio
async def test_scheduler_crash_idempotency_prevents_duplicate_enqueue(
    db_session,
) -> None:
    await require_schedule_tables(db_session)
    settings = job_settings()
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    store = PostgresJobScheduleStore(factory)
    due_at = datetime.datetime(2026, 2, 2, 8, 0, tzinfo=datetime.UTC)
    schedule = await store.insert_schedule(
        name=f"crash-recover-{uuid.uuid4().hex[:8]}",
        job_type="fixture_crash",
        payload={"version": 1},
        interval_seconds=300,
        next_run_at=due_at,
    )
    idempotency_key = f"{schedule.name}:{due_at.isoformat()}"

    await queue.enqueue(
        job_type="fixture_crash",
        payload={"version": 1},
        idempotency_key=idempotency_key,
        schedule_id=schedule.id,
    )

    scheduler = JobScheduler(queue=queue, store=store, settings=settings)
    await scheduler._process_schedule(schedule, due_at + datetime.timedelta(seconds=1))

    jobs = await queue.list(job_type="fixture_crash")
    assert len(jobs) == 1


@pytest.mark.anyio
async def test_lease_expiry_while_handler_running_reclaims_idempotently(
    db_session,
) -> None:
    await require_background_jobs_tables(db_session)
    settings = job_settings(
        background_jobs_claim_lease_seconds=300,
        background_jobs_default_max_attempts=3,
    )
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    side_effects = {"count": 0}

    async def idempotent_handler(job: BackgroundJob) -> JobResult:
        del job
        side_effects["count"] += 1
        return JobResult(summary="indexed-once")

    registry = JobHandlerRegistry()
    registry.register("fixture_slow_idempotent", idempotent_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=settings)

    job = await queue.enqueue(
        job_type="fixture_slow_idempotent",
        payload={"version": 1},
        max_attempts=3,
        run_at=datetime.datetime.now(datetime.UTC),
    )
    claimed = await queue.claim_due(
        worker_id="slow-worker",
        batch_size=1,
        lease_seconds=300,
    )
    assert len(claimed) == 1
    assert claimed[0].attempt_count == 1

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

    await worker.poll_once()
    final = await queue.get(job.id)
    assert final is not None
    assert final.status is JobStatus.SUCCEEDED
    assert final.attempt_count == 2
    assert side_effects["count"] == 1


@pytest.mark.anyio
async def test_complete_and_retry_stale_version_only_one_succeeds(
    db_session,
) -> None:
    await require_background_jobs_tables(db_session)
    settings = job_settings()
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    worker_id = generate_worker_id()

    job = await queue.enqueue(job_type="fixture_success", payload={"version": 1})
    claimed = await queue.claim_due(
        worker_id=worker_id, batch_size=1, lease_seconds=300
    )
    await queue.fail(
        claimed[0].id,
        error="boom",
        expected_version=claimed[0].version,
        dead_letter=True,
    )

    retried, completed = await asyncio.gather(
        queue.retry_dead_letter(job.id),
        queue.complete(
            job.id,
            result=JobResult(summary="stale"),
            expected_version=claimed[0].version,
        ),
        return_exceptions=True,
    )

    assert isinstance(retried, BackgroundJob)
    assert isinstance(completed, JobConcurrencyError)


@pytest.mark.anyio
async def test_duplicate_retry_execution_is_idempotent(db_session) -> None:
    await require_background_jobs_tables(db_session)
    job_type = f"fixture_idempotent_{uuid.uuid4().hex[:8]}"
    settings = job_settings(
        background_jobs_default_max_attempts=1,
        background_jobs_worker_batch_size=1,
    )
    side_effects = {"count": 0}
    applied = {"done": False}

    async def idempotent_handler(job: BackgroundJob) -> JobResult:
        del job
        if not applied["done"]:
            applied["done"] = True
            side_effects["count"] += 1
        return JobResult(summary="already done")

    queue, worker, _factory = make_queue_worker(
        db_session,
        settings,
        register_handlers=lambda registry: registry.register(
            job_type, idempotent_handler
        ),
    )

    job = await queue.enqueue(
        job_type=job_type,
        payload={"version": 1},
        max_attempts=1,
    )
    await worker.poll_once()
    first = await queue.get(job.id)
    assert first is not None
    assert first.status is JobStatus.SUCCEEDED
    assert side_effects["count"] == 1

    await db_session.execute(
        text(
            """
            UPDATE background_jobs
            SET status = 'dead_letter', finished_at = now(), last_error = 'reset'
            WHERE id = :job_id
            """
        ),
        {"job_id": job.id},
    )
    await db_session.commit()
    await queue.retry_dead_letter(job.id)
    await worker.poll_once()

    final = await queue.get(job.id)
    assert final is not None
    assert final.status is JobStatus.SUCCEEDED
    assert side_effects["count"] == 1


@pytest.mark.anyio
async def test_rest_retry_after_exhaustion_completes(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await require_background_jobs_tables(db_session)
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "true")
    get_settings.cache_clear()
    settings = job_settings(
        background_jobs_enabled=True,
        background_jobs_default_max_attempts=1,
    )
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    registry = JobHandlerRegistry()

    async def poison_handler(job: BackgroundJob) -> JobResult:
        del job
        raise NonRetryableJobError("first pass poison")

    registry.register("fixture_rest", poison_handler)
    worker = JobWorker(queue=queue, registry=registry, settings=settings)

    job = await queue.enqueue(job_type="fixture_rest", payload={"version": 1})
    await worker.poll_once()
    dead = await queue.get(job.id)
    assert dead is not None
    assert dead.status is JobStatus.DEAD_LETTER

    registry.register("fixture_rest", success_handler)

    user = await SqlUserStore(db_session).create(
        sub=f"jobs-retry-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    token = create_access_token(user_id=user.id, settings=settings)
    test_app = FastAPI()
    test_app.include_router(jobs_router.router)
    register_exception_handlers(test_app)
    test_app.dependency_overrides[get_job_queue] = lambda: queue

    async def _override_db_session():
        yield db_session

    from app.db.session import get_db_session

    test_app.dependency_overrides[get_db_session] = _override_db_session

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            retry_response = await client.post(
                f"/api/jobs/{job.id}/retry",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert retry_response.status_code == 200

        await worker.poll_once()
        final = await queue.get(job.id)
        assert final is not None
        assert final.status is JobStatus.SUCCEEDED
    finally:
        test_app.dependency_overrides.pop(get_job_queue, None)
        test_app.dependency_overrides.pop(get_db_session, None)
        get_settings.cache_clear()
