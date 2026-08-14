"""Audit log retention cleanup handler (Epic 11 Phase 3)."""

from __future__ import annotations

import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.jobs.models import BackgroundJob, JobResult, ScheduleStatus
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.core.config import Settings
from app.core.logging import get_logger

SECURITY_AUDIT_RETENTION_SCHEDULE_NAME = "security-audit-retention-cleanup"
_RECONCILE_MAX_ATTEMPTS = 5

_logger = get_logger(__name__)


async def _delete_audit_events_batch(
    session: AsyncSession,
    *,
    cutoff: datetime.datetime,
    batch_size: int,
) -> int:
    result = await session.execute(
        text(
            """
            DELETE FROM audit_events
            WHERE id IN (
                SELECT id FROM audit_events
                WHERE occurred_at < :cutoff
                LIMIT :batch_size
            )
            """
        ),
        {"cutoff": cutoff, "batch_size": batch_size},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def security_audit_retention_cleanup(
    job: BackgroundJob,
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> JobResult:
    """Batch-delete ``audit_events`` rows past ``security_audit_retention_days``."""
    if not (
        settings.security_governance_enabled and settings.security_audit_log_enabled
    ):
        return JobResult(
            summary="security audit log disabled",
            counts={"audit_events_deleted": 0},
        )

    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=settings.security_audit_retention_days
    )
    batch_size = settings.security_audit_retention_cleanup_batch_size

    audit_events_deleted = 0
    async with session_factory() as session:
        while True:
            deleted = await _delete_audit_events_batch(
                session, cutoff=cutoff, batch_size=batch_size
            )
            audit_events_deleted += deleted
            await session.commit()
            if deleted == 0:
                break

    return JobResult(
        summary=f"deleted {audit_events_deleted} audit_events rows",
        counts={"audit_events_deleted": audit_events_deleted},
    )


async def reconcile_security_audit_retention_schedule_status(
    store: PostgresJobScheduleStore,
    settings: Settings,
) -> None:
    """Align the seeded retention-cleanup schedule row with the Security &
    Governance master flag (mirrors ``reconcile_evaluation_schedule_status``)."""
    desired = (
        ScheduleStatus.ENABLED
        if (
            settings.security_governance_enabled and settings.security_audit_log_enabled
        )
        else ScheduleStatus.DISABLED
    )

    for _ in range(_RECONCILE_MAX_ATTEMPTS):
        schedule = await store.get_by_name(SECURITY_AUDIT_RETENTION_SCHEDULE_NAME)
        if schedule is None:
            return
        if schedule.status == desired:
            return

        updated = await store.set_status(
            schedule.id,
            expected_version=schedule.version,
            status=desired,
        )
        if updated is not None:
            return

    _logger.warning(
        "Failed to reconcile security audit retention schedule status after optimistic retries",
        schedule_name=SECURITY_AUDIT_RETENTION_SCHEDULE_NAME,
        desired_status=desired.value,
        max_attempts=_RECONCILE_MAX_ATTEMPTS,
    )
