"""Security audit log retention cleanup handler tests (Epic 11 Phase 3)."""

from __future__ import annotations

import datetime
import uuid
from typing import AsyncIterator

import pytest
from sqlalchemy import text

from app.ai.jobs.handlers.security_audit_retention import (
    SECURITY_AUDIT_RETENTION_SCHEDULE_NAME,
    reconcile_security_audit_retention_schedule_status,
    security_audit_retention_cleanup,
)
from app.ai.jobs.models import BackgroundJob, JobStatus, ScheduleStatus
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.core.config import Settings
from tests.ai.jobs.conftest import (
    background_job_schedules_table_available,
    make_queue_session_factory,
)


async def _audit_events_table_available(db_session) -> bool:
    result = await db_session.scalar(
        text("SELECT to_regclass('public.audit_events') IS NOT NULL")
    )
    return bool(result)


@pytest.fixture(autouse=True)
async def _truncate_audit_events(db_session) -> AsyncIterator[None]:
    if not await _audit_events_table_available(db_session):
        return
    await db_session.execute(text("TRUNCATE audit_events"))
    await db_session.commit()
    yield
    await db_session.execute(text("TRUNCATE audit_events"))
    await db_session.commit()


def _sample_job(*, job_id: uuid.UUID | None = None) -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=job_id or uuid.uuid4(),
        job_type="security_audit_retention_cleanup",
        status=JobStatus.RUNNING,
        payload={"version": 1},
        attempt_count=1,
        max_attempts=3,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
        locked_by="test-worker",
        locked_at=now,
        started_at=now,
    )


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "security_governance_enabled": True,
        "security_audit_log_enabled": True,
        "security_audit_retention_days": 30,
        "security_audit_retention_cleanup_batch_size": 2,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _insert_audit_event(
    db_session, *, occurred_at: datetime.datetime
) -> uuid.UUID:
    event_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO audit_events (
                id, occurred_at, actor_kind, action, outcome, metadata
            ) VALUES (
                :id, :occurred_at, 'system', 'login.succeeded', 'success', '{}'::jsonb
            )
            """
        ),
        {"id": event_id, "occurred_at": occurred_at},
    )
    await db_session.commit()
    return event_id


@pytest.mark.anyio
async def test_disabled_when_security_governance_flag_off(db_session) -> None:
    if not await _audit_events_table_available(db_session):
        pytest.skip("audit_events table not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    result = await security_audit_retention_cleanup(
        _sample_job(),
        settings=_settings(security_governance_enabled=False),
        session_factory=factory,
    )

    assert result.counts["audit_events_deleted"] == 0


@pytest.mark.anyio
async def test_deletes_only_rows_older_than_retention_window(db_session) -> None:
    if not await _audit_events_table_available(db_session):
        pytest.skip("audit_events table not available — run alembic upgrade head")

    now = datetime.datetime.now(datetime.UTC)
    stale_at = now - datetime.timedelta(days=60)
    recent_at = now - datetime.timedelta(days=1)
    await _insert_audit_event(db_session, occurred_at=stale_at)
    recent_id = await _insert_audit_event(db_session, occurred_at=recent_at)

    factory = make_queue_session_factory(db_session.bind)
    result = await security_audit_retention_cleanup(
        _sample_job(), settings=_settings(), session_factory=factory
    )

    assert result.counts["audit_events_deleted"] == 1
    async with factory() as session:
        remaining = (
            (await session.execute(text("SELECT id FROM audit_events"))).scalars().all()
        )
    assert list(remaining) == [recent_id]


@pytest.mark.anyio
async def test_batching_deletes_more_than_one_batch_in_single_invocation(
    db_session,
) -> None:
    if not await _audit_events_table_available(db_session):
        pytest.skip("audit_events table not available — run alembic upgrade head")

    stale_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=60)
    for _ in range(5):
        await _insert_audit_event(db_session, occurred_at=stale_at)

    factory = make_queue_session_factory(db_session.bind)
    result = await security_audit_retention_cleanup(
        _sample_job(),
        settings=_settings(security_audit_retention_cleanup_batch_size=2),
        session_factory=factory,
    )

    assert result.counts["audit_events_deleted"] == 5
    async with factory() as session:
        remaining = await session.scalar(text("SELECT count(*) FROM audit_events"))
    assert remaining == 0


@pytest.mark.anyio
async def test_reconcile_enables_schedule_when_flag_on(db_session) -> None:
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules table not available")

    factory = make_queue_session_factory(db_session.bind)
    store = PostgresJobScheduleStore(factory)
    due_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)
    await store.insert_schedule(
        name=SECURITY_AUDIT_RETENTION_SCHEDULE_NAME,
        job_type="security_audit_retention_cleanup",
        payload={"version": 1},
        interval_seconds=86400,
        next_run_at=due_at,
        status=ScheduleStatus.DISABLED,
    )

    await reconcile_security_audit_retention_schedule_status(
        store,
        _settings(security_governance_enabled=True, security_audit_log_enabled=True),
    )

    reconciled = await store.get_by_name(SECURITY_AUDIT_RETENTION_SCHEDULE_NAME)
    assert reconciled is not None
    assert reconciled.status is ScheduleStatus.ENABLED
